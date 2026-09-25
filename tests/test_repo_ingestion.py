"""Tests for `src.repo_ingestion` -- repository identification/access (Phase 1, requirement 1).

Uses small local Git repositories created on the fly (via GitPython) rather
than depending on a real external GitHub repository or network access.
"""

from __future__ import annotations

from pathlib import Path

from git import Repo

from src.repo_ingestion import (
    clone_repository,
    find_repository_root,
    get_repository_info,
)


def make_git_repo(path: Path) -> Repo:
    """Initialize a small Git repo at `path` with one commit, and return it."""
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "test@example.com")

    (path / "hello.py").write_text("def hello():\n    pass\n")
    repo.index.add(["hello.py"])
    repo.index.commit("Initial commit")
    return repo


# ---------------------------------------------------------------------------
# Accessing a repository
# ---------------------------------------------------------------------------


def test_clone_repository_returns_existing_local_directory(tmp_path):
    make_git_repo(tmp_path)

    result = clone_repository(str(tmp_path), dest_path=tmp_path / "unused_dest")

    assert result == tmp_path.resolve()


def test_clone_repository_clones_from_a_remote_url(tmp_path):
    """A `file://` URL is not itself an existing local directory path, so
    `clone_repository` takes the real "clone it" path -- the same one it
    would take for a `https://github.com/...` URL."""
    source = tmp_path / "source_repo"
    source.mkdir()
    make_git_repo(source)

    dest = tmp_path / "cloned_repo"
    result = clone_repository(f"file://{source}", dest_path=dest)

    assert result == dest.resolve()
    assert (dest / "hello.py").exists()
    assert (dest / ".git").exists()


# ---------------------------------------------------------------------------
# Repository root identification
# ---------------------------------------------------------------------------


def test_find_repository_root_from_a_nested_subdirectory(tmp_path):
    make_git_repo(tmp_path)
    nested = tmp_path / "package" / "subpackage"
    nested.mkdir(parents=True)

    assert find_repository_root(nested) == tmp_path.resolve()


def test_find_repository_root_falls_back_to_itself_when_not_a_git_repo(tmp_path):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()

    assert find_repository_root(plain_dir) == plain_dir.resolve()


# ---------------------------------------------------------------------------
# Repository metadata
# ---------------------------------------------------------------------------


def test_get_repository_info_for_a_git_repository(tmp_path):
    repo = make_git_repo(tmp_path)

    info = get_repository_info(tmp_path)

    assert info.root_path == str(tmp_path.resolve())
    assert info.name == tmp_path.name
    assert info.is_git_repository is True
    assert info.head_commit == repo.head.commit.hexsha
    assert info.current_branch is not None
    # No remote was configured for this local-only fixture repo.
    assert info.remote_url is None


def test_get_repository_info_for_a_plain_non_git_directory(tmp_path):
    plain_dir = tmp_path / "not_a_repo"
    plain_dir.mkdir()

    info = get_repository_info(plain_dir)

    assert info.is_git_repository is False
    assert info.remote_url is None
    assert info.current_branch is None
    assert info.head_commit is None
