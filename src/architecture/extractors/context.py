"""Shared helpers for the extractors."""

from __future__ import annotations

from dataclasses import dataclass

from src.rca.models import EvidenceStatus

from ..builder import SnapshotBuilder
from ..models import (
    ArchEntity,
    ArchitectureEvidence,
    ArchRelationship,
    EntityType,
    GraphRelationship,
    entity_key,
)

__all__ = ["ExtractionContext", "add_relationship", "ensure_entity", "make_evidence"]


@dataclass(frozen=True)
class ExtractionContext:
    repository: str
    revision: str
    service: str  # the service this repository is
    known_services: frozenset[str] = frozenset()  # names an outbound call may be attributed to


def make_evidence(
    ctx: ExtractionContext,
    source: str,
    details: str,
    *,
    file: str | None = None,
    line: int | None = None,
    relationship: GraphRelationship | None = None,
) -> ArchitectureEvidence:
    return ArchitectureEvidence(
        source=source,
        status=EvidenceStatus.FACT,
        details=details,
        repository=ctx.repository,
        file=file,
        line=line,
        commit=ctx.revision,
        revision=ctx.revision,
        relationship=relationship.value if relationship else None,
    )


def ensure_entity(
    builder: SnapshotBuilder,
    entity_type: EntityType,
    name: str,
    evidence: ArchitectureEvidence,
    *,
    properties: dict[str, str] | None = None,
    meta: dict[str, str] | None = None,
    key: str | None = None,
) -> ArchEntity:
    return builder.add_entity(
        ArchEntity(
            key=key or entity_key(entity_type, name),
            entity_type=entity_type,
            name=name,
            properties=dict(properties or {}),
            meta=dict(meta or {}),
            evidence=[evidence],
        )
    )


def add_relationship(
    builder: SnapshotBuilder,
    rel_type: GraphRelationship,
    source: ArchEntity,
    target: ArchEntity,
    evidence: ArchitectureEvidence,
    properties: dict[str, str] | None = None,
) -> None:
    builder.add_relationship(
        ArchRelationship(
            rel_type=rel_type,
            source=source.key,
            target=target.key,
            properties=dict(properties or {}),
            evidence=[evidence],
        )
    )
