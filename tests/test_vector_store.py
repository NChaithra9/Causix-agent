from src.reasoning.retrieval.chunker import CodeChunk
from src.reasoning.retrieval.vector_store import InMemoryVectorStore, MilvusVectorStore


def chunk(name: str) -> CodeChunk:
    return CodeChunk(id=f"a.py::{name}", file="a.py", name=name, kind="function",
                     start_line=1, end_line=2, text=f"def {name}(): pass")


def test_in_memory_ranks_by_similarity_and_dedupes():
    store = InMemoryVectorStore()
    store.add([chunk("x"), chunk("y")], [[1.0, 0.0], [0.0, 1.0]])
    store.add([chunk("x")], [[1.0, 0.0]])            # re-index same chunk
    assert store.count() == 2
    hits = store.search([0.9, 0.1], top_k=2)
    assert [h.chunk.name for h in hits] == ["x", "y"]


class FakeMilvus:
    def __init__(self):
        self.rows, self.created = [], False

    def has_collection(self, name):
        return self.created

    def create_collection(self, **kwargs):
        self.created = True
        self.kwargs = kwargs

    def upsert(self, collection_name, data):
        self.rows.extend(data)

    def search(self, collection_name, data, limit, output_fields):
        return [[{"id": r["id"], "distance": 0.9, "entity": {k: r[k] for k in output_fields}}
                 for r in self.rows[:limit]]]

    def get_collection_stats(self, collection_name):
        return {"row_count": len(self.rows)}


def test_milvus_store_round_trip_with_fake_client():
    client = FakeMilvus()
    store = MilvusVectorStore(uri="unused", dim=2, client=client)
    assert client.kwargs["metric_type"] == "COSINE"
    store.add([chunk("x")], [[1.0, 0.0]])
    hits = store.search([1.0, 0.0], top_k=1)
    assert hits[0].chunk.location == "a.py::x"
    assert hits[0].score == 0.9
    assert store.count() == 1
