"""Vector stores: in-memory for dev/tests, Milvus when MILVUS_URI is set."""
import math
import os
from typing import Protocol

from pydantic import BaseModel

from src.reasoning.retrieval.chunker import CodeChunk


class SearchHit(BaseModel):
    chunk: CodeChunk
    score: float


class VectorStore(Protocol):
    def add(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> None: ...
    def search(self, vector: list[float], top_k: int) -> list[SearchHit]: ...
    def count(self) -> int: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[CodeChunk, list[float]]] = {}

    def add(self, chunks, vectors) -> None:
        for chunk, vec in zip(chunks, vectors):
            self._items[chunk.id] = (chunk, vec)   # re-indexing replaces, never duplicates

    def search(self, vector, top_k) -> list[SearchHit]:
        scored = [SearchHit(chunk=c, score=_cosine(vector, v)) for c, v in self._items.values()]
        scored = [h for h in scored if h.score > 0]
        return sorted(scored, key=lambda h: h.score, reverse=True)[:top_k]

    def count(self) -> int:
        return len(self._items)


class MilvusVectorStore:
    """Milvus-backed store. `client` can be injected for tests."""

    FIELDS = ["file", "name", "kind", "start_line", "end_line", "text"]

    def __init__(self, uri: str, dim: int, collection: str = "causix_code", client=None) -> None:
        if client is None:
            from pymilvus import MilvusClient  # imported lazily so tests don't need Milvus
            client = MilvusClient(uri=uri)
        self.client, self.collection = client, collection
        if not self.client.has_collection(collection):
            self.client.create_collection(
                collection_name=collection, dimension=dim, primary_field_name="id",
                id_type="string", max_length=1024, metric_type="COSINE", auto_id=False)

    def add(self, chunks, vectors) -> None:
        if not chunks:
            return
        rows = [{"id": c.id, "vector": v, **c.model_dump(include=set(self.FIELDS))}
                for c, v in zip(chunks, vectors)]
        self.client.upsert(collection_name=self.collection, data=rows)

    def search(self, vector, top_k) -> list[SearchHit]:
        results = self.client.search(collection_name=self.collection, data=[vector],
                                     limit=top_k, output_fields=self.FIELDS)
        hits = []
        for row in results[0] if results else []:
            entity = row["entity"]
            chunk = CodeChunk(id=row["id"], **{k: entity[k] for k in self.FIELDS})
            hits.append(SearchHit(chunk=chunk, score=float(row["distance"])))
        return hits

    def count(self) -> int:
        stats = self.client.get_collection_stats(collection_name=self.collection)
        return int(stats.get("row_count", 0))


def get_vector_store(dim: int) -> VectorStore:
    uri = os.getenv("MILVUS_URI")
    return MilvusVectorStore(uri=uri, dim=dim) if uri else InMemoryVectorStore()
