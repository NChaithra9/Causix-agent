"""Data models for repository identification (Phase 1, requirement 1)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RepositoryInfo:
    """Basic, deterministic metadata about a repository on disk.

    Every field beyond ``root_path``/``name`` is ``None`` (or ``False`` for
    ``is_git_repository``) when it genuinely isn't available -- e.g. a plain
    directory that isn't a Git repository, or a fresh repo with no commits
    yet. Nothing here is inferred or guessed.
    """

    root_path: str
    name: str
    is_git_repository: bool = False
    remote_url: str | None = None
    current_branch: str | None = None
    head_commit: str | None = None
