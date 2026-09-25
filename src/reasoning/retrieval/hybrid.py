"""Hybrid retrieval: semantic (vector) hits + graph hits, merged with reciprocal rank fusion."""
from src.reasoning.retrieval.chunker import CodeChunk
from src.reasoning.retrieval.embeddings import Embedder
from src.reasoning.retrieval.graph import GraphSearch, graph_terms
from src.reasoning.retrieval.vector_store import SearchHit, VectorStore
from src.reasoning.schemas import Evidence, IssueUnderstanding

RRF_K = 60  # standard constant; dampens the gap between rank 1 and rank 2


def issue_query(issue: IssueUnderstanding) -> str:
    parts = [issue.summary, issue.suspected_component or "", issue.error_type or "", *issue.keywords]
    return " ".join(p for p in parts if p)


def hit_to_evidence(hit: SearchHit) -> Evidence:
    c = hit.chunk
    return Evidence(source="semantic",
                    description=f"{c.kind} {c.name} (lines {c.start_line}-{c.end_line})",
                    location=c.location, score=round(hit.score, 4))


def fuse(lists: list[list[Evidence]], top_k: int) -> list[Evidence]:
    """Reciprocal rank fusion; items with the same location are merged into one."""
    scores: dict[str, float] = {}
    first: dict[str, Evidence] = {}
    for items in lists:
        for rank, ev in enumerate(items, start=1):
            key = ev.location or f"{ev.source}:{ev.description}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            first.setdefault(key, ev)
    ranked = sorted(scores, key=scores.get, reverse=True)[:top_k]
    return [first[k].model_copy(update={"score": round(scores[k], 4)}) for k in ranked]


class HybridRetriever:
    def __init__(self, embedder: Embedder, store: VectorStore, graph: GraphSearch | None = None):
        self.embedder, self.store, self.graph = embedder, store, graph

    def index(self, chunks: list[CodeChunk]) -> int:
        if chunks:
            self.store.add(chunks, self.embedder.embed([f"{c.name}\n{c.text}" for c in chunks]))
        return len(chunks)

    def search(self, query: str, top_k: int = 5) -> list[Evidence]:
        vector = self.embedder.embed([query])[0]
        return [hit_to_evidence(h) for h in self.store.search(vector, top_k)]

    def retrieve(self, issue: IssueUnderstanding, top_k: int = 5) -> list[Evidence]:
        semantic = self.search(issue_query(issue), top_k)
        graph = self.graph.related(graph_terms(issue), top_k) if self.graph else []
        return fuse([semantic, graph], top_k)
