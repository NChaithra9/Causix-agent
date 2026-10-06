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

REPO = Path(__file__).parent / "fixtures" / "impact_repo"
LOC = "billing/refunds.py::is_eligible"

ISSUE = json.dumps({"summary": "Refund eligibility is wrong", "error_type": None,
                    "suspected_component": "is_eligible", "keywords": ["eligible", "refund"]})
RCA = json.dumps({"root_cause": "Eligibility ignores missing orders.", "confidence": "high",
                  "affected_component": "billing", "location": LOC, "evidence_ids": ["E1"],
                  "reasoning": "E1", "suggested_fix": "Check order first.",
                  "insufficient_evidence": False})
FIX = json.dumps({"summary": "Return False for missing orders.", "explanation": "...",
                  "code_after": "def is_eligible(order):\n    if order is None:\n        return False\n"
                                "    return order.amount > 0\n",
                  "test_name": "test_none", "test_code": "def test_none():\n    assert True\n",
                  "risks": []})
IMPACT = json.dumps({"summary": "Refund processing and the refund endpoint are affected.",
                     "risk_level": "medium", "reasons": {"N1": "Calls is_eligible."}})


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


def test_full_pipeline_explains_impact(analyze):
    impact = analyze(ISSUE, RCA, FIX, IMPACT)["impact"]
    assert impact["status"] == "explained" and impact["changed_location"] == LOC
    assert "process_refund" in [i["name"] for i in impact["directly_affected"]]
    assert "refund_endpoint" in [i["name"] for i in impact["potentially_affected"]]
    assert "test_is_eligible_positive_amount" in [i["name"] for i in impact["tests_to_run"]]


def test_impact_skipped_without_fix(analyze):
    weak_rca = json.loads(RCA) | {"insufficient_evidence": True}
    impact = analyze(ISSUE, json.dumps(weak_rca))["impact"]
    assert impact["status"] == "skipped"


def test_impact_failure_keeps_fix(analyze):
    result = analyze(ISSUE, RCA, FIX, "not json")
    assert result["impact"] is None
    assert result["fix"]["status"] == "recommended"
    assert any("Impact explanation failed" in n for n in result["notes"])
