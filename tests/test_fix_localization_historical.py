"""Tests for `src.fix_localization.historical` -- reusing Phase 3's
already-gathered git facts (file-level), plus new method-scoped `-L`
line-history. Pure GitPython, no Neo4j needed."""

from __future__ import annotations

from src.fix_localization.historical import historical_changes_for_location, method_scoped_commits
from src.fix_localization.models import MethodLocalization, ResolutionStatus
from src.rca.git_investigation import investigate_blame, investigate_git_history

from ._sample_repo import build_sample_repository


def test_reuses_phase3_git_facts_with_factual_labels(tmp_path):
    build_sample_repository(tmp_path)

    git_history = investigate_git_history(tmp_path, "payment/validator.py")
    blame = investigate_blame(tmp_path, "payment/validator.py", 1)

    changes = historical_changes_for_location(git_history, blame)

    assert len(changes) == 1
    assert changes[0].label == "related_commit"  # blame's commit takes precedence
    assert changes[0].commit_hash == blame.commit_hash


def test_no_git_history_yields_no_historical_changes():
    changes = historical_changes_for_location(None, None)
    assert changes == []


def test_method_scoped_commits_traces_the_exact_line_range(tmp_path):
    build_sample_repository(tmp_path)

    method_loc = MethodLocalization(
        resolution_status=ResolutionStatus.RESOLVED,
        name="validate_payment",
        qualified_name="validate_payment",
        kind="Function",
        file_path="payment/validator.py",
        start_line=1,
        end_line=2,
    )

    changes = method_scoped_commits(tmp_path, method_loc)

    assert len(changes) == 1
    assert changes[0].method == "validate_payment"
    assert changes[0].label == "historical_change"


def test_method_scoped_commits_empty_when_location_unresolved(tmp_path):
    build_sample_repository(tmp_path)
    method_loc = MethodLocalization(resolution_status=ResolutionStatus.UNRESOLVED)
    assert method_scoped_commits(tmp_path, method_loc) == []
