"""Data models for Phase 5: the deterministic Impact Analysis Engine.

Answers "if I change this code, what else could be affected?" using only
relationships that actually exist in the Phase 2 graph. Nothing here is
produced by an LLM, and nothing here scores or ranks impact ("high/medium/
low") -- there is no such deterministic rule, so there is no such field.

Reuses Phase 3's :class:`~src.rca.models.ResolutionStatus` for the overall
status rather than inventing a parallel one, and is built to consume a
Phase 4 :class:`~src.fix_localization.models.FixLocalizationResult` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.rca.models import ResolutionStatus

__all__ = [
    "ResolutionStatus",
    "ImpactCategory",
    "GraphRelationship",
    "PathDirection",
    "KnowledgeStatus",
    "Directness",
    "ImpactItem",
    "PathStep",
    "ImpactPath",
    "UnresolvedItem",
    "ImpactAnalysisResult",
]


class ImpactCategory(str, Enum):
    """What kind of thing an impact item is."""

    PRIMARY_CHANGE = "PRIMARY_CHANGE"
    DIRECT_CALLER = "DIRECT_CALLER"
    INDIRECT_CALLER = "INDIRECT_CALLER"
    CLASS = "CLASS"
    FILE = "FILE"
    SERVICE = "SERVICE"
    API = "API"
    EVENT = "EVENT"
    DATABASE = "DATABASE"
    TABLE = "TABLE"
    DOWNSTREAM_SERVICE = "DOWNSTREAM_SERVICE"
    TEST = "TEST"


class GraphRelationship(str, Enum):
    """Every graph relationship type this engine reads, in one place.

    Deliberately separate from ``src.parser.models.RelationshipType``: that
    enum doubles as Phase 2's "populated relationship types" set, and these
    architecture-level relationships (EXPOSES, PUBLISHES, ...) are *not*
    populated by the current ingestion pipeline -- adding them there would
    make the schema claim data that doesn't exist.

    ``NAMING_CONVENTION`` is not a graph edge: it marks a test link found by
    Phase 4's deterministic test-file naming rule, so a consumer can tell it
    apart from a real graph relationship.
    """

    CALLS = "CALLS"
    CONTAINS = "CONTAINS"
    EXPOSES = "EXPOSES"
    PUBLISHES = "PUBLISHES"
    CONSUMES = "CONSUMES"
    ACCESSES = "ACCESSES"
    DEPENDS_ON = "DEPENDS_ON"
    VALIDATES = "VALIDATES"
    NAMING_CONVENTION = "NAMING_CONVENTION"


class PathDirection(str, Enum):
    """Which way a path step followed the stored edge.

    FORWARD -- from the edge's start node to its end node (e.g. Service
               EXPOSES API, walked Service -> API).
    REVERSE -- against the stored direction (e.g. process_payment CALLS
               validate_payment, walked validate_payment -> process_payment,
               which is how a *caller* is found). Relationship names are
               always reported exactly as stored; direction carries the rest.
    """

    FORWARD = "FORWARD"
    REVERSE = "REVERSE"


class KnowledgeStatus(str, Enum):
    """Whether the graph holds the data needed to answer a category at all.

    KNOWN      -- the relevant relationships exist in the graph, so an empty
                  list for this category really means "nothing is connected".
    UNRESOLVED -- the graph has no such relationships, so an empty list means
                  "cannot tell", not "nothing is affected".
    """

    KNOWN = "KNOWN"
    UNRESOLVED = "UNRESOLVED"


class Directness(str, Enum):
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"


@dataclass
class ImpactItem:
    """One impacted element, with the identifiers a consumer needs."""

    id: str
    name: str
    category: ImpactCategory
    node_label: str | None  # graph label: Method / Function / Class / File / Service / API / ...
    depth: int  # edges from the primary change along the shortest known path (primary = 0)
    relationship: str | None = None  # relationship that connects it to the step before it
    repository: str | None = None
    file: str | None = None
    class_name: str | None = None  # None for a top-level function / non-code element
    method: str | None = None  # method or function name for code elements
    qualified_name: str | None = None  # e.g. "PaymentService.process_payment"
    directness: Directness | None = None  # set for downstream services
    detail: dict[str, str] = field(default_factory=dict)  # e.g. {"event_role": "PUBLISHES"}


@dataclass
class PathStep:
    """One node on an impact path, plus how the path got there."""

    node_id: str
    name: str
    category: ImpactCategory
    node_label: str | None = None
    relationship: str | None = None  # None for the first step (the primary change)
    direction: PathDirection | None = None


@dataclass
class ImpactPath:
    """The deterministic chain of graph relationships that put one element
    into the impact result -- the evidence for *why* it is impacted."""

    terminal_id: str
    category: ImpactCategory
    steps: list[PathStep] = field(default_factory=list)

    @property
    def chain(self) -> str:
        """Compact form: ``validate_payment → process_payment → POST /checkout``."""
        return " → ".join(step.name for step in self.steps)

    @property
    def detailed(self) -> str:
        """Form that keeps relationship names and directions exactly as stored."""
        parts: list[str] = []
        for step in self.steps:
            if step.relationship is None:
                parts.append(step.name)
            elif step.direction == PathDirection.REVERSE:
                parts.append(f"<-[{step.relationship}]- {step.name}")
            elif step.direction == PathDirection.FORWARD:
                parts.append(f"-[{step.relationship}]-> {step.name}")
            else:
                parts.append(f"~[{step.relationship}]~ {step.name}")
        return " ".join(parts)


@dataclass
class UnresolvedItem:
    """Something the graph could not tell us -- reported, never guessed."""

    category: ImpactCategory
    reason: str


@dataclass
class ImpactAnalysisResult:
    """Everything the graph establishes about the blast radius of one change."""

    resolution_status: ResolutionStatus
    max_depth: int
    primary_change: ImpactItem | None = None
    callers: list[ImpactItem] = field(default_factory=list)
    classes: list[ImpactItem] = field(default_factory=list)
    files: list[ImpactItem] = field(default_factory=list)
    services: list[ImpactItem] = field(default_factory=list)
    apis: list[ImpactItem] = field(default_factory=list)
    events: list[ImpactItem] = field(default_factory=list)
    databases: list[ImpactItem] = field(default_factory=list)
    tables: list[ImpactItem] = field(default_factory=list)
    downstream_services: list[ImpactItem] = field(default_factory=list)
    tests: list[ImpactItem] = field(default_factory=list)
    impact_paths: list[ImpactPath] = field(default_factory=list)
    unresolved_items: list[UnresolvedItem] = field(default_factory=list)
    category_status: dict[ImpactCategory, KnowledgeStatus] = field(default_factory=dict)

    @property
    def direct_callers(self) -> list[ImpactItem]:
        return [c for c in self.callers if c.category == ImpactCategory.DIRECT_CALLER]

    @property
    def indirect_callers(self) -> list[ImpactItem]:
        return [c for c in self.callers if c.category == ImpactCategory.INDIRECT_CALLER]

    def paths_to(self, item_id: str) -> list[ImpactPath]:
        """Every preserved path that ends at ``item_id``."""
        return [p for p in self.impact_paths if p.terminal_id == item_id]
