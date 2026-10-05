"""Test impact -- Phase 5, requirement 12.

Three deterministic sources, each labelled so a consumer can tell a real
graph edge from a naming rule:

1. Test nodes with a ``(:Test)-[:VALIDATES]->(code)`` edge (graph fact).
2. Test functions that actually ``CALLS`` impacted code (graph fact; found
   by the caller traversal in ``analyzer`` -- a caller living in a test file
   is reported as a TEST, not as a production caller).
3. Phase 4's naming-convention rule (``test_<name>`` functions in test
   files) for the *changed* code only -- reused as-is from
   :func:`src.fix_localization.related_tests.find_related_tests`, and marked
   ``NAMING_CONVENTION`` rather than presented as a graph relationship.

Nothing is generated and no test is executed.
"""

from __future__ import annotations

from src.fix_localization.related_tests import find_related_tests
from src.graph.connection import Neo4jConnection

from .code_graph import CodeNode
from .models import GraphRelationship, ImpactCategory, ImpactItem, PathDirection, PathStep
from .paths import PathCollector

__all__ = ["trace_validating_tests", "trace_naming_convention_tests"]


def trace_validating_tests(
    connection: Neo4jConnection,
    anchor_paths: dict[str, list[list[PathStep]]],
    collector: PathCollector,
) -> list[ImpactItem]:
    """Tests that ``VALIDATES`` any impacted method / function / class / file."""
    if not anchor_paths:
        return []
    rows = connection.execute_read(
        "MATCH (t:Test)-[:VALIDATES]->(x) WHERE x.id IN $ids "
        "RETURN t.id AS id, coalesce(t.name, t.id) AS name, coalesce(t.path, t.file_path, t.file) AS file, "
        "x.id AS target_id",
        {"ids": list(anchor_paths)},
    )
    items: dict[str, ImpactItem] = {}
    for row in rows:
        depth = None
        for path in anchor_paths[row["target_id"]]:
            steps = path + [
                PathStep(
                    node_id=row["id"], name=row["name"], category=ImpactCategory.TEST, node_label="Test",
                    relationship=GraphRelationship.VALIDATES.value, direction=PathDirection.REVERSE,
                )
            ]
            collector.add(row["id"], ImpactCategory.TEST, steps)
            depth = len(steps) - 1 if depth is None else min(depth, len(steps) - 1)
        existing = items.get(row["id"])
        if existing is None or (depth is not None and depth < existing.depth):
            items[row["id"]] = ImpactItem(
                id=row["id"], name=row["name"], category=ImpactCategory.TEST, node_label="Test",
                depth=depth or 1, relationship=GraphRelationship.VALIDATES.value, file=row["file"],
                method=row["name"],
            )
    return list(items.values())


def trace_naming_convention_tests(
    connection: Neo4jConnection,
    primary: CodeNode,
    primary_paths: list[list[PathStep]],
    collector: PathCollector,
    already_found: set[tuple[str | None, str]],
) -> list[ImpactItem]:
    """Phase 4's naming rule applied to the changed code; skips tests already found via real edges."""
    related = find_related_tests(connection, primary.repository_id, primary.name)
    items: list[ImpactItem] = []
    for test in related:
        if (test.test_file, test.test_function) in already_found:
            continue
        test_id = f"{test.test_file}::{test.test_function}"
        depth = None
        for path in primary_paths:
            steps = path + [
                PathStep(
                    node_id=test_id, name=test.test_function, category=ImpactCategory.TEST, node_label="Function",
                    relationship=GraphRelationship.NAMING_CONVENTION.value, direction=None,
                )
            ]
            collector.add(test_id, ImpactCategory.TEST, steps)
            depth = len(steps) - 1 if depth is None else min(depth, len(steps) - 1)
        items.append(
            ImpactItem(
                id=test_id, name=test.test_function, category=ImpactCategory.TEST, node_label="Function",
                depth=depth or 1, relationship=GraphRelationship.NAMING_CONVENTION.value,
                repository=primary.repository_name, file=test.test_file, method=test.test_function,
                detail={"target": test.target},
            )
        )
    return items
