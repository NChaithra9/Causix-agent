"""Tests for `src.rca.git_investigation` -- recent commits, last-modifying
commit, changes before a failure, and blame, all reusing GitPython via
`src.git_history.git_reader` (no new Git abstraction here)."""

from __future__ import annotations

from datetime import timedelta

from src.rca.git_investigation import investigate_blame, investigate_git_history
from src.rca.models import ResolutionStatus

from ._sample_repo import build_sample_repository


def test_recent_commits_and_last_modifying_commit(tmp_path):
    build_sample_repository(tmp_path)

    result = investigate_git_history(tmp_path, "payment/validator.py")

    assert result.resolution_status == ResolutionStatus.RESOLVED
    assert len(result.recent_commits) == 1
    assert result.last_modifying_commit is not None
    assert result.last_modifying_commit.message == "Add payment service"


def test_recent_commits_most_recent_first_for_a_file_touched_twice(tmp_path):
    from git import Repo

    build_sample_repository(tmp_path)
    repo = Repo(tmp_path)
    (tmp_path / "payment" / "validator.py").write_text("def validate_payment():\n    return True\n")
    repo.index.add(["payment/validator.py"])
    repo.index.commit("Fix validator")

    result = investigate_git_history(tmp_path, "payment/validator.py")

    assert [c.message for c in result.recent_commits] == ["Fix validator", "Add payment service"]
    assert result.last_modifying_commit.message == "Fix validator"


def test_changes_before_failure_timestamp_are_reported_without_causal_language(tmp_path):
    build_sample_repository(tmp_path)

    result = investigate_git_history(tmp_path, "payment/validator.py")
    last_commit_time = result.last_modifying_commit.committed_at
    after_commit = last_commit_time + timedelta(minutes=5)

    result_with_failure = investigate_git_history(tmp_path, "payment/validator.py", failure_timestamp=after_commit)

    assert len(result_with_failure.changes_before_failure) == 1
    assert result_with_failure.changes_before_failure[0].commit_hash == result.last_modifying_commit.commit_hash


def test_no_changes_before_a_failure_that_predates_every_commit(tmp_path):
    build_sample_repository(tmp_path)

    result = investigate_git_history(tmp_path, "payment/validator.py")
    before_everything = result.last_modifying_commit.committed_at - timedelta(days=365)

    result_with_failure = investigate_git_history(
        tmp_path, "payment/validator.py", failure_timestamp=before_everything
    )

    assert result_with_failure.changes_before_failure == []


def test_unresolved_for_a_file_git_has_never_seen(tmp_path):
    build_sample_repository(tmp_path)

    result = investigate_git_history(tmp_path, "payment/does_not_exist.py")

    assert result.resolution_status == ResolutionStatus.UNRESOLVED
    assert result.recent_commits == []


def test_blame_resolves_the_commit_that_last_touched_a_line(tmp_path):
    build_sample_repository(tmp_path)

    result = investigate_blame(tmp_path, "payment/validator.py", 1)

    assert result.resolution_status == ResolutionStatus.RESOLVED
    assert result.commit_hash is not None
    assert result.author_name == "Test Author"


def test_blame_unresolved_for_a_line_out_of_range(tmp_path):
    build_sample_repository(tmp_path)

    result = investigate_blame(tmp_path, "payment/validator.py", 999)

    assert result.resolution_status == ResolutionStatus.UNRESOLVED


def test_blame_unresolved_for_a_non_git_directory(tmp_path):
    result = investigate_blame(tmp_path, "whatever.py", 1)

    assert result.resolution_status == ResolutionStatus.UNRESOLVED
