"""Neo4j constraints/indexes for RootFix's stable identifiers -- Phase 2, requirement 9.

A uniqueness constraint on `id` for each populated node label gives two
things at once: fast lookup (Neo4j indexes the constrained property
automatically) and a hard guarantee against accidental duplicates, on top of
the idempotent `MERGE`-based ingestion in `src.graph.ingestion`.

Uses `IF NOT EXISTS`, so applying these is itself idempotent -- safe to call
before every ingestion run. Written as plain Cypher DDL, compatible with the
Neo4j version this project's driver targets (the `CREATE CONSTRAINT ... IF
NOT EXISTS FOR (n:Label) REQUIRE n.prop IS UNIQUE` syntax is Neo4j 5.x).
"""

from __future__ import annotations

from .connection import Neo4jConnection
from .schema import NodeLabel

__all__ = ["CONSTRAINT_STATEMENTS", "ensure_constraints"]

CONSTRAINT_STATEMENTS: tuple[str, ...] = tuple(
    f"CREATE CONSTRAINT {label.value.lower()}_id_unique IF NOT EXISTS "
    f"FOR (n:`{label.value}`) REQUIRE n.id IS UNIQUE"
    for label in (
        NodeLabel.REPOSITORY,
        NodeLabel.FILE,
        NodeLabel.CLASS,
        NodeLabel.METHOD,
        NodeLabel.FUNCTION,
        NodeLabel.COMMIT,
    )
)


def ensure_constraints(connection: Neo4jConnection) -> None:
    """Create every uniqueness constraint RootFix relies on, if not already present."""
    for statement in CONSTRAINT_STATEMENTS:
        connection.execute_write(statement)
