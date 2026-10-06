import json

from fastapi.testclient import TestClient

from src.api.main import app, get_engines, get_impact_provider, get_retriever
from src.reasoning.execution import StubScenarioRunner
from src.reasoning.facts import StubFactsProvider
from src.reasoning.impact import CodeCallGraph
from src.reasoning.integration.architecture import GitArchitectureProvider
from src.reasoning.integration.engines import Engines
from src.reasoning.llm import FakeLLM
from src.reasoning.retrieval.embeddings import HashEmbedder
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.retrieval.vector_store import InMemoryVectorStore
from tests.test_api_documentation import DOCS
from tests.test_api_impact import FIX, IMPACT, ISSUE, RCA, REPO
from tests.test_api_scenario import SCENARIO
from tests.test_integration_architecture import change, result


class FakeConn:
    pass


def engines(mode="stub", detect=None):
    arch = GitArchitectureProvider(lambda: str(REPO), detect or (lambda p, a, b: result(changes=[change()])))
    e = Engines(mode, StubFactsProvider(), None, StubScenarioRunner(), arch,
                connection=FakeConn() if mode == "real" else None)
    e.ingested = []
    if mode == "real":
        e.ingest = lambda path: e.ingested.append(path) or True
    return e


def client(monkeypatch, eng, *responses):
    retriever = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
    app.dependency_overrides[get_retriever] = lambda: retriever
    app.dependency_overrides[get_engines] = lambda: eng
    graph = CodeCallGraph()
    app.dependency_overrides[get_impact_provider] = lambda: graph
    monkeypatch.setattr("src.api.main.get_llm", lambda: FakeLLM(responses=list(responses)))
    return TestClient(app)


def test_index_ingests_into_graph_only_with_real_engines(monkeypatch):
    try:
        eng = engines("real")
        c = client(monkeypatch, eng)
        r = c.post("/api/causix/index", json={"repo_path": str(REPO)}).json()
        assert r["graph_ingested"] is True and eng.ingested == [str(REPO)]
        stub = client(monkeypatch, engines("stub"))
        assert stub.post("/api/causix/index", json={"repo_path": str(REPO)}).json()["graph_ingested"] is None
    finally:
        app.dependency_overrides.clear()


def test_architecture_changes_in_result_and_report(monkeypatch):
    try:
        c = client(monkeypatch, engines(), ISSUE, RCA, FIX, IMPACT, SCENARIO, DOCS)
        c.post("/api/causix/index", json={"repo_path": str(REPO)})
        job = c.post("/api/causix/analyze", json={"issue": "refund eligible", "previous_revision": "a",
                                                  "current_revision": "b"}).json()["job_id"]
        res = c.get(f"/api/causix/result/{job}").json()
        assert res["architecture"]["status"] == "CHANGES_DETECTED"
        assert res["architecture"]["changes"][0]["name"] == "GET /refunds"
        md = c.get(f"/api/causix/report/{job}").text
        assert "## Architecture changes" in md and "API_ADDED" in md and "rootfix-architecture.json" in md
    finally:
        app.dependency_overrides.clear()


def test_architecture_skipped_without_revisions_and_engine_failure_is_a_note(monkeypatch):
    def boom(p, a, b):
        raise RuntimeError("git exploded")
    try:
        c = client(monkeypatch, engines(detect=boom), ISSUE, RCA, FIX, IMPACT, SCENARIO, DOCS)
        c.post("/api/causix/index", json={"repo_path": str(REPO)})
        job = c.post("/api/causix/analyze", json={"issue": "refund eligible"}).json()["job_id"]
        assert c.get(f"/api/causix/result/{job}").json()["architecture"] is None
        c2 = client(monkeypatch, engines(detect=boom), ISSUE, RCA, FIX, IMPACT, SCENARIO, DOCS)
        job = c2.post("/api/causix/analyze", json={"issue": "x", "previous_revision": "a",
                                                   "current_revision": "b"}).json()["job_id"]
        res = c2.get(f"/api/causix/result/{job}").json()
        assert res["architecture"] is None and any("git exploded" in n for n in res["notes"])
    finally:
        app.dependency_overrides.clear()
