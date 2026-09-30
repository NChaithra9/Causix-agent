"""Data models for Phase 4: deterministic Exact Fix Localization.

Extends Phase 3's models rather than duplicating them: a
:class:`FixLocalizationResult` is built directly on top of one
:class:`~src.rca.models.RCAResult` (its ``location``, ``code_context``,
``call_graph_*``, ``git_history``, ``blame`` and ``evidence`` are all reused
as-is), and every new type here composes :class:`~src.rca.models.CodeLocation`,
:class:`~src.rca.models.ResolutionStatus` and :class:`~src.rca.models.Evidence`
directly rather than inventing parallel ones.

Person 1 still only ever produces facts here -- there is no "recommended
fix" field anywhere in this module. That synthesis is Person 2's later job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.rca.models import CodeContext, CodeLocation, Evidence, RCAResult, ResolutionStatus

__all__ = [
    "ResolutionStatus",
    "LocationRole",
    "RepositoryIdentity",
    "ClassLocalization",
    "MethodLocalization",
    "RelevantCode",
    "CandidateLocation",
    "HistoricalChange",
    "RelatedTest",
    "FixLocalizationResult",
]


class LocationRole(str, Enum):
    """Whether a candidate location is the failure site itself, or context
    around it (a caller, another frame) -- never a subjective "best" score."""

    PRIMARY = "PRIMARY"
    RELATED = "RELATED"


@dataclass
class RepositoryIdentity:
    """Which real Repository node this location's file belongs to."""

    resolution_status: ResolutionStatus
    name: str | None = None
    repository_id: str | None = None
    remote_url: str | None = None
    root_path: str | None = None


@dataclass
class ClassLocalization:
    """The class (if any) that owns the localized method/function."""

    resolution_status: ResolutionStatus
    name: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class MethodLocalization:
    """The exact function/method the failure/location resolves to."""

    resolution_status: ResolutionStatus
    name: str | None = None  # bare name, e.g. "validate_payment"
    qualified_name: str | None = None  # e.g. "PaymentValidator.validate_payment"
    kind: str | None = None  # "Method" | "Function" | "Class" (class-only location)
    class_name: str | None = None  # None for a top-level function
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    failing_line: int | None = None


@dataclass
class RelevantCode:
    """The actual source code for the localized method/function -- never
    generated, never modified, and never the whole file when only a method
    is relevant."""

    resolution_status: ResolutionStatus
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    lines: list[str] = field(default_factory=list)


@dataclass
class CandidateLocation:
    """One location this investigation points to -- the failure site itself
    (PRIMARY) or context around it (RELATED, e.g. a caller). The distinction
    is always based on deterministic evidence (which stack frame it came
    from, or a real CALLS edge) -- never a subjective ranking."""

    role: LocationRole
    location: CodeLocation
    reason: str
    repository_identity: RepositoryIdentity | None = None
    class_localization: ClassLocalization | None = None
    method_localization: MethodLocalization | None = None
    relevant_code: RelevantCode | None = None


@dataclass(frozen=True)
class HistoricalChange:
    """One commit related to the localized file/method.

    ``label`` is always a factual, non-causal description of *what kind* of
    relationship this commit has, never a claim about intent:
        "related_commit"    -- the commit git blame attributes the failing
                                line to (a direct, current-code fact).
        "previous_change"   -- a commit that touched this file/method before
                                the failure timestamp (temporal correlation
                                only, never "caused").
        "historical_change" -- any other commit that touched this file/method.
    """

    label: str
    commit_hash: str
    file_path: str
    author_name: str
    committed_at: datetime
    message: str
    method: str | None = None


@dataclass(frozen=True)
class RelatedTest:
    """One test deterministically linked to the localized method/function,
    by an existing Phase 1/2 graph relationship or by a reliable naming/path
    convention -- never guessed."""

    test_file: str
    test_function: str
    target: str


@dataclass
class FixLocalizationResult:
    """The full Phase 4 output: everything Person 2 needs to know *where*
    to make a change, and what deterministic evidence supports that -- never
    what the change should be."""

    resolution_status: ResolutionStatus
    rca: RCAResult
    primary_location: CandidateLocation | None = None
    related_locations: list[CandidateLocation] = field(default_factory=list)
    historical_changes: list[HistoricalChange] = field(default_factory=list)
    related_tests: list[RelatedTest] = field(default_factory=list)
    related_commits: list[str] = field(default_factory=list)
    related_prs: list[str] = field(default_factory=list)
    related_jira: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
