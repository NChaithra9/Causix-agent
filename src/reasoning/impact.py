"""Impact data: what else is affected when one function/method changes.

Person 1 owns the real impact engine (Neo4j graph traversal: callers -> services -> APIs
-> events -> databases -> tests). Person 2 codes against ImpactProvider. Until that engine
is ready, CodeCallGraph gives a simple, deterministic stand-in built from indexed code.
"""
import re
from typing import Protocol

from pydantic import BaseModel, Field

from src.reasoning.retrieval.chunker import CodeChunk


class ImpactNode(BaseModel):
    id: str                      # unique id, usually the location "file::Name"
    kind: str                    # method | function | class | service | api | event | database | test
    name: str
    location: str | None = None
    depth: int = 1               # hops away from the changed code (1 = direct)
    relation: str = "calls"      # how it depends on the node before it, e.g. calls / tests / exposes


class ImpactGraph(BaseModel):
    changed: str                 # location of the changed code
    nodes: list[ImpactNode] = Field(default_factory=list)


class ImpactProvider(Protocol):
    def impact_of(self, location: str) -> ImpactGraph: ...


class StubImpactProvider:
    """Returns a fixed graph (for tests / until Person 1's engine is connected)."""

    def __init__(self, nodes: list[ImpactNode] | None = None) -> None:
        self.nodes = nodes or []

    def impact_of(self, location: str) -> ImpactGraph:
        return ImpactGraph(changed=location, nodes=list(self.nodes))


def _is_test(chunk: CodeChunk) -> bool:
    filename = chunk.file.rsplit("/", 1)[-1]
    return filename.startswith("test_") or chunk.name.split(".")[-1].startswith("test_")


class CodeCallGraph:
    """Interim impact provider: finds callers by name in indexed code, breadth-first.

    Name-based, so it can over-report when two functions share a name. Replace with
    Person 1's graph-based provider once it exists.
    """

    def __init__(self, max_depth: int = 3) -> None:
        self.max_depth = max_depth
        self.chunks: dict[str, CodeChunk] = {}

    def load(self, chunks: list[CodeChunk]) -> None:
        for c in chunks:
            if c.kind != "class":          # class chunks contain their methods' text; skip them
                self.chunks[c.id] = c

    def _callers_of(self, name: str, exclude: str) -> list[CodeChunk]:
        pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
        return [c for c in self.chunks.values()
                if c.id != exclude and pattern.search(c.text)]

    def impact_of(self, location: str) -> ImpactGraph:
        seen = {location}
        nodes: list[ImpactNode] = []
        frontier = [location]
        for depth in range(1, self.max_depth + 1):
            next_frontier = []
            for loc in frontier:
                short = loc.split("::")[-1].split(".")[-1]
                for caller in self._callers_of(short, exclude=loc):
                    if caller.id in seen:
                        continue
                    seen.add(caller.id)
                    is_test = _is_test(caller)
                    nodes.append(ImpactNode(
                        id=caller.id, kind="test" if is_test else caller.kind,
                        name=caller.name, location=caller.location, depth=depth,
                        relation="tests" if is_test else "calls"))
                    if not is_test:
                        next_frontier.append(caller.id)
            frontier = next_frontier
        return ImpactGraph(changed=location, nodes=nodes)
