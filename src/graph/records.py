"""Plain records produced by the graph mapper and consumed by the ingestion service.

These are the "clean separation" boundary requirement 10 asks for: the
mapper turns Phase 1's structured facts into these plain, DB-agnostic
records, and only the ingestion service (`src.graph.ingestion`) knows how to
turn them into actual Cypher. Neither the parser nor the mapper talks to
Neo4j directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import NodeLabel

__all__ = ["NodeRecord", "RelationshipRecord", "GraphBatch"]


@dataclass(frozen=True)
class NodeRecord:
    """One node to MERGE into the graph, identified by its stable `id`."""

    label: NodeLabel
    id: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RelationshipRecord:
    """One relationship to MERGE into the graph, between two nodes identified by their stable ids."""

    start_label: NodeLabel
    start_id: str
    rel_type: str
    end_label: NodeLabel
    end_id: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphBatch:
    """Everything the mapper produced for one ingestion run, ready for `src.graph.ingestion`."""

    nodes: list[NodeRecord] = field(default_factory=list)
    relationships: list[RelationshipRecord] = field(default_factory=list)
