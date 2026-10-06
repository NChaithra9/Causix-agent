import json

from fastapi.testclient import TestClient

from src.api.main import app, get_impact_provider, get_retriever
from src.reasoning.impact import CodeCallGraph
from src.reasoning.llm import FakeLLM
from src.reasoning.retrieval.embeddings import HashEmbedder
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.retrieval.vector_store import InMemoryVectorStore
from tests.test_api_impact import FIX, IMPACT, ISSUE, RCA, REPO
from tests.test_api_scenario import SCENARIO

DOCS = json.dumps({"title": "Refund bug", "summary": "Fixed eligibility.", "lessons": ["Test None"]})


def run(monkeypatch, *responses):
    retriever = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
    impact = CodeCallGraph()
    app.dependency_overrides[get_retriever] = lambda: retriever
    app.dependency_overrides[get_impact_provider] = lambda: impact
    llm = FakeLLM(responses=list(responses))
    monkeypatch.setattr("src.api.main.get_llm", lambda: llm)
    client = TestClient(app)
    client.post("/api/causix/index", json={"repo_path": str(REPO)})
    job = client.post("/api/causix/analyze", json={"issue": "refund eligible"}).json()["job_id"]
    return client, job


def test_report_endpoint_returns_markdown(monkeypatch):
    try:
        client, job = run(monkeypatch, ISSUE, RCA, FIX, IMPACT, SCENARIO, DOCS)
        res = client.get(f"/api/causix/result/{job}").json()
        assert res["documentation"]["status"] == "generated"
        r = client.get(f"/api/causix/report/{job}")
        assert r.status_code == 200 and r.text.startswith("# Refund bug")
        assert "Execution: **not_run**" in r.text
    finally:
        app.dependency_overrides.clear()


def test_report_404_without_grounded_rca(monkeypatch):
    try:
        weak = json.dumps(json.loads(RCA) | {"insufficient_evidence": True})
        client, job = run(monkeypatch, ISSUE, weak)
        assert client.get(f"/api/causix/report/{job}").status_code == 404
        assert client.get("/api/causix/report/nope").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_docs_failure_keeps_everything_else(monkeypatch):
    try:
        client, job = run(monkeypatch, ISSUE, RCA, FIX, IMPACT, SCENARIO, "not json")
        res = client.get(f"/api/causix/result/{job}").json()
        assert res["documentation"]["status"] == "skipped" and res["scenario"]["status"] == "generated"
        assert any("Documentation failed" in n for n in res["notes"])
    finally:
        app.dependency_overrides.clear()
