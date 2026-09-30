"""Top-level RCA orchestration -- Phase 3, requirement 13, and the
architectural rule the spec lays out:

    Input -> Parser -> Neo4j -> Git -> Deterministic Traversal -> Evidence

Explicitly NOT:

    Input -> LLM -> "Probably the root cause is..."

``investigate()`` is the single entry point: given an :class:`~src.rca.models.RCAInput`,
a live Neo4j connection, and the repository's local checkout path (for Git
operations), it runs every deterministic step above and returns a
structured :class:`~src.rca.models.RCAResult` -- evidence and facts only,
never a "root_cause" conclusion. That reasoning step is explicitly left to
Person 2's later LLM agent (see ``src.rca.facts_provider``).
"""

from __future__ import annotations

from pathlib import Path

from src.graph.connection import Neo4jConnection

from . import evidence as evidence_builders
from .call_graph import DEFAULT_MAX_DEPTH, traverse_callees, traverse_callers
from .code_context import build_code_context
from .code_resolution import resolve_stack_frame
from .dependencies import traverse_dependencies
from .git_investigation import investigate_blame, investigate_git_history
from .log_correlation import correlate_logs
from .models import (
    CodeContext,
    CodeLocation,
    Evidence,
    RCAInput,
    RCAResult,
    ResolutionStatus,
)
from .stack_trace import parse_stack_trace

__all__ = ["investigate"]


def investigate(
    rca_input: RCAInput,
    *,
    connection: Neo4jConnection,
    repo_root: str | Path,
    call_graph_depth: int = DEFAULT_MAX_DEPTH,
) -> RCAResult:
    """Run the full deterministic RCA pipeline for one reported failure."""
    all_evidence: list[Evidence] = []

    # --- 1 & 2: input already structured; parse the stack trace ----------
    parsed = parse_stack_trace(rca_input.stack_trace)
    if not parsed.exception_type and rca_input.exception_type:
        parsed.exception_type = rca_input.exception_type
    if not parsed.exception_message and rca_input.error_message:
        parsed.exception_message = rca_input.error_message
    all_evidence.extend(evidence_builders.stack_trace_evidence(parsed, rca_input.repository))

    # --- 3: exception -> code location (first frame is where it happened) -
    location = CodeLocation(
        resolution_status=ResolutionStatus.UNRESOLVED,
        repository=rca_input.repository,
        reason="No stack trace frames were available to resolve",
    )
    if parsed.frames:
        location = resolve_stack_frame(connection, rca_input.repository, parsed.frames[0])
    all_evidence.extend(evidence_builders.code_location_evidence(location))

    # --- 4: code context ---------------------------------------------------
    code_context: CodeContext | None = None
    if location.resolution_status != ResolutionStatus.UNRESOLVED:
        code_context = build_code_context(repo_root, location)

    # --- 5 & 6: call graph (forward + reverse) -----------------------------
    call_graph_forward = None
    call_graph_reverse = None
    if location.node_id and location.node_label in ("Method", "Function"):
        call_graph_forward = traverse_callees(connection, location.node_id, max_depth=call_graph_depth)
        call_graph_reverse = traverse_callers(connection, location.node_id, max_depth=call_graph_depth)
        all_evidence.extend(evidence_builders.call_graph_evidence(call_graph_forward))
        all_evidence.extend(evidence_builders.call_graph_evidence(call_graph_reverse))

    # --- 7: dependency traversal --------------------------------------------
    dependencies = None
    if location.file_id:
        dependencies = traverse_dependencies(connection, location.file_id)
        all_evidence.extend(evidence_builders.dependency_evidence(dependencies))

    # --- 8, 9 & 10: git history, recent changes, blame ----------------------
    git_history = None
    blame = None
    if location.file_path:
        git_history = investigate_git_history(
            repo_root, location.file_path, failure_timestamp=rca_input.timestamp
        )
        all_evidence.extend(evidence_builders.git_history_evidence(git_history))
        if location.line:
            blame = investigate_blame(repo_root, location.file_path, location.line)
            all_evidence.extend(evidence_builders.blame_evidence(blame))

    # --- 11: log correlation ------------------------------------------------
    log_correlations = []
    if rca_input.logs:
        log_correlations = correlate_logs(
            rca_input.logs,
            stack_trace=parsed,
            location=location,
            failure_timestamp=rca_input.timestamp,
        )
        all_evidence.extend(evidence_builders.log_evidence(log_correlations))

    return RCAResult(
        input=rca_input,
        stack_trace=parsed,
        location=location,
        code_context=code_context,
        call_graph_forward=call_graph_forward,
        call_graph_reverse=call_graph_reverse,
        dependencies=dependencies,
        git_history=git_history,
        blame=blame,
        log_correlations=log_correlations,
        evidence=all_evidence,
    )
