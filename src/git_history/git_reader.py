"""Deterministic Git history extraction -- Phase 1, requirement 7.

Uses GitPython (a thin, well-tested wrapper around the real ``git`` binary)
to read commits, which files each commit touched, and blame information.
Nothing here is inferred or guessed -- every fact comes directly from Git's
own history data, the same way ``ast`` gives Step 4 facts directly from
source code. No Jira integration, RCA, or LLM reasoning is involved: this
module only answers "what does Git's own history record", nothing more.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from ..parser.models import Relationship, RelationshipType
from .models import BlameInfo, CommitInfo

__all__ = [
    "is_git_repository",
    "open_repository",
    "get_commits",
    "get_commit_file_relationships",
    "blame_line",
]


def is_git_repository(repo_path: str | Path) -> bool:
    """Whether `repo_path` is (inside) a Git repository."""
    return open_repository(repo_path) is not None


def open_repository(repo_path: str | Path) -> Repo | None:
    """Open ``repo_path`` as a Git repository, or ``None`` if it isn't one.

    Never raises -- callers that don't care about Git history at all can
    just get ``None`` back for a plain, non-Git directory.
    """
    try:
        return Repo(str(repo_path), search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None


def get_commits(repo_path: str | Path, max_count: int | None = None) -> list[CommitInfo]:
    """Commits reachable from HEAD, most recent first.

    Each :class:`CommitInfo` also carries the repo-relative paths of the
    files that commit changed. Returns ``[]`` for a directory that isn't a
    Git repository, or a repository with no commits yet -- never raises.

    ``max_count`` caps how many commits to read (from HEAD backwards); pass
    ``None`` (the default) to read the full history.
    """
    repo = open_repository(repo_path)
    if repo is None:
        return []

    try:
        repo.head.commit  # raises ValueError if there are no commits yet
    except ValueError:
        return []

    commits: list[CommitInfo] = []
    for commit in repo.iter_commits(max_count=max_count):
        changed_files = sorted(commit.stats.files.keys())
        commits.append(
            CommitInfo(
                commit_hash=commit.hexsha,
                author_name=commit.author.name or "",
                author_email=commit.author.email or "",
                committed_at=datetime.fromtimestamp(commit.committed_date, tz=timezone.utc),
                message=commit.message.strip(),
                changed_files=changed_files,
            )
        )
    return commits


def get_commit_file_relationships(commits: list[CommitInfo]) -> list[Relationship]:
    """``Commit -> MODIFIES -> File`` relationships, derived from already-extracted commits.

    A commit modifies a whole file, not a specific line, so ``line`` is set
    to ``0`` as a sentinel (there's no meaningful line number for this
    relationship type).
    """
    relationships: list[Relationship] = []
    for commit in commits:
        for file_path in commit.changed_files:
            relationships.append(
                Relationship(
                    source=commit.commit_hash,
                    relationship_type=RelationshipType.MODIFIES,
                    target=file_path,
                    source_file=file_path,
                    line=0,
                )
            )
    return relationships


def blame_line(repo_path: str | Path, file_path: str, line_number: int) -> BlameInfo | None:
    """Which commit last touched ``file_path``'s ``line_number`` (1-based).

    ``file_path`` should be relative to the repository root (as recorded on
    ``ParsedFile.file_path`` / ``ClassInfo``/``MethodInfo``/``FunctionInfo``
    line numbers). Returns ``None`` -- rather than guessing -- when the path
    isn't a Git repository, the file isn't tracked, or the line is out of
    range.
    """
    repo = open_repository(repo_path)
    if repo is None:
        return None

    try:
        blame_entries = repo.blame("HEAD", file_path)
    except (GitCommandError, ValueError):
        return None
    if not blame_entries:
        return None

    current_line = 0
    for commit, lines in blame_entries:
        for _ in lines:
            current_line += 1
            if current_line == line_number:
                return BlameInfo(
                    file_path=file_path,
                    line_number=line_number,
                    commit_hash=commit.hexsha,
                    author_name=commit.author.name or "",
                    committed_at=datetime.fromtimestamp(commit.committed_date, tz=timezone.utc),
                    message=commit.message.strip(),
                )
    return None
