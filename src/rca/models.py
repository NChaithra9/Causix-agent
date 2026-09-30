"""Data models for Phase 3: deterministic Root Cause Analysis (RCA).

Every model here is a plain dataclass -- there is no LLM anywhere in this
package. The RCA engine only ever produces *evidence* (things it can prove
from the stack trace, the Phase 1/2 code graph, and Git history) -- never a
prose "root cause" conclusion. That reasoning step belongs to Person 2's
later LLM agent; see ``src.reasoning.facts.FactsProvider``.

Resolution vocabulary (deliberately not a numeric confidence score -- the
project doesn't define one, and inventing one here would be exactly the
kind of guess this phase must avoid):

    ResolutionStatus  -- how completely one category of investigation
                         (code location, call graph, git history, ...)
                         could be carried out.
    EvidenceStatus    -- how strong a single piece of evidence is:
                         FACT                -- directly observed (parsed
                                                 stack trace, a real graph
                                                 node, an actual commit).
                         POTENTIALLY_RELEVANT -- correlated/contextual (a
                                                 commit that touched the
                                                 file before the failure,
                                                 a caller several hops
                                                 away) -- never a causal
                                                 claim.
                         UNRESOLVED          -- looked for, not found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

__all__ = [
    "ResolutionStatus",
    "EvidenceStatus",
    "RCAInput",
    "StackFrame",
    "ParsedStackTrace",
    "CodeLocation",
    "CodeContext",
    "CallGraphEdge",
    "CallGraphResult",
    "DependencyResult",
    "RecentChange",
    "GitHistoryResult",
    "BlameResult",
    "LogEntry",
    "LogCorrelation",
    "Evidence",
    "StackTraceEvidence",
    "CodeLocationEvidence",
    "CallGraphEvidence",
    "DependencyEvidence",
    "GitHistoryEvidence",
    "RecentChangeEvidence",
    "BlameEvidence",
    "LogEvidence",
    "RCAResult",
]


class ResolutionStatus(str, Enum):
    """How completely one investigation category resolved, for one RCA run."""

    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    UNRESOLVED = "UNRESOLVED"


class EvidenceStatus(str, Enum):
    """How strong a single piece of evidence is -- never a numeric score."""

    FACT = "FACT"
    POTENTIALLY_RELEVANT = "POTENTIALLY_RELEVANT"
    UNRESOLVED = "UNRESOLVED"


# ---------------------------------------------------------------------------
# 1. RCA Input Model
# ---------------------------------------------------------------------------


@dataclass
class RCAInput:
    """Everything an RCA investigation may be given. Every field is optional --
    the engine must degrade gracefully down to "just an exception type", per
    the Phase 3 spec's requirement 1."""

    issue_id: str | None = None
    error_message: str | None = None
    exception_type: str | None = None
    stack_trace: str | None = None
    logs: list[str] = field(default_factory=list)
    repository: str | None = None  # name, remote URL, or root path -- matched against Repository nodes
    commit: str | None = None
    deployment: str | None = None
    timestamp: datetime | None = None  # when the failure occurred, for recent-changes/log correlation


# ---------------------------------------------------------------------------
# 2. Stack Trace Parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StackFrame:
    """One deterministically parsed frame of a Python traceback."""

    file: str
    line: int
    function: str
    code_snippet: str | None = None  # the source line the traceback itself printed, if any


@dataclass
class ParsedStackTrace:
    """The full deterministic parse of a traceback string."""

    frames: list[StackFrame] = field(default_factory=list)
    exception_type: str | None = None
    exception_message: str | None = None
    raw: str | None = None


# ---------------------------------------------------------------------------
# 3 & 4. Exception -> Code Location, and Code Context
# ---------------------------------------------------------------------------


@dataclass
class CodeLocation:
    """Where a stack frame actually maps to in the ingested code graph.

    ``resolution_status`` is ``UNRESOLVED`` (with every other field left
    ``None``/empty) whenever the frame cannot be tied to a real node --
    this class never invents a location.
    """

    resolution_status: ResolutionStatus
    repository: str | None = None
    file_path: str | None = None
    file_id: str | None = None
    line: int | None = None
    node_id: str | None = None  # the resolved Method/Function/Class graph node id
    node_label: str | None = None  # "Method" | "Function" | "Class"
    qualified_name: str | None = None  # e.g. "RefundService.is_eligible_for_refund"
    reason: str | None = None  # set when UNRESOLVED/PARTIALLY_RESOLVED, explains why


@dataclass
class CodeContext:
    """Deterministic context around a resolved code location. Facts only --
    no explanation of what the code does."""

    resolution_status: ResolutionStatus
    containing_function: str | None = None
    containing_class: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    source_lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 5 & 6. Call Graph Traversal (forward + reverse)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallGraphEdge:
    """One CALLS edge found during traversal, with its distance from the start node."""

    depth: int
    source_id: str
    source_name: str
    source_label: str
    target_id: str
    target_name: str
    target_label: str


@dataclass
class CallGraphResult:
    """Result of a bounded, cycle-safe CALLS traversal from one starting node."""

    resolution_status: ResolutionStatus
    direction: str  # "forward" (callees) | "reverse" (callers)
    start_node_id: str | None = None
    start_name: str | None = None
    max_depth: int = 0
    edges: list[CallGraphEdge] = field(default_factory=list)
    visited_node_ids: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# 7. Dependency Traversal
# ---------------------------------------------------------------------------


@dataclass
class DependencyResult:
    """Files related to the failing file via IMPORTS edges already in the graph."""

    resolution_status: ResolutionStatus
    file_id: str | None = None
    file_path: str | None = None
    imports: list[str] = field(default_factory=list)  # files this file imports
    imported_by: list[str] = field(default_factory=list)  # files that import this file


# ---------------------------------------------------------------------------
# 8, 9 & 10. Git History, Recent Changes, Blame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecentChange:
    """One commit that touched the failing file before the failure timestamp.

    Deliberately descriptive, never causal: this is "commit X modified this
    file before the failure", not "commit X caused the bug" -- that
    conclusion belongs to Person 2's later reasoning step.
    """

    commit_hash: str
    author_name: str
    committed_at: datetime
    message: str


@dataclass
class GitHistoryResult:
    """Deterministic Git facts for one file."""

    resolution_status: ResolutionStatus
    file_path: str | None = None
    recent_commits: list[RecentChange] = field(default_factory=list)  # most recent first
    last_modifying_commit: RecentChange | None = None
    changes_before_failure: list[RecentChange] = field(default_factory=list)


@dataclass
class BlameResult:
    """Git blame for the failing line -- connects current code to its origin."""

    resolution_status: ResolutionStatus
    file_path: str | None = None
    line: int | None = None
    commit_hash: str | None = None
    author_name: str | None = None
    committed_at: datetime | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# 11. Log Correlation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogEntry:
    """One log line, with whatever structured fields could be deterministically extracted."""

    raw: str
    timestamp: datetime | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class LogCorrelation:
    """One log entry matched to the investigation by an explicit, named rule --
    never by semantic/LLM similarity."""

    entry: LogEntry
    matched_on: str  # e.g. "exception_type" | "file_path" | "timestamp_window" | "correlation_id"


# ---------------------------------------------------------------------------
# 12. Evidence Model
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """One fact the deterministic engine can stand behind. Base for every
    specific evidence type below -- keeps a single common shape (type,
    source, status, repository/file/line/method/commit/timestamp,
    human-readable details) so callers can treat a mixed list of evidence
    uniformly, while still knowing exactly which category each item is via
    its concrete subclass."""

    type: str
    source: str
    status: EvidenceStatus
    details: str
    repository: str | None = None
    file: str | None = None
    line: int | None = None
    method: str | None = None
    commit: str | None = None
    timestamp: datetime | None = None


@dataclass
class StackTraceEvidence(Evidence):
    type: str = field(default="stack_trace", init=False)


@dataclass
class CodeLocationEvidence(Evidence):
    type: str = field(default="code_location", init=False)


@dataclass
class CallGraphEvidence(Evidence):
    type: str = field(default="call_graph", init=False)


@dataclass
class DependencyEvidence(Evidence):
    type: str = field(default="dependency", init=False)


@dataclass
class GitHistoryEvidence(Evidence):
    type: str = field(default="git_history", init=False)


@dataclass
class RecentChangeEvidence(Evidence):
    type: str = field(default="recent_change", init=False)


@dataclass
class BlameEvidence(Evidence):
    type: str = field(default="blame", init=False)


@dataclass
class LogEvidence(Evidence):
    type: str = field(default="log", init=False)


# ---------------------------------------------------------------------------
# 13 & 14. Deterministic RCA Result
# ---------------------------------------------------------------------------


@dataclass
class RCAResult:
    """The final structured investigation result -- facts and evidence only.

    Never contains a ``root_cause`` field: this package proves things, it
    does not conclude things. Person 2's reasoning agent consumes
    ``evidence`` (numbered, in the order collected) to do that reasoning.
    """

    input: RCAInput
    stack_trace: ParsedStackTrace | None
    location: CodeLocation
    code_context: CodeContext | None
    call_graph_forward: CallGraphResult | None
    call_graph_reverse: CallGraphResult | None
    dependencies: DependencyResult | None
    git_history: GitHistoryResult | None
    blame: BlameResult | None
    log_correlations: list[LogCorrelation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
