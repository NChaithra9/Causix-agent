"""Tests for `src.git_history` -- deterministic Git history extraction (Phase 1, requirement 7).

Builds a tiny local Git repository with two commits (via GitPython) rather
than depending on a real external repository or network access.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from git import Repo

from src.git_history import blame_line, get_commit_file_relationships, get_commits
from src.parser.models import RelationshipType


def make_two_commit_repo(path: Path) -> Repo:
    """A repo with:
      commit 1 ("Add hello"): adds hello.py
      commit 2 ("Update hello"): modifies hello.py, adds extra.py
    """
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Ada Lovelace")
        config.set_value("user", "email", "ada@example.com")

    (path / "hello.py").write_text("def hello():\n    pass\n")
    repo.index.add(["hello.py"])
    repo.index.commit("Add hello")

    (path / "hello.py").write_text("def hello():\n    return 'hi'\n")
    (path / "extra.py").write_text("def extra():\n    pass\n")
    repo.index.add(["hello.py", "extra.py"])
    repo.index.commit("Update hello")

    return repo


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------


def test_commits_are_extracted_most_recent_first(tmp_path):
    make_two_commit_repo(tmp_path)

    commits = get_commits(tmp_path)

    assert len(commits) == 2
    assert commits[0].message == "Update hello"
    assert commits[1].message == "Add hello"


def test_commit_metadata_is_extracted(tmp_path):
    make_two_commit_repo(tmp_path)

    commits = get_commits(tmp_path)
    latest = commits[0]

    assert len(latest.commit_hash) == 40
    assert latest.author_name == "Ada Lovelace"
    assert latest.author_email == "ada@example.com"
    assert isinstance(latest.committed_at, datetime)


def test_get_commits_on_a_non_git_directory_returns_empty(tmp_path):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()

    assert get_commits(plain_dir) == []


# ---------------------------------------------------------------------------
# Changed files
# ---------------------------------------------------------------------------


def test_changed_files_are_identified_per_commit(tmp_path):
    make_two_commit_repo(tmp_path)

    commits = get_commits(tmp_path)
    latest, first = commits[0], commits[1]

    assert first.changed_files == ["hello.py"]
    assert latest.changed_files == ["extra.py", "hello.py"]


def test_commit_modifies_file_relationships(tmp_path):
    make_two_commit_repo(tmp_path)

    commits = get_commits(tmp_path)
    relationships = get_commit_file_relationships(commits)

    assert len(relationships) == 3  # 1 file in commit 1, 2 files in commit 2
    assert all(r.relationship_type == RelationshipType.MODIFIES for r in relationships)

    latest_hash = commits[0].commit_hash
    targets_for_latest = {r.target for r in relationships if r.source == latest_hash}
    assert targets_for_latest == {"hello.py", "extra.py"}


# ---------------------------------------------------------------------------
# Blame
# ---------------------------------------------------------------------------


def test_blame_returns_the_commit_that_last_touched_a_line(tmp_path):
    make_two_commit_repo(tmp_path)
    commits = get_commits(tmp_path)
    latest_hash = commits[0].commit_hash  # "Update hello", which last touched hello.py's line 2

    blame = blame_line(tmp_path, "hello.py", 2)

    assert blame is not None
    assert blame.commit_hash == latest_hash
    assert blame.message == "Update hello"
    assert blame.author_name == "Ada Lovelace"


def test_blame_on_a_non_git_directory_returns_none(tmp_path):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()
    (plain_dir / "hello.py").write_text("def hello():\n    pass\n")

    assert blame_line(plain_dir, "hello.py", 1) is None


def test_blame_on_an_untracked_file_returns_none(tmp_path):
    make_two_commit_repo(tmp_path)

    assert blame_line(tmp_path, "does_not_exist.py", 1) is None
