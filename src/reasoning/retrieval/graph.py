"""Graph side of hybrid retrieval. Person 1 owns Neo4j; Person 2 codes against GraphSearch."""
from typing import Protocol

from src.reasoning.schemas import Evidence, IssueUnderstanding


class GraphSearch(Protocol):
    def related(self, terms: list[str], limit: int) -> list[Evidence]: ...


def graph_terms(issue: IssueUnderstanding) -> list[str]:
    """Turn the understood issue into names to look up in the graph (component first)."""
    terms = [issue.suspected_component, issue.error_type, *issue.keywords]
    seen, out = set(), []
    for t in terms:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


class StubGraphSearch:
    """Returns canned evidence per term until Person 1's Neo4j search is ready."""

    def __init__(self, by_term: dict[str, list[Evidence]] | None = None) -> None:
        self.by_term = {k.lower(): v for k, v in (by_term or {}).items()}

    def related(self, terms, limit) -> list[Evidence]:
        out: list[Evidence] = []
        for t in terms:
            out.extend(self.by_term.get(t.lower(), []))
        return out[:limit]
