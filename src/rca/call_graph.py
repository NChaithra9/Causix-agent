"""Call Graph Traversal (forward + reverse) -- Phase 3, requirements 5 & 6.

Traverses the Phase 2 ``CALLS`` relationships from a given Method/Function
node, in either direction, to a configurable depth. Cycle-safe (a node is
never expanded twice, however many paths reach it) and depth-bounded (never
walks the whole graph -- only the requested number of hops from the start
node).
"""

from __future__ import annotations

from src.graph.connection import Neo4jConnection

from .models import CallGraphEdge, CallGraphResult, ResolutionStatus

__all__ = ["traverse_callees", "traverse_callers"]

DEFAULT_MAX_DEPTH = 2


def traverse_callees(connection: Neo4jConnection, start_node_id: str, *, max_depth: int = DEFAULT_MAX_DEPTH) -> CallGraphResult:
    """Forward traversal: what does ``start_node_id`` call, directly or transitively?"""
    return _traverse(connection, start_node_id, direction="forward", max_depth=max_depth)


def traverse_callers(connection: Neo4jConnection, start_node_id: str, *, max_depth: int = DEFAULT_MAX_DEPTH) -> CallGraphResult:
    """Reverse traversal: who calls ``start_node_id``, directly or transitively?"""
    return _traverse(connection, start_node_id, direction="reverse", max_depth=max_depth)


def _traverse(
    connection: Neo4jConnection, start_node_id: str, *, direction: str, max_depth: int
) -> CallGraphResult:
    if max_depth < 1:
        max_depth = 1

    start_rows = connection.execute_read(
        "MATCH (n) WHERE n.id = $id AND (n:Method OR n:Function) RETURN n.id AS id, n.name AS name, labels(n)[0] AS label",
        {"id": start_node_id},
    )
    if not start_rows:
        return CallGraphResult(
            resolution_status=ResolutionStatus.UNRESOLVED,
            direction="forward" if direction == "forward" else "reverse",
            start_node_id=start_node_id,
        )
    start_name = start_rows[0]["name"]

    edges: list[CallGraphEdge] = []
    visited: set[str] = {start_node_id}
    frontier = [start_node_id]

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        if direction == "forward":
            query = (
                "MATCH (a)-[:CALLS]->(b) WHERE a.id IN $frontier "
                "RETURN a.id AS source_id, a.name AS source_name, labels(a)[0] AS source_label, "
                "b.id AS target_id, b.name AS target_name, labels(b)[0] AS target_label"
            )
        else:
            query = (
                "MATCH (a)-[:CALLS]->(b) WHERE b.id IN $frontier "
                "RETURN b.id AS source_id, b.name AS source_name, labels(b)[0] AS source_label, "
                "a.id AS target_id, a.name AS target_name, labels(a)[0] AS target_label"
            )
        rows = connection.execute_read(query, {"frontier": frontier})

        next_frontier: list[str] = []
        for row in rows:
            target_id = row["target_id"]
            edges.append(
                CallGraphEdge(
                    depth=depth,
                    source_id=row["source_id"],
                    source_name=row["source_name"],
                    source_label=row["source_label"],
                    target_id=target_id,
                    target_name=row["target_name"],
                    target_label=row["target_label"],
                )
            )
            if target_id not in visited:
                visited.add(target_id)
                next_frontier.append(target_id)
        frontier = next_frontier

    status = ResolutionStatus.RESOLVED if edges else ResolutionStatus.PARTIALLY_RESOLVED
    return CallGraphResult(
        resolution_status=status,
        direction="forward" if direction == "forward" else "reverse",
        start_node_id=start_node_id,
        start_name=start_name,
        max_depth=max_depth,
        edges=edges,
        visited_node_ids=visited,
    )
