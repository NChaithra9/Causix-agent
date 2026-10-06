"""The deterministic architecture diff.

Pure set arithmetic over two snapshots:

    added   = current  - previous
    removed = previous - current
    modified = same identity, different structural properties

No traversal, so cycles in the architecture are irrelevant, and no LLM,
embedding or heuristic scoring is involved. Duplicates were already merged
by the snapshot builder.

Two honesty rules apply on top of the arithmetic:

* A fact whose source file could not be analysed at one revision is
  *unknown* there, not absent. Such differences are dropped from the
  changes and reported as unresolved instead of as false add/remove noise.
* Snapshots of different repositories are never compared.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from src.git_history.models import CommitInfo, FileChange

from .extractors.context import ExtractionContext, make_evidence
from .models import (
    ArchEntity,
    ArchitectureChange,
    ArchitectureEvidence,
    ArchitectureSnapshot,
    ArchRelationship,
    ChangeCategory,
    ChangeSummary,
    ChangeType,
    EntityType,
    GraphRelationship,
    UnresolvedItem,
)

__all__ = ["ArchitectureDiffEngine", "DiffOutcome", "summarise"]

_MAX_COMMITS_PER_CHANGE = 3
_ENTITY_CATEGORY = {
    EntityType.SERVICE: (ChangeCategory.SERVICE_ADDED, ChangeCategory.SERVICE_REMOVED),
    EntityType.API: (ChangeCategory.API_ADDED, ChangeCategory.API_REMOVED),
    EntityType.EVENT: (ChangeCategory.EVENT_ADDED, ChangeCategory.EVENT_REMOVED),
    EntityType.DATABASE: (ChangeCategory.DATABASE_ADDED, ChangeCategory.DATABASE_REMOVED),
    EntityType.TABLE: (ChangeCategory.TABLE_ADDED, ChangeCategory.TABLE_REMOVED),
}
_ORDER = {t: i for i, t in enumerate(EntityType)}


@dataclass
class DiffOutcome:
    changes: list[ArchitectureChange] = field(default_factory=list)
    unresolved: list[UnresolvedItem] = field(default_factory=list)


def summarise(changes: Iterable[ArchitectureChange]) -> ChangeSummary:
    summary = ChangeSummary()
    for change in changes:
        summary.total += 1
        for counts, name in (
            (summary.by_change_type, change.change_type.value),
            (summary.by_entity_type, change.entity_type.value),
            (summary.by_category, change.category.value),
        ):
            counts[name] = counts.get(name, 0) + 1
    return summary


def _files(evidence: Iterable[ArchitectureEvidence]) -> list[str]:
    return sorted({e.file for e in evidence if e.file})


class ArchitectureDiffEngine:
    def __init__(
        self,
        previous: ArchitectureSnapshot,
        current: ArchitectureSnapshot,
        *,
        file_changes: Sequence[FileChange] = (),
        commits: Sequence[CommitInfo] = (),
    ) -> None:
        if previous.repository != current.repository:
            raise ValueError(
                "cannot compare snapshots of different repositories: "
                f"{previous.repository!r} vs {current.repository!r}"
            )
        self.previous = previous
        self.current = current
        self._file_changes = list(file_changes)
        self._commits = list(commits)
        self._blocked_prev = {u.file for u in previous.unresolved if u.file}
        self._blocked_cur = {u.file for u in current.unresolved if u.file}
        self._unresolved: dict[tuple[str, str], UnresolvedItem] = {}

    # ------------------------------------------------------------------ public

    def diff(self) -> DiffOutcome:
        prev, cur = self.previous, self.current
        changes: list[ArchitectureChange] = []

        removed = set(prev.entities) - set(cur.entities)
        added = set(cur.entities) - set(prev.entities)

        paired = self._pair_apis(
            [
                prev.entities[k]
                for k in sorted(removed)
                if prev.entities[k].entity_type is EntityType.API
            ],
            [
                cur.entities[k]
                for k in sorted(added)
                if cur.entities[k].entity_type is EntityType.API
            ],
        )
        for before, after, category in paired:
            removed.discard(before.key)
            added.discard(after.key)
            changes.append(self._api_modified(before, after, category))

        # A library is reported through its service -> library relationship, never alone.
        for key in sorted(added):
            if cur.entities[key].entity_type is not EntityType.DEPENDENCY:
                changes.append(self._entity_change(cur.entities[key], ChangeType.ADDED))
        for key in sorted(removed):
            if prev.entities[key].entity_type is not EntityType.DEPENDENCY:
                changes.append(self._entity_change(prev.entities[key], ChangeType.REMOVED))
        for key in sorted(set(prev.entities) & set(cur.entities)):
            before, after = prev.entities[key], cur.entities[key]
            if (
                before.properties != after.properties
                and before.entity_type is not EntityType.DEPENDENCY
            ):
                changes.append(self._entity_modified(before, after))

        # Entities whose own add/remove already tells the whole story: an API's
        # EXPOSES edge is implied by the API appearing/disappearing.
        implied = set(prev.entities) ^ set(cur.entities)
        for key in sorted(set(cur.relationships) - set(prev.relationships)):
            rel = cur.relationships[key]
            if not self._implied(rel, implied):
                changes.append(self._relationship_change(rel, ChangeType.ADDED))
        for key in sorted(set(prev.relationships) - set(cur.relationships)):
            rel = prev.relationships[key]
            if not self._implied(rel, implied):
                changes.append(self._relationship_change(rel, ChangeType.REMOVED))
        for key in sorted(set(prev.relationships) & set(cur.relationships)):
            before, after = prev.relationships[key], cur.relationships[key]
            if before.properties != after.properties:
                changes.append(self._relationship_modified(before, after))

        kept = [c for c in changes if not self._unknown_because_unresolved(c)]
        kept.sort(
            key=lambda c: (
                _ORDER[c.entity_type],
                c.category.value,
                c.entity_id,
                c.change_type.value,
            )
        )
        return DiffOutcome(
            changes=kept,
            unresolved=sorted(self._unresolved.values(), key=lambda u: (u.file or "", u.reason)),
        )

    # --------------------------------------------------------------- entities

    def _entity_change(self, entity: ArchEntity, change_type: ChangeType) -> ArchitectureChange:
        added = change_type is ChangeType.ADDED
        category = _ENTITY_CATEGORY[entity.entity_type][0 if added else 1]
        state = {**entity.properties, **{f"meta.{k}": v for k, v in entity.meta.items()}}
        return self._make(
            change_type,
            entity.entity_type,
            category,
            entity.key,
            entity.name,
            entity.evidence,
            current_state=state if added else None,
            previous_state=None if added else state,
        )

    def _entity_modified(self, before: ArchEntity, after: ArchEntity) -> ArchitectureChange:
        return self._make(
            ChangeType.MODIFIED,
            after.entity_type,
            ChangeCategory.ENTITY_MODIFIED,
            after.key,
            after.name,
            [*before.evidence, *after.evidence],
            previous_state=dict(before.properties),
            current_state=dict(after.properties),
        )

    def _pair_apis(
        self, removed: list[ArchEntity], added: list[ArchEntity]
    ) -> list[tuple[ArchEntity, ArchEntity, ChangeCategory]]:
        """Pair a removed and an added API when exactly one of each is served by the
        same handler and only the path (or only the method) differs."""

        def by_handler(entities: list[ArchEntity]) -> dict[str, list[ArchEntity]]:
            groups: dict[str, list[ArchEntity]] = {}
            for entity in entities:
                handler = entity.meta.get("handler")
                if handler:
                    groups.setdefault(f"{entity.meta.get('service', '')}::{handler}", []).append(
                        entity
                    )
            return groups

        before_groups, after_groups = by_handler(removed), by_handler(added)
        pairs = []
        for handler in sorted(set(before_groups) & set(after_groups)):
            if len(before_groups[handler]) != 1 or len(after_groups[handler]) != 1:
                continue  # ambiguous: report plain add/remove rather than guess
            before, after = before_groups[handler][0], after_groups[handler][0]
            same_method = before.properties["method"] == after.properties["method"]
            same_path = before.properties["path"] == after.properties["path"]
            if same_method and not same_path:
                pairs.append((before, after, ChangeCategory.API_PATH_CHANGED))
            elif same_path and not same_method:
                pairs.append((before, after, ChangeCategory.API_METHOD_CHANGED))
        return pairs

    def _api_modified(
        self, before: ArchEntity, after: ArchEntity, category: ChangeCategory
    ) -> ArchitectureChange:
        return self._make(
            ChangeType.MODIFIED,
            EntityType.API,
            category,
            after.key,
            f"{before.name} -> {after.name}",
            [*before.evidence, *after.evidence],
            previous_state={**before.properties, "handler": before.meta.get("handler", "")},
            current_state={**after.properties, "handler": after.meta.get("handler", "")},
        )

    # ----------------------------------------------------------- relationships

    def _implied(self, rel: ArchRelationship, implied_entities: set[str]) -> bool:
        return rel.rel_type is GraphRelationship.EXPOSES and rel.target in implied_entities

    def _entity(self, key: str, added: bool) -> ArchEntity:
        snapshot = self.current if added else self.previous
        return snapshot.entities[key]

    def _relationship_change(
        self, rel: ArchRelationship, change_type: ChangeType
    ) -> ArchitectureChange:
        added = change_type is ChangeType.ADDED
        source, target = self._entity(rel.source, added), self._entity(rel.target, added)
        category, entity_type = self._classify(rel.rel_type, source, target, added)
        state = {"relationship": rel.rel_type.value, **rel.properties}
        return self._make(
            change_type,
            entity_type,
            category,
            self._rel_id(rel),
            f"{source.name} -[{rel.rel_type.value}]-> {target.name}",
            rel.evidence,
            source=source.name,
            target=target.name,
            current_state=state if added else None,
            previous_state=None if added else state,
        )

    def _relationship_modified(
        self, before: ArchRelationship, after: ArchRelationship
    ) -> ArchitectureChange:
        source, target = self.current.entities[after.source], self.current.entities[after.target]
        library = (
            after.rel_type is GraphRelationship.DEPENDS_ON
            and target.entity_type is EntityType.DEPENDENCY
        )
        version_changed = before.properties.get("version") != after.properties.get("version")
        category = (
            ChangeCategory.DEPENDENCY_VERSION_CHANGED
            if library and version_changed
            else ChangeCategory.RELATIONSHIP_MODIFIED
        )
        return self._make(
            ChangeType.MODIFIED,
            EntityType.DEPENDENCY if library else EntityType.RELATIONSHIP,
            category,
            self._rel_id(after),
            f"{source.name} -[{after.rel_type.value}]-> {target.name}",
            [*before.evidence, *after.evidence],
            source=source.name,
            target=target.name,
            previous_state=dict(before.properties),
            current_state=dict(after.properties),
        )

    @staticmethod
    def _rel_id(rel: ArchRelationship) -> str:
        return f"{rel.rel_type.value}:{rel.source}->{rel.target}"

    @staticmethod
    def _classify(
        rel_type: GraphRelationship, source: ArchEntity, target: ArchEntity, added: bool
    ) -> tuple[ChangeCategory, EntityType]:
        direction = "ADDED" if added else "REMOVED"
        relationship = EntityType.RELATIONSHIP
        if rel_type is GraphRelationship.EXPOSES and target.entity_type is EntityType.API:
            return ChangeCategory.API_RELATIONSHIP_CHANGED, relationship
        if rel_type is GraphRelationship.PUBLISHES:
            return ChangeCategory.EVENT_PUBLISHER_CHANGED, relationship
        if rel_type is GraphRelationship.CONSUMES:
            return ChangeCategory.EVENT_CONSUMER_CHANGED, relationship
        if rel_type is GraphRelationship.ACCESSES and target.entity_type in (
            EntityType.DATABASE,
            EntityType.TABLE,
        ):
            return ChangeCategory(f"DATABASE_ACCESS_{direction}"), relationship
        if (
            source.entity_type is EntityType.SERVICE
            and target.entity_type is EntityType.SERVICE
            and rel_type in (GraphRelationship.CALLS, GraphRelationship.DEPENDS_ON)
        ):
            return ChangeCategory(f"SERVICE_RELATIONSHIP_{direction}"), relationship
        if rel_type is GraphRelationship.DEPENDS_ON and target.entity_type is EntityType.DEPENDENCY:
            return ChangeCategory(f"DEPENDENCY_{direction}"), EntityType.DEPENDENCY
        return ChangeCategory(f"RELATIONSHIP_{direction}"), relationship

    # ------------------------------------------------------------------ common

    def _make(
        self,
        change_type: ChangeType,
        entity_type: EntityType,
        category: ChangeCategory,
        entity_id: str,
        name: str,
        evidence: list[ArchitectureEvidence],
        *,
        source: str | None = None,
        target: str | None = None,
        previous_state: dict[str, str] | None = None,
        current_state: dict[str, str] | None = None,
    ) -> ArchitectureChange:
        change = ArchitectureChange(
            change_type=change_type,
            entity_type=entity_type,
            category=category,
            entity_id=entity_id,
            name=name,
            repository=self.current.repository,
            previous_revision=self.previous.revision,
            current_revision=self.current.revision,
            source=source,
            target=target,
            previous_state=previous_state,
            current_state=current_state,
            evidence=list(evidence),
        )
        change.evidence.extend(self._git_evidence(evidence))
        return change

    def _git_evidence(self, evidence: list[ArchitectureEvidence]) -> list[ArchitectureEvidence]:
        """Facts from ``git diff``/``git log`` about the files the evidence points to."""
        out: list[ArchitectureEvidence] = []
        ctx = ExtractionContext(self.current.repository, self.current.revision, "")
        by_path = {c.path: c for c in self._file_changes}
        by_old = {c.old_path: c for c in self._file_changes if c.old_path}
        wording = {"A": "added", "M": "modified", "D": "deleted"}
        for path in _files(evidence):
            change = by_path.get(path) or by_old.get(path)
            if change is not None:
                if change.status == "R":
                    detail = f"file renamed {change.old_path} -> {change.path}"
                else:
                    detail = f"file {wording.get(change.status, change.status)}"
                out.append(
                    make_evidence(
                        ctx,
                        "git_diff",
                        f"{detail} between {self.previous.revision[:12]} "
                        f"and {self.current.revision[:12]}",
                        file=path,
                    )
                )
            touching = [
                c
                for c in self._commits
                if path in c.changed_files or (change and change.path in c.changed_files)
            ]
            for commit in touching[:_MAX_COMMITS_PER_CHANGE]:
                item = make_evidence(
                    ctx,
                    "git_commit",
                    f"commit {commit.commit_hash[:12]} touched {path}: "
                    f"{commit.message.splitlines()[0] if commit.message else ''}",
                    file=path,
                )
                item.commit = commit.commit_hash
                item.timestamp = commit.committed_at
                out.append(item)
        return out

    def _unknown_because_unresolved(self, change: ArchitectureChange) -> bool:
        files = _files(change.evidence)
        for path in files:
            reason = None
            if change.change_type is ChangeType.ADDED and path in self._blocked_prev:
                reason = (self.previous.revision, "added")
            elif change.change_type is ChangeType.REMOVED and path in self._blocked_cur:
                reason = (self.current.revision, "removed")
            elif change.change_type is ChangeType.MODIFIED and (
                path in self._blocked_prev or path in self._blocked_cur
            ):
                reason = (self.current.revision, "modified")
            if reason:
                revision, what = reason
                self._unresolved[(path, change.entity_id)] = UnresolvedItem(
                    reason=f"{change.name!r} may have been {what}, but {path} "
                    "could not be analysed "
                    f"at revision {revision[:12]}",
                    file=path,
                    revision=revision,
                )
                return True
        return False
