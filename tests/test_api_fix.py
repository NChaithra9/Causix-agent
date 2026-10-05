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

ISSUE = json.dumps({"summary": "Refund eligibility check crashes", "error_type": "AttributeError",
                    "suspected_component": "RefundService",
                    "keywords": ["refund", "eligible", "payment profile"]})
RCA = json.dumps({"root_cause": "Missing payment profile is not handled.", "confidence": "high",
                  "affected_component": "RefundService", "location": REFUND,
                  "evidence_ids": ["E1"], "reasoning": "E1 reads the profile without a check.",
                  "suggested_fix": "Return False when the profile is missing.",
                  "insufficient_evidence": False})
FIX = json.dumps({"summary": "Guard against a missing payment profile.", "explanation": "...",
                  "code_after": "def is_eligible_for_refund(self, order):\n"
                                "    profile = self.repository.get_payment_profile(order.customer_id)\n"
                                "    if profile is None:\n        return False\n"
                                "    return profile.allows_refunds and order.amount > 0\n",
                  "test_name": "test_no_profile",
                  "test_code": "def test_no_profile():\n    assert True\n", "risks": []})


@pytest.fixture
def analyze(monkeypatch):
    def _run(*responses, issue_text="Refund API fails"):
        retriever = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
        app.dependency_overrides[get_retriever] = lambda: retriever
        llm = FakeLLM(responses=list(responses))
        monkeypatch.setattr("src.api.main.get_llm", lambda: llm)
        client = TestClient(app)
        client.post("/api/causix/index", json={"repo_path": str(SAMPLE)})
        job = client.post("/api/causix/analyze", json={"issue": issue_text}).json()["job_id"]
        return client.get(f"/api/causix/result/{job}").json()
    yield _run
    app.dependency_overrides.clear()


def test_full_pipeline_returns_fix(analyze):
    result = analyze(ISSUE, RCA, FIX)
    fix = result["fix"]
    assert fix["status"] == "recommended" and fix["applied"] is False
    assert fix["location"] == REFUND
    assert "if profile is None" in fix["diff"]
    assert fix["regression_test"]["name"] == "test_no_profile"


def test_fix_skipped_when_rca_insufficient(analyze):
    weak = json.loads(RCA) | {"insufficient_evidence": True}
    result = analyze(ISSUE, json.dumps(weak))
    assert result["fix"]["status"] == "skipped"


def test_fix_failure_keeps_rca(analyze):
    result = analyze(ISSUE, RCA, "not json")
    assert result["fix"] is None
    assert result["rca"]["location"] == REFUND
    assert any("Fix recommendation failed" in n for n in result["notes"])
