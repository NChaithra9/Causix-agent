import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_retriever
from src.reasoning.llm import FakeLLM
from src.reasoning.retrieval.embeddings import HashEmbedder
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.retrieval.vector_store import InMemoryVectorStore

SAMPLE = Path(__file__).parent / "fixtures" / "sample_repo"
REFUND = "payment_service/refund_service.py::RefundService.is_eligible_for_refund"

ISSUE_JSON = json.dumps({"summary": "Refund eligibility check crashes", "error_type": "AttributeError",
                         "suspected_component": "RefundService",
                         "keywords": ["refund", "eligible", "payment profile"]})


def rca_json(location=REFUND, evidence_ids=("E1",)):
    return json.dumps({"root_cause": "Missing payment profile is not handled.", "confidence": "medium",
                       "affected_component": "RefundService", "location": location,
                       "evidence_ids": list(evidence_ids), "reasoning": "E1 reads the profile.",
                       "suggested_fix": "Guard against a missing payment profile.",
                       "insufficient_evidence": False})


@pytest.fixture
def run_analysis(monkeypatch):
    def _run(*llm_responses, index=True):
        retriever = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
        app.dependency_overrides[get_retriever] = lambda: retriever
        llm = FakeLLM(responses=list(llm_responses))
        monkeypatch.setattr("src.api.main.get_llm", lambda: llm)
        client = TestClient(app)
        if index:
            client.post("/api/causix/index", json={"repo_path": str(SAMPLE)})
        job_id = client.post("/api/causix/analyze", json={"issue": "Refund API fails"}).json()["job_id"]
        return client.get(f"/api/causix/result/{job_id}").json()
    yield _run
    app.dependency_overrides.clear()


def test_end_to_end_root_cause(run_analysis):
    result = run_analysis(ISSUE_JSON, rca_json())
    assert result["evidence"][0]["location"] == REFUND      # retrieval ranked it first
    assert result["rca"]["location"] == REFUND
    assert result["root_cause"] == "Missing payment profile is not handled."
    assert result["rca"]["supporting_evidence"][0]["location"] == REFUND


def test_hallucinated_location_is_removed(run_analysis):
    result = run_analysis(ISSUE_JSON, rca_json(location="ghost.py::Nope"))
    assert result["rca"]["location"] is None
    assert result["rca"]["warnings"]


def test_no_index_means_insufficient_evidence(run_analysis):
    result = run_analysis(ISSUE_JSON, index=False)
    assert result["evidence"] == []
    assert result["rca"]["insufficient_evidence"] is True
    assert result["root_cause"] is None


def test_rca_failure_keeps_evidence(run_analysis):
    result = run_analysis(ISSUE_JSON, "not json")
    assert result["rca"] is None
    assert result["evidence"]
    assert any("Root cause analysis failed" in n for n in result["notes"])
