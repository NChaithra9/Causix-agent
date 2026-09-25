from pathlib import Path

from src.reasoning.retrieval.chunker import chunk_repository
from src.reasoning.retrieval.embeddings import HashEmbedder
from src.reasoning.retrieval.graph import StubGraphSearch, graph_terms
from src.reasoning.retrieval.hybrid import HybridRetriever, fuse
from src.reasoning.retrieval.vector_store import InMemoryVectorStore
from src.reasoning.schemas import Evidence, IssueUnderstanding

SAMPLE = Path(__file__).parent / "fixtures" / "sample_repo"
REFUND_METHOD = "payment_service/refund_service.py::RefundService.is_eligible_for_refund"

ISSUE = IssueUnderstanding(summary="Refund eligibility check crashes",
                           error_type="AttributeError", suspected_component="RefundService",
                           keywords=["refund", "eligible", "payment profile"])


def make_retriever(graph=None) -> HybridRetriever:
    r = HybridRetriever(HashEmbedder(), InMemoryVectorStore(), graph)
    r.index(chunk_repository(SAMPLE))
    return r


def test_semantic_search_finds_refund_method():
    results = make_retriever().search("refund eligibility payment profile", top_k=3)
    assert results[0].location == REFUND_METHOD
    assert results[0].source == "semantic"


def test_graph_terms_put_component_first_and_dedupe():
    issue = ISSUE.model_copy(update={"keywords": ["RefundService", "refund"]})
    assert graph_terms(issue) == ["RefundService", "AttributeError", "refund"]


def test_retrieve_merges_graph_and_semantic_evidence():
    graph = StubGraphSearch({"RefundService": [
        Evidence(source="graph", description="RefundController calls RefundService",
                 location="api/refund_controller.py::RefundController.refund"),
        Evidence(source="graph", description="same method, found via graph", location=REFUND_METHOD),
    ]})
    results = make_retriever(graph).retrieve(ISSUE, top_k=5)
    locations = [e.location for e in results]
    assert locations[0] == REFUND_METHOD          # found by both -> ranked first
    assert locations.count(REFUND_METHOD) == 1    # merged, not duplicated
    assert "api/refund_controller.py::RefundController.refund" in locations


def test_fuse_respects_top_k():
    evs = [Evidence(source="semantic", description=str(i), location=f"f{i}") for i in range(10)]
    assert len(fuse([evs], top_k=3)) == 3


def test_empty_index_returns_nothing():
    r = HybridRetriever(HashEmbedder(), InMemoryVectorStore())
    assert r.retrieve(ISSUE) == []
