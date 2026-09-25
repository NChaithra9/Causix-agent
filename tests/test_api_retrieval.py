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


@pytest.fixture
def client(monkeypatch):
    retriever = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
    app.dependency_overrides[get_retriever] = lambda: retriever
    llm = FakeLLM(json.dumps({"summary": "Refund eligibility fails", "error_type": None,
                              "suspected_component": "RefundService",
                              "keywords": ["refund", "eligible"]}))
    monkeypatch.setattr("src.api.main.get_llm", lambda: llm)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_index_then_search(client):
    resp = client.post("/api/causix/index", json={"repo_path": str(SAMPLE)})
    assert resp.status_code == 200
    assert resp.json()["chunks_indexed"] >= 8

    hits = client.post("/api/causix/search", json={"query": "refund eligibility", "top_k": 3}).json()
    assert any("is_eligible_for_refund" in h["location"] for h in hits)


def test_index_rejects_missing_directory(client):
    assert client.post("/api/causix/index", json={"repo_path": "/no/such/dir"}).status_code == 400


def test_analyze_includes_retrieved_evidence(client):
    client.post("/api/causix/index", json={"repo_path": str(SAMPLE)})
    job_id = client.post("/api/causix/analyze", json={"issue": "Refund API fails"}).json()["job_id"]
    result = client.get(f"/api/causix/result/{job_id}").json()
    assert any(e["source"] == "semantic" and "refund" in e["location"] for e in result["evidence"])
    assert not any("Person 1" in n for n in result["notes"])   # evidence exists now
