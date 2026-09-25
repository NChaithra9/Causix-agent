"""Deterministic Git history extraction -- Phase 1, requirement 7.

Public API:
    get_commits(repo_path, max_count=None) -> list[CommitInfo]
    get_commit_file_relationships(commits) -> list[Relationship]   (Commit -MODIFIES-> File)
    blame_line(repo_path, file_path, line_number) -> BlameInfo | None
    is_git_repository(repo_path) -> bool
"""

from .git_reader import (
    blame_line,
    get_commit_file_relationships,
    get_commits,
    is_git_repository,
    open_repository,
)
from .models import BlameInfo, CommitInfo

__all__ = [
    "BlameInfo",
    "CommitInfo",
    "blame_line",
    "get_commit_file_relationships",
    "get_commits",
    "is_git_repository",
    "open_repository",
]
