"""Accumulates extracted facts into a snapshot, merging duplicates.

The same fact is often found more than once (two manifests listing one
library, a route declared twice). Duplicates collapse into one entity /
relationship whose evidence is the union of every place it was found --
never a double count.
"""

from __future__ import annotations

from datetime import datetime

from .models import (
    ArchEntity,
    ArchitectureEvidence,
    ArchitectureSnapshot,
    ArchRelationship,
    UnresolvedItem,
)

__all__ = ["SnapshotBuilder"]


def _merge_evidence(into: list[ArchitectureEvidence], more: list[ArchitectureEvidence]) -> None:
    seen = {e.dedupe_key() for e in into}
    for item in more:
        if item.dedupe_key() not in seen:
            seen.add(item.dedupe_key())
            into.append(item)


def _merge_properties(into: dict[str, str], more: dict[str, str]) -> None:
    for name, value in more.items():
        if name not in into or into[name] == value:
            into[name] = value
        else:  # conflicting declarations: keep every distinct value, deterministically
            values = sorted(set(into[name].split(", ")) | {value})
            into[name] = ", ".join(values)


class SnapshotBuilder:
    def __init__(
        self, repository: str, revision: str, committed_at: datetime | None = None
    ) -> None:
        self.snapshot = ArchitectureSnapshot(
            repository=repository, revision=revision, committed_at=committed_at
        )

    def add_entity(self, entity: ArchEntity) -> ArchEntity:
        existing = self.snapshot.entities.get(entity.key)
        if existing is None:
            self.snapshot.entities[entity.key] = entity
            return entity
        _merge_properties(existing.properties, entity.properties)
        for name, value in entity.meta.items():
            existing.meta.setdefault(name, value)
        _merge_evidence(existing.evidence, entity.evidence)
        return existing

    def add_relationship(self, relationship: ArchRelationship) -> ArchRelationship:
        for end in (relationship.source, relationship.target):
            if end not in self.snapshot.entities:
                raise KeyError(f"relationship refers to unknown entity {end!r}")
        existing = self.snapshot.relationships.get(relationship.key)
        if existing is None:
            self.snapshot.relationships[relationship.key] = relationship
            return relationship
        _merge_properties(existing.properties, relationship.properties)
        _merge_evidence(existing.evidence, relationship.evidence)
        return existing

    def unresolved(self, reason: str, file: str | None = None) -> None:
        self.snapshot.unresolved.append(
            UnresolvedItem(reason=reason, file=file, revision=self.snapshot.revision)
        )

    def build(self) -> ArchitectureSnapshot:
        self.snapshot.unresolved.sort(key=lambda u: (u.file or "", u.reason))
        return self.snapshot
