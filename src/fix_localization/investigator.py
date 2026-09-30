"""Top-level Phase 4 orchestration: the deterministic Exact Fix Localization
Engine.

    Phase 3 RCA result/evidence
            v
    Primary + related candidate locations   (candidate_locations.py)
            v
    Repository / Class / Method hierarchy   (repository_identity.py, hierarchy.py)
            v
    Relevant code                            (relevant_code.py)
            v
    Historical changes                       (historical.py)
            v
    Related tests                            (related_tests.py)
            v
    FixLocalizationResult (facts + evidence -- never a recommendation)

``localize_fix()`` is the single entry point. It takes an already-computed
Phase 3 :class:`~src.rca.models.RCAResult` -- it does not re-run the RCA
pipeline or duplicate its models, per the Phase 4 spec's explicit
instruction to reuse Phase 3's result/evidence models directly.
"""

from __future__ import annotations

from pathlib import Path

from src.graph.connection import Neo4jConnection
from src.rca.models import Evidence, RCAResult, ResolutionStatus

from . import evidence as evidence_builders
from .candidate_locations import build_candidate_locations
from .historical import historical_changes_for_location, method_scoped_commits
from .models import FixLocalizationResult
from .related_tests import find_related_tests

__all__ = ["localize_fix"]


def localize_fix(
    rca_result: RCAResult, *, connection: Neo4jConnection, repo_root: str | Path
) -> FixLocalizationResult:
    """Run the full deterministic fix-localization pipeline over one Phase 3
    RCA result."""
    all_evidence: list[Evidence] = list(rca_result.evidence)  # reuse Phase 3's evidence, don't rebuild it

    primary, related = build_candidate_locations(
        connection, repo_root, rca_result.input.repository, rca_result.stack_trace
    )

    if primary is None:
        return FixLocalizationResult(
            resolution_status=ResolutionStatus.UNRESOLVED,
            rca=rca_result,
            evidence=all_evidence,
        )

    for candidate in [primary, *related]:
        all_evidence.extend(evidence_builders.candidate_location_evidence(candidate))
        all_evidence.extend(evidence_builders.relevant_code_evidence(candidate))

    # --- Historical changes: file-level reused directly from Phase 3's own
    # git_history/blame for the primary location; method-scoped is new here.
    historical_changes = historical_changes_for_location(rca_result.git_history, rca_result.blame)
    historical_changes.extend(
        c
        for c in method_scoped_commits(repo_root, primary.method_localization)
        if c.commit_hash not in {h.commit_hash for h in historical_changes}
    )
    all_evidence.extend(evidence_builders.historical_change_evidence(historical_changes))

    # --- Related tests, scoped to whichever name (method or class) was resolved.
    target_name = None
    if primary.method_localization and primary.method_localization.resolution_status == ResolutionStatus.RESOLVED:
        target_name = primary.method_localization.name
    elif primary.class_localization and primary.class_localization.resolution_status == ResolutionStatus.RESOLVED:
        target_name = primary.class_localization.name

    repository_id = primary.repository_identity.repository_id if primary.repository_identity else None
    related_tests = find_related_tests(connection, repository_id, target_name)
    all_evidence.extend(evidence_builders.related_test_evidence(related_tests))

    related_commits = sorted({c.commit_hash for c in historical_changes})

    return FixLocalizationResult(
        resolution_status=ResolutionStatus.RESOLVED,
        rca=rca_result,
        primary_location=primary,
        related_locations=related,
        historical_changes=historical_changes,
        related_tests=related_tests,
        related_commits=related_commits,
        related_prs=[],  # no PR ingestion exists yet in this project -- never invented
        related_jira=[],  # no Jira ingestion exists yet in this project -- never invented
        evidence=all_evidence,
    )
