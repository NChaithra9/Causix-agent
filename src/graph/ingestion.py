"""Neo4j Ingestion -- Phase 2, requirement 7.

Takes the plain records `src.graph.mapper` already produced and MERGEs them
into Neo4j. This is the only module in the graph package that actually talks
to the database for writes; everything upstream (parsing, relationship
extraction, mapping) is pure Python with no database dependency, which keeps
Phase 1 independently testable (requirement 10).

Idempotency (requirement 7/8) comes from two things together: stable ids
(`src.graph.identity`) and Cypher `MERGE` keyed on those ids -- running
ingestion twice with the same Phase 1 output matches the same existing
nodes/relationships instead of creating new ones.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.repository import Repository

from .connection import Neo4jConnection
from .constraints import ensure_constraints
from .mapper import map_repository_to_graph
from .records import GraphBatch, NodeRecord, RelationshipRecord

__all__ = ["IngestionSummary", "ingest_batch", "ingest_repository"]


@dataclass(frozen=True)
class IngestionSummary:
    """How many nodes/relationships a single ingestion run MERGEd."""

    nodes_merged: int
    relationships_merged: int


def ingest_batch(connection: Neo4jConnection, batch: GraphBatch) -> IngestionSummary:
    """MERGE every node and relationship in `batch` into Neo4j.

    Nodes are grouped by label, relationships by (start_label, rel_type,
    end_label), and each group is sent as a single `UNWIND`-driven `MERGE`
    query -- far fewer round trips than one query per record, while still
    being exactly as idempotent as running them one at a time.
    """
    _merge_nodes(connection, batch.nodes)
    _merge_relationships(connection, batch.relationships)
    return IngestionSummary(nodes_merged=len(batch.nodes), relationships_merged=len(batch.relationships))


def ingest_repository(connection: Neo4jConnection, repo: Repository) -> IngestionSummary:
    """The full Phase 2 entry point: Repository (Phase 1 output) -> Neo4j.

    Ensures constraints exist (cheap and idempotent -- safe on every run),
    maps `repo` to a `GraphBatch`, and ingests it.
    """
    ensure_constraints(connection)
    batch = map_repository_to_graph(repo)
    return ingest_batch(connection, batch)


def _merge_nodes(connection: Neo4jConnection, nodes: list[NodeRecord]) -> None:
    by_label: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        by_label[node.label.value].append({"id": node.id, "properties": dict(node.properties)})

    for label, rows in by_label.items():
        query = (
            f"UNWIND $rows AS row\n"
            f"MERGE (n:`{label}` {{id: row.id}})\n"
            f"SET n += row.properties"
        )
        connection.execute_write(query, {"rows": rows})


def _merge_relationships(connection: Neo4jConnection, relationships: list[RelationshipRecord]) -> None:
    by_shape: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for rel in relationships:
        key = (rel.start_label.value, rel.rel_type, rel.end_label.value)
        by_shape[key].append({"start_id": rel.start_id, "end_id": rel.end_id, "properties": dict(rel.properties)})

    for (start_label, rel_type, end_label), rows in by_shape.items():
        query = (
            f"UNWIND $rows AS row\n"
            f"MATCH (a:`{start_label}` {{id: row.start_id}})\n"
            f"MATCH (b:`{end_label}` {{id: row.end_id}})\n"
            f"MERGE (a)-[r:`{rel_type}`]->(b)\n"
            f"SET r += row.properties"
        )
        connection.execute_write(query, {"rows": rows})
