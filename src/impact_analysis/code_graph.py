"""Call-graph side of impact analysis -- Phase 5, requirements 3, 4, 5, 6, 17, 18.

Reuses Phase 3's bounded, cycle-safe reverse CALLS traversal
(:func:`src.rca.call_graph.traverse_callers`) rather than re-implementing
it, and adds what impact analysis needs on top: for every caller, the
containing class / file / repository, and the *set of shortest-path
predecessors* so every distinct route from the changed code to a caller
can be reconstructed (not just one of them).

Direction: a caller is potentially affected when the code it calls changes,
so impact flows against the stored ``(caller)-[:CALLS]->(callee)`` edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.graph.connection import Neo4jConnection
from src.rca.call_graph import traverse_callers
from src.rca.models import ResolutionStatus

__all__ = ["CodeNode", "CallerGraph", "fetch_code_nodes", "build_caller_graph", "find_unresolved_call_sites", "has_more_callers"]


@dataclass
class CodeNode:
    """A Method / Function / Class node with the containers it lives in."""

    id: str
    name: str
    label: str
    line: int | None
    class_id: str | None
    class_name: str | None
    file_id: str | None
    file_path: str | None
    repository_id: str | None
    repository_name: str | None

    @property
    def qualified_name(self) -> str:
        if self.label == "Method" and self.class_name:
            return f"{self.class_name}.{self.name}"
        return self.name


@dataclass
class CallerGraph:
    """Result of the reverse traversal from the changed node."""

    start_id: str
    depth: dict[str, int] = field(default_factory=dict)  # node id -> depth (start = 0)
    # node id -> ids it was reached from at depth-1 (its callees on a shortest path to the start)
    predecessors: dict[str, list[str]] = field(default_factory=dict)
    max_depth: int = 0


def fetch_code_nodes(connection: Neo4jConnection, ids: list[str]) -> dict[str, CodeNode]:
    if not ids:
        return {}
    rows = connection.execute_read(
        "MATCH (n) WHERE n.id IN $ids AND (n:Method OR n:Function OR n:Class) "
        "OPTIONAL MATCH (c:Class {id: n.class_id}) "
        "OPTIONAL MATCH (f:File {id: n.file_id}) "
        "OPTIONAL MATCH (r:Repository {id: f.repository_id}) "
        "RETURN n.id AS id, n.name AS name, labels(n)[0] AS label, n.line_number AS line, "
        "c.id AS class_id, c.name AS class_name, f.id AS file_id, f.path AS file_path, "
        "r.id AS repository_id, r.name AS repository_name",
        {"ids": ids},
    )
    return {
        row["id"]: CodeNode(
            id=row["id"],
            name=row["name"],
            label=row["label"],
            line=row["line"],
            class_id=row["class_id"],
            class_name=row["class_name"],
            file_id=row["file_id"],
            file_path=row["file_path"],
            repository_id=row["repository_id"],
            repository_name=row["repository_name"],
        )
        for row in rows
    }


def build_caller_graph(connection: Neo4jConnection, start_id: str, max_depth: int) -> CallerGraph | None:
    """Reverse CALLS traversal to ``max_depth``. ``None`` if ``start_id`` isn't a Method/Function.

    ``max_depth < 1`` means "the changed code only" -- no traversal.
    """
    graph = CallerGraph(start_id=start_id, depth={start_id: 0}, max_depth=max(max_depth, 0))
    if max_depth < 1:
        return graph

    result = traverse_callers(connection, start_id, max_depth=max_depth)
    if result.resolution_status == ResolutionStatus.UNRESOLVED:
        return None

    # Edges arrive in depth order; first sighting of a caller fixes its depth.
    for edge in result.edges:
        graph.depth.setdefault(edge.source_id, edge.depth)

    # Keep an edge as a path predecessor only if it steps exactly one level
    # closer to the start. Edges to already-seen nodes (cycles, cross edges)
    # never become predecessors, so path reconstruction always terminates.
    for edge in result.edges:
        caller_depth = graph.depth.get(edge.source_id)
        callee_depth = graph.depth.get(edge.target_id)
        if caller_depth == edge.depth and callee_depth == edge.depth - 1:
            preds = graph.predecessors.setdefault(edge.source_id, [])
            if edge.target_id not in preds:
                preds.append(edge.target_id)
    return graph


def has_more_callers(connection: Neo4jConnection, graph: CallerGraph) -> bool:
    """Whether callers exist beyond the traversal's deepest level (i.e. the depth limit cut something off)."""
    if graph.max_depth < 1:
        frontier = [graph.start_id]
    else:
        frontier = [n for n, d in graph.depth.items() if d == graph.max_depth]
    if not frontier:
        return False
    rows = connection.execute_read(
        "MATCH (a)-[:CALLS]->(b) WHERE b.id IN $frontier AND (a:Method OR a:Function) "
        "AND NOT a.id IN $seen RETURN count(DISTINCT a) AS n",
        {"frontier": frontier, "seen": list(graph.depth)},
    )
    return bool(rows and rows[0]["n"])


def find_unresolved_call_sites(
    connection: Neo4jConnection, target_name: str, exclude_ids: set[str], limit: int = 25
) -> list[tuple[str, str]]:
    """Methods/functions with an *unresolved* call whose last dotted segment equals ``target_name``.

    These are NOT impacted callers -- no CALLS edge exists, so the engine
    refuses to claim them. They are reported only as unresolved information,
    so a reader knows the graph could not rule them in or out.
    Returns ``(caller qualified name, raw call text)`` pairs.
    """
    rows = connection.execute_read(
        "MATCH (m) WHERE (m:Method OR m:Function) AND m.unresolved_calls IS NOT NULL "
        "AND any(c IN m.unresolved_calls WHERE c = $name OR c ENDS WITH ('.' + $name)) "
        "AND NOT m.id IN $exclude "
        "OPTIONAL MATCH (cls:Class {id: m.class_id}) "
        "RETURN m.id AS id, m.name AS name, cls.name AS class_name, "
        "[c IN m.unresolved_calls WHERE c = $name OR c ENDS WITH ('.' + $name)][0] AS call "
        "ORDER BY class_name, name LIMIT $limit",
        {"name": target_name, "exclude": list(exclude_ids), "limit": limit},
    )
    return [
        (f"{row['class_name']}.{row['name']}" if row["class_name"] else row["name"], row["call"])
        for row in rows
    ]
