"""Phase 7 data models: architecture snapshots and the changes between them.

A snapshot is the set of architecture *facts* (services, APIs, events,
databases, tables, dependencies and the relationships between them) that
deterministic extraction established at one exact revision. A change is a
fact that was added, removed or modified between two snapshots, with the
evidence that supports it.

Reused rather than duplicated: ``src.rca.models.Evidence`` (the project's
evidence shape) and ``src.impact_analysis.models.GraphRelationship`` (the
architecture relationship vocabulary). Nothing here interprets what a
change *means*.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from src.git_history.models import FileChange
from src.impact_analysis.models import GraphRelationship
from src.rca.models import Evidence

__all__ = [
    "ArchEntity",
    "ArchRelationship",
    "ArchitectureChange",
    "ArchitectureChangeResult",
    "ArchitectureChangeStatus",
    "ArchitectureEvidence",
    "ArchitectureSnapshot",
    "ChangeCategory",
    "ChangeSummary",
    "ChangeType",
    "EntityType",
    "FileChange",
    "RelationshipKey",
    "UnresolvedItem",
    "api_key",
    "entity_key",
]


class EntityType(str, Enum):
    SERVICE = "SERVICE"
    API = "API"
    EVENT = "EVENT"
    DATABASE = "DATABASE"
    TABLE = "TABLE"
    DEPENDENCY = "DEPENDENCY"
    RELATIONSHIP = "RELATIONSHIP"  # only used on changes, never as a snapshot entity


class ChangeType(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"


class ChangeCategory(str, Enum):
    """The specific kind of architecture change (``change_type`` carries the direction)."""

    SERVICE_ADDED = "SERVICE_ADDED"
    SERVICE_REMOVED = "SERVICE_REMOVED"
    SERVICE_RELATIONSHIP_ADDED = "SERVICE_RELATIONSHIP_ADDED"  # service -> service
    SERVICE_RELATIONSHIP_REMOVED = "SERVICE_RELATIONSHIP_REMOVED"
    API_ADDED = "API_ADDED"
    API_REMOVED = "API_REMOVED"
    API_METHOD_CHANGED = "API_METHOD_CHANGED"
    API_PATH_CHANGED = "API_PATH_CHANGED"
    API_RELATIONSHIP_CHANGED = "API_RELATIONSHIP_CHANGED"  # who EXPOSES an existing API
    EVENT_ADDED = "EVENT_ADDED"
    EVENT_REMOVED = "EVENT_REMOVED"
    EVENT_PUBLISHER_CHANGED = "EVENT_PUBLISHER_CHANGED"  # a PUBLISHES edge added/removed
    EVENT_CONSUMER_CHANGED = "EVENT_CONSUMER_CHANGED"  # a CONSUMES edge added/removed
    DATABASE_ADDED = "DATABASE_ADDED"
    DATABASE_REMOVED = "DATABASE_REMOVED"
    TABLE_ADDED = "TABLE_ADDED"
    TABLE_REMOVED = "TABLE_REMOVED"
    DATABASE_ACCESS_ADDED = "DATABASE_ACCESS_ADDED"  # ACCESSES a database or table
    DATABASE_ACCESS_REMOVED = "DATABASE_ACCESS_REMOVED"
    DEPENDENCY_ADDED = "DEPENDENCY_ADDED"  # service -> library
    DEPENDENCY_REMOVED = "DEPENDENCY_REMOVED"
    DEPENDENCY_VERSION_CHANGED = "DEPENDENCY_VERSION_CHANGED"
    RELATIONSHIP_ADDED = "RELATIONSHIP_ADDED"  # any other architecture relationship
    RELATIONSHIP_REMOVED = "RELATIONSHIP_REMOVED"
    RELATIONSHIP_MODIFIED = "RELATIONSHIP_MODIFIED"
    ENTITY_MODIFIED = "ENTITY_MODIFIED"


class ArchitectureChangeStatus(str, Enum):
    NO_CHANGES = "NO_CHANGES"
    CHANGES_DETECTED = "CHANGES_DETECTED"
    UNRESOLVED = "UNRESOLVED"  # the comparison could not be made (see unresolved_items)


@dataclass
class ArchitectureEvidence(Evidence):
    """The project's ``Evidence`` plus the revision and graph relationship it supports."""

    type: str = field(default="architecture", init=False)
    revision: str | None = None
    relationship: str | None = None

    def dedupe_key(self) -> tuple:
        return (self.source, self.file, self.line, self.details, self.revision, self.relationship)


def entity_key(entity_type: EntityType, name: str) -> str:
    return f"{entity_type.value.lower()}:{name}"


def api_key(method: str, path: str) -> str:
    """An API is identified by its HTTP method and declared path; the exposing
    service is the source of its EXPOSES relationship."""
    return f"api:{method.upper()} {path}"


@dataclass
class ArchEntity:
    """One architecture entity in a snapshot.

    ``properties`` are the structural attributes that count as a change when
    they differ. ``meta`` is supporting detail (e.g. an API's handler) that is
    kept for evidence and correlation but never reported as a change itself.
    """

    key: str
    entity_type: EntityType
    name: str
    properties: dict[str, str] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)
    evidence: list[ArchitectureEvidence] = field(default_factory=list)


RelationshipKey = tuple[str, str, str]  # (relationship type, source key, target key)


@dataclass
class ArchRelationship:
    rel_type: GraphRelationship
    source: str  # entity key
    target: str  # entity key
    properties: dict[str, str] = field(default_factory=dict)
    evidence: list[ArchitectureEvidence] = field(default_factory=list)

    @property
    def key(self) -> RelationshipKey:
        return (self.rel_type.value, self.source, self.target)


@dataclass
class UnresolvedItem:
    """Something that could not be established -- never silently dropped or guessed."""

    reason: str
    file: str | None = None
    revision: str | None = None


@dataclass
class ArchitectureSnapshot:
    """The architecture facts of one repository at one exact revision."""

    repository: str
    revision: str  # the exact commit hash
    committed_at: datetime | None = None
    entities: dict[str, ArchEntity] = field(default_factory=dict)
    relationships: dict[RelationshipKey, ArchRelationship] = field(default_factory=dict)
    unresolved: list[UnresolvedItem] = field(default_factory=list)

    def entities_of(self, entity_type: EntityType) -> list[ArchEntity]:
        return sorted(
            (e for e in self.entities.values() if e.entity_type is entity_type),
            key=lambda e: e.key,
        )

    @property
    def services(self) -> list[ArchEntity]:
        return self.entities_of(EntityType.SERVICE)

    @property
    def apis(self) -> list[ArchEntity]:
        return self.entities_of(EntityType.API)

    @property
    def events(self) -> list[ArchEntity]:
        return self.entities_of(EntityType.EVENT)

    @property
    def databases(self) -> list[ArchEntity]:
        return self.entities_of(EntityType.DATABASE)

    @property
    def tables(self) -> list[ArchEntity]:
        return self.entities_of(EntityType.TABLE)

    @property
    def dependencies(self) -> list[ArchEntity]:
        return self.entities_of(EntityType.DEPENDENCY)


# ----------------------------------------------------------------- the diff


@dataclass
class ArchitectureChange:
    change_type: ChangeType
    entity_type: EntityType
    category: ChangeCategory
    entity_id: str  # the entity key, or "TYPE:source->target" for a relationship
    name: str
    repository: str
    previous_revision: str
    current_revision: str
    source: str | None = None  # relationship source / owning service (display name)
    target: str | None = None  # relationship target (display name)
    previous_state: dict[str, str] | None = None
    current_state: dict[str, str] | None = None
    evidence: list[ArchitectureEvidence] = field(default_factory=list)


@dataclass
class ChangeSummary:
    total: int = 0
    by_change_type: dict[str, int] = field(default_factory=dict)
    by_entity_type: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)


@dataclass
class ArchitectureChangeResult:
    status: ArchitectureChangeStatus
    repository: str
    previous_revision: str
    current_revision: str
    changes: list[ArchitectureChange] = field(default_factory=list)
    summary: ChangeSummary = field(default_factory=ChangeSummary)
    evidence: list[ArchitectureEvidence] = field(default_factory=list)  # revision-level (git diff)
    changed_files: list[FileChange] = field(default_factory=list)
    unresolved_items: list[UnresolvedItem] = field(default_factory=list)

    def of_category(self, category: ChangeCategory) -> list[ArchitectureChange]:
        return [c for c in self.changes if c.category is category]

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form (enums as strings, datetimes as ISO text)."""
        return _plain(asdict(self))


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value
