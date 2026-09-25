"""A small set of deterministic verification queries -- Phase 2, requirement 11.

These exist to confirm the graph actually works, not to power any reasoning
engine -- that's explicitly out of scope for this phase. Each function is a
thin wrapper around one Cypher query, returning plain dicts.
"""

from __future__ import annotations

from typing import Any

from .connection import Neo4jConnection

__all__ = [
    "repository_files",
    "class_methods",
    "method_call_relationships",
    "commit_modified_files",
]


def repository_files(connection: Neo4jConnection) -> list[dict[str, Any]]:
    """Every Repository -> CONTAINS -> File relationship in the graph."""
    return connection.execute_read(
        "MATCH (r:Repository)-[:CONTAINS]->(f:File) "
        "RETURN r.id AS repository_id, r.name AS repository_name, "
        "f.id AS file_id, f.path AS file_path"
    )


def class_methods(connection: Neo4jConnection) -> list[dict[str, Any]]:
    """Every Class -> CONTAINS -> Method relationship in the graph."""
    return connection.execute_read(
        "MATCH (c:Class)-[:CONTAINS]->(m:Method) "
        "RETURN c.id AS class_id, c.name AS class_name, "
        "m.id AS method_id, m.name AS method_name"
    )


def method_call_relationships(connection: Neo4jConnection) -> list[dict[str, Any]]:
    """Every Method/Function -> CALLS -> Method/Function relationship in the graph."""
    return connection.execute_read(
        "MATCH (source)-[:CALLS]->(target) "
        "WHERE source:Method OR source:Function "
        "RETURN labels(source) AS source_labels, source.name AS source_name, "
        "labels(target) AS target_labels, target.name AS target_name"
    )


def commit_modified_files(connection: Neo4jConnection) -> list[dict[str, Any]]:
    """Every Commit -> MODIFIES -> File relationship in the graph."""
    return connection.execute_read(
        "MATCH (c:Commit)-[:MODIFIES]->(f:File) "
        "RETURN c.id AS commit_id, c.hash AS commit_hash, c.message AS commit_message, "
        "f.id AS file_id, f.path AS file_path"
    )
