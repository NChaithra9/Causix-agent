import math

from src.reasoning.retrieval.embeddings import HashEmbedder, tokenize


def test_tokenize_splits_snake_and_camel_case():
    assert tokenize("is_eligible_for_refund") == ["eligible", "refund"]
    assert tokenize("RefundService") == ["refund", "service"]


def test_same_text_same_vector_and_normalized():
    emb = HashEmbedder(dim=64)
    a, b = emb.embed(["refund payment", "refund payment"])
    assert a == b
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)


def test_related_text_scores_higher_than_unrelated():
    emb = HashEmbedder()
    q, related, unrelated = emb.embed(["refund eligibility", "is_eligible_for_refund", "send invoice email"])
    dot = lambda x, y: sum(i * j for i, j in zip(x, y))
    assert dot(q, related) > dot(q, unrelated)
