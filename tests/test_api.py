import json

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_orchestrator
from src.reasoning.agents.issue_agent import IssueAgent
from src.reasoning.facts import StubFactsProvider
from src.reasoning.llm import FakeLLM
from src.reasoning.orchestrator import Orchestrator
from src.reasoning.schemas import Evidence

LLM_JSON = json.dumps({"summary": "Refund API returns 500", "error_type": "AttributeError",
                       "suspected_component": "RefundService", "keywords": ["refund"]})


def use_orchestrator(llm_response: str, evidence=None):
    app.dependency_overrides[get_orchestrator] = lambda: Orchestrator(
        IssueAgent(FakeLLM(llm_response)), StubFactsProvider(evidence))


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_analyze_happy_path(client):
    use_orchestrator(LLM_JSON)
    resp = client.post("/api/causix/analyze", json={"issue": "Refund API fails"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # TestClient runs background tasks before returning, so the job is already done
    assert client.get(f"/api/causix/status/{job_id}").json()["state"] == "completed"

    result = client.get(f"/api/causix/result/{job_id}").json()
    assert result["issue"]["suspected_component"] == "RefundService"
    assert result["evidence"] == []
    assert any("Person 1" in n for n in result["notes"])


def test_evidence_from_facts_provider_is_returned(client):
    ev = Evidence(source="stack_trace", description="Null payment profile",
                  location="refund_service.py::is_eligible_for_refund")
    use_orchestrator(LLM_JSON, evidence=[ev])
    job_id = client.post("/api/causix/analyze", json={"issue": "x"}).json()["job_id"]
    result = client.get(f"/api/causix/result/{job_id}").json()
    assert result["evidence"][0]["location"] == "refund_service.py::is_eligible_for_refund"


def test_bad_llm_output_marks_job_failed(client):
    use_orchestrator("not json at all")
    job_id = client.post("/api/causix/analyze", json={"issue": "x"}).json()["job_id"]
    status = client.get(f"/api/causix/status/{job_id}").json()
    assert status["state"] == "failed"
    assert "JSON" in status["error"]
    assert client.get(f"/api/causix/result/{job_id}").status_code == 409


def test_empty_issue_rejected(client):
    assert client.post("/api/causix/analyze", json={"issue": ""}).status_code == 422


def test_unknown_job_returns_404(client):
    assert client.get("/api/causix/status/nope").status_code == 404
    assert client.get("/api/causix/result/nope").status_code == 404
