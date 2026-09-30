"""Evidence Model construction -- Phase 3, requirement 12.

Turns the deterministic facts gathered by every other module in this
package into the numbered, structured ``Evidence`` list an
:class:`~src.rca.models.RCAResult` carries. No new facts are produced
here -- this module only *describes* facts already established elsewhere,
and assigns each one the correct :class:`~src.rca.models.EvidenceStatus`
(FACT / POTENTIALLY_RELEVANT / UNRESOLVED).
"""

from __future__ import annotations

from .models import (
    BlameEvidence,
    BlameResult,
    CallGraphEvidence,
    CallGraphResult,
    CodeLocation,
    CodeLocationEvidence,
    DependencyEvidence,
    DependencyResult,
    Evidence,
    EvidenceStatus,
    GitHistoryEvidence,
    GitHistoryResult,
    LogCorrelation,
    LogEvidence,
    ParsedStackTrace,
    RecentChangeEvidence,
    ResolutionStatus,
    StackTraceEvidence,
)

__all__ = [
    "stack_trace_evidence",
    "code_location_evidence",
    "call_graph_evidence",
    "dependency_evidence",
    "git_history_evidence",
    "blame_evidence",
    "log_evidence",
]


def stack_trace_evidence(stack_trace: ParsedStackTrace | None, repository: str | None) -> list[Evidence]:
    if stack_trace is None or (not stack_trace.frames and not stack_trace.exception_type):
        return []
    items: list[Evidence] = []
    if stack_trace.exception_type:
        message = f": {stack_trace.exception_message}" if stack_trace.exception_message else ""
        items.append(
            StackTraceEvidence(
                source="stack_trace",
                status=EvidenceStatus.FACT,
                details=f"Parsed exception {stack_trace.exception_type}{message}",
                repository=repository,
            )
        )
    for frame in stack_trace.frames:
        items.append(
            StackTraceEvidence(
                source="stack_trace",
                status=EvidenceStatus.FACT,
                details=f"Frame: {frame.file}:{frame.line} in {frame.function}"
                + (f" -- `{frame.code_snippet}`" if frame.code_snippet else ""),
                repository=repository,
                file=frame.file,
                line=frame.line,
                method=frame.function,
            )
        )
    return items


def code_location_evidence(location: CodeLocation) -> list[Evidence]:
    if location.resolution_status == ResolutionStatus.RESOLVED:
        return [
            CodeLocationEvidence(
                source="graph",
                status=EvidenceStatus.FACT,
                details=f"Resolved to {location.node_label} `{location.qualified_name}` "
                f"({location.file_path}:{location.line})",
                repository=location.repository,
                file=location.file_path,
                line=location.line,
                method=location.qualified_name,
            )
        ]
    if location.resolution_status == ResolutionStatus.PARTIALLY_RESOLVED:
        return [
            CodeLocationEvidence(
                source="graph",
                status=EvidenceStatus.POTENTIALLY_RELEVANT,
                details=f"File resolved ({location.file_path}) but the exact definition could not be pinned down"
                + (f" -- {location.reason}" if location.reason else ""),
                repository=location.repository,
                file=location.file_path,
                line=location.line,
            )
        ]
    return [
        CodeLocationEvidence(
            source="graph",
            status=EvidenceStatus.UNRESOLVED,
            details=location.reason or "Stack frame could not be resolved to a location in the code graph",
            repository=location.repository,
        )
    ]


def call_graph_evidence(result: CallGraphResult | None) -> list[Evidence]:
    if result is None or result.resolution_status == ResolutionStatus.UNRESOLVED:
        return []
    items: list[Evidence] = []
    verb = "calls" if result.direction == "forward" else "is called by"
    for edge in result.edges:
        near, far = (edge.source_name, edge.target_name) if result.direction == "forward" else (edge.target_name, edge.source_name)
        items.append(
            CallGraphEvidence(
                source="graph",
                status=EvidenceStatus.FACT if edge.depth == 1 else EvidenceStatus.POTENTIALLY_RELEVANT,
                details=f"(depth {edge.depth}) {near} {verb} {far}",
                method=edge.target_name if result.direction == "forward" else edge.source_name,
            )
        )
    return items


def dependency_evidence(result: DependencyResult | None) -> list[Evidence]:
    if result is None or result.resolution_status != ResolutionStatus.RESOLVED:
        return []
    items: list[Evidence] = []
    for dep in result.imports:
        items.append(
            DependencyEvidence(
                source="graph",
                status=EvidenceStatus.POTENTIALLY_RELEVANT,
                details=f"{result.file_path} imports {dep}",
                file=result.file_path,
            )
        )
    for dep in result.imported_by:
        items.append(
            DependencyEvidence(
                source="graph",
                status=EvidenceStatus.POTENTIALLY_RELEVANT,
                details=f"{dep} imports {result.file_path}",
                file=result.file_path,
            )
        )
    return items


def git_history_evidence(result: GitHistoryResult | None) -> list[Evidence]:
    if result is None or result.resolution_status == ResolutionStatus.UNRESOLVED:
        return []
    items: list[Evidence] = []
    if result.last_modifying_commit:
        c = result.last_modifying_commit
        items.append(
            GitHistoryEvidence(
                source="git",
                status=EvidenceStatus.FACT,
                details=f"Last commit to modify this file: {c.commit_hash[:12]} by {c.author_name}: {c.message}",
                file=result.file_path,
                commit=c.commit_hash,
                timestamp=c.committed_at,
            )
        )
    for c in result.changes_before_failure:
        items.append(
            RecentChangeEvidence(
                source="git",
                status=EvidenceStatus.POTENTIALLY_RELEVANT,
                details=f"Commit {c.commit_hash[:12]} modified this file before the failure "
                f"(does not by itself establish causation): {c.message}",
                file=result.file_path,
                commit=c.commit_hash,
                timestamp=c.committed_at,
            )
        )
    return items


def blame_evidence(result: BlameResult | None) -> list[Evidence]:
    if result is None or result.resolution_status == ResolutionStatus.UNRESOLVED:
        return []
    return [
        BlameEvidence(
            source="git",
            status=EvidenceStatus.FACT,
            details=f"Line {result.line} of {result.file_path} was last changed by commit "
            f"{result.commit_hash[:12]} ({result.author_name}): {result.message}",
            file=result.file_path,
            line=result.line,
            commit=result.commit_hash,
            timestamp=result.committed_at,
        )
    ]


def log_evidence(correlations: list[LogCorrelation]) -> list[Evidence]:
    items: list[Evidence] = []
    for correlation in correlations:
        items.append(
            LogEvidence(
                source="log",
                status=EvidenceStatus.POTENTIALLY_RELEVANT,
                details=f"Log line correlated via {correlation.matched_on}: {correlation.entry.raw}",
                timestamp=correlation.entry.timestamp,
            )
        )
    return items
