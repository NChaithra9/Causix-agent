"""Multiple Candidate Locations -- Phase 4, requirement 13.

Builds the PRIMARY location (the failure site itself -- the first parsed
stack frame) and any RELATED locations (subsequent frames in the same
traceback -- callers, per the spec's own example: ``validator.py:18``
primary, ``service.py:42`` related). The distinction is always driven by
deterministic evidence (which frame it came from) -- never a subjective
"best guess" ranking, and no useful, resolvable frame is silently dropped.
"""

from __future__ import annotations

from pathlib import Path

from src.graph.connection import Neo4jConnection
from src.rca.code_resolution import resolve_stack_frame
from src.rca.models import ParsedStackTrace, ResolutionStatus

from .hierarchy import localize_class_and_method
from .models import CandidateLocation, LocationRole
from .relevant_code import extract_relevant_code
from .repository_identity import get_repository_identity

__all__ = ["build_candidate_locations"]


def build_candidate_locations(
    connection: Neo4jConnection,
    repo_root: str | Path,
    repository: str | None,
    stack_trace: ParsedStackTrace | None,
) -> tuple[CandidateLocation | None, list[CandidateLocation]]:
    """Returns ``(primary, related)``. ``primary`` is ``None`` only when the
    first frame itself is unresolved (no location to build anything from)."""
    if stack_trace is None or not stack_trace.frames:
        return None, []

    primary_frame = stack_trace.frames[0]
    primary_location = resolve_stack_frame(connection, repository, primary_frame)
    primary = _build_candidate(
        connection, repo_root, primary_location, LocationRole.PRIMARY, "Stack trace frame 1 (the failing frame)"
    )
    if primary is None:
        return None, []

    related: list[CandidateLocation] = []
    seen_node_ids = {primary_location.node_id} if primary_location.node_id else set()
    for index, frame in enumerate(stack_trace.frames[1:], start=2):
        location = resolve_stack_frame(connection, repository, frame)
        if location.resolution_status == ResolutionStatus.UNRESOLVED:
            continue
        if location.node_id and location.node_id in seen_node_ids:
            continue
        candidate = _build_candidate(
            connection, repo_root, location, LocationRole.RELATED, f"Stack trace frame {index} (caller)"
        )
        if candidate is not None:
            related.append(candidate)
            if location.node_id:
                seen_node_ids.add(location.node_id)

    return primary, related


def _build_candidate(connection, repo_root, location, role, reason) -> CandidateLocation | None:
    if location.resolution_status == ResolutionStatus.UNRESOLVED:
        return None

    repository_identity = get_repository_identity(connection, location.repository)
    class_localization, method_localization = localize_class_and_method(repo_root, location)
    relevant_code = extract_relevant_code(repo_root, class_localization, method_localization)

    return CandidateLocation(
        role=role,
        location=location,
        reason=reason,
        repository_identity=repository_identity,
        class_localization=class_localization,
        method_localization=method_localization,
        relevant_code=relevant_code,
    )
