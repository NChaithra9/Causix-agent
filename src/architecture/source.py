"""Read a repository's files at an exact revision without checking it out.

Reading blobs straight from the Git object database means the developer's
working tree and index are never touched, and any two revisions can be
analysed side by side.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from git import BadName, GitCommandError, Repo
from git.objects import Commit

from src.git_history import open_repository

__all__ = ["GitSourceTree", "MemorySourceTree", "SourceTree", "resolve_commit"]

MAX_FILE_BYTES = 1_000_000  # bigger files are reported as unresolved, not read


class SourceTree(Protocol):
    def paths(self) -> list[str]: ...

    def read_text(self, path: str) -> str | None:
        """The file's text, or ``None`` when it is missing, binary or too large."""
        ...


def resolve_commit(repo: Repo, revision: str) -> Commit | None:
    """The exact commit for a commit/branch/tag, or ``None`` when it does not exist."""
    if not revision or revision.startswith("-"):
        return None
    try:
        return repo.commit(revision)
    except (BadName, GitCommandError, ValueError):
        return None


class GitSourceTree:
    def __init__(self, repo_path: str | Path, commit: Commit) -> None:
        self._commit = commit
        self._blobs = {
            item.path: item
            for item in commit.tree.traverse()
            if item.type == "blob"  # type: ignore[union-attr]
        }

    @classmethod
    def open(cls, repo_path: str | Path, revision: str) -> GitSourceTree | None:
        repo = open_repository(repo_path)
        commit = resolve_commit(repo, revision) if repo else None
        return cls(repo_path, commit) if commit else None

    @property
    def commit(self) -> Commit:
        return self._commit

    def paths(self) -> list[str]:
        return sorted(self._blobs)

    def read_text(self, path: str) -> str | None:
        blob = self._blobs.get(path)
        if blob is None or blob.size > MAX_FILE_BYTES:  # type: ignore[attr-defined]
            return None
        try:
            return blob.data_stream.read().decode("utf-8")  # type: ignore[attr-defined]
        except UnicodeDecodeError:
            return None


class MemorySourceTree:
    """A fixed in-memory tree (unit tests, or analysing text you already hold)."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = dict(files)

    def paths(self) -> list[str]:
        return sorted(self._files)

    def read_text(self, path: str) -> str | None:
        return self._files.get(path)


def iter_paths(tree: SourceTree, suffixes: Iterable[str]) -> list[str]:
    wanted = tuple(suffixes)
    return [p for p in tree.paths() if p.endswith(wanted)]
