import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_impact_provider, get_retriever
from src.reasoning.impact import CodeCallGraph
from src.reasoning.llm import FakeLLM
from src.reasoning.retrieval.embeddings import HashEmbedder
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.retrieval.vector_store import InMemoryVectorStore
from tests.test_api_impact import FIX, IMPACT, ISSUE, RCA, REPO

SCENARIO = json.dumps({"title": "Refund of a missing order", "setup": ["No order exists"],
                       "action": "Check refund eligibility.", "expected": "Not eligible.",
                       "expected_error_code": None})


@pytest.fixture
def analyze(monkeypatch):
    def _run(*responses):
        retriever = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
        impact = CodeCallGraph()
        app.dependency_overrides[get_retriever] = lambda: retriever
        app.dependency_overrides[get_impact_provider] = lambda: impact
        llm = FakeLLM(responses=list(responses))
        monkeypatch.setattr("src.api.main.get_llm", lambda: llm)
        client = TestClient(app)
        client.post("/api/causix/index", json={"repo_path": str(REPO)})
        job = client.post("/api/causix/analyze", json={"issue": "refund eligible"}).json()["job_id"]
        return client.get(f"/api/causix/result/{job}").json()
    yield _run
    app.dependency_overrides.clear()


def test_full_pipeline_generates_scenario_not_executed(analyze):
    sc = analyze(ISSUE, RCA, FIX, IMPACT, SCENARIO)["scenario"]
    assert sc["status"] == "generated" and sc["action"] == "Check refund eligibility."
    assert sc["related_location"] == "billing/refunds.py::is_eligible"
    assert sc["execution"]["status"] == "not_run"   # sandbox not connected: no fake PASS


def test_scenario_skipped_without_fix(analyze):
    weak = json.dumps(json.loads(RCA) | {"insufficient_evidence": True})
    assert analyze(ISSUE, weak)["scenario"]["status"] == "skipped"


def test_scenario_failure_keeps_earlier_results(analyze):
    result = analyze(ISSUE, RCA, FIX, IMPACT, "not json")
    assert result["scenario"]["status"] == "skipped"
    assert result["fix"]["status"] == "recommended" and result["impact"]["status"] == "explained"
    assert any("Scenario generation failed" in n for n in result["notes"])
