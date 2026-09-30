"""Git History Analysis, Recent Changes, and Blame -- Phase 3, requirements 8, 9 & 10.

Reuses ``src.git_history.git_reader`` (GitPython-backed, already built in
Phase 1) for every actual Git operation -- this module only filters/shapes
those facts for RCA, it never talks to GitPython directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.git_history.git_reader import blame_line, get_commits

from .models import BlameResult, GitHistoryResult, RecentChange, ResolutionStatus

__all__ = ["investigate_git_history", "investigate_blame"]

DEFAULT_RECENT_COMMITS = 5


def investigate_git_history(
    repo_root: str | Path,
    file_path: str,
    *,
    failure_timestamp: datetime | None = None,
    recent_limit: int = DEFAULT_RECENT_COMMITS,
) -> GitHistoryResult:
    """Recent commits, the last modifying commit, and (when a failure
    timestamp is given) commits that touched this file before the failure --
    reported as "modified this file before the failure", never as a cause.
    """
    all_commits = get_commits(repo_root)
    touching = [c for c in all_commits if file_path in c.changed_files]

    if not touching:
        return GitHistoryResult(
            resolution_status=ResolutionStatus.UNRESOLVED,
            file_path=file_path,
            recent_commits=[],
        )

    def _as_change(commit) -> RecentChange:
        return RecentChange(
            commit_hash=commit.commit_hash,
            author_name=commit.author_name,
            committed_at=commit.committed_at,
            message=commit.message,
        )

    recent = [_as_change(c) for c in touching[:recent_limit]]
    last_modifying = recent[0] if recent else None

    changes_before_failure: list[RecentChange] = []
    if failure_timestamp is not None:
        before = [c for c in touching if c.committed_at < failure_timestamp]
        changes_before_failure = [_as_change(c) for c in before]

    return GitHistoryResult(
        resolution_status=ResolutionStatus.RESOLVED,
        file_path=file_path,
        recent_commits=recent,
        last_modifying_commit=last_modifying,
        changes_before_failure=changes_before_failure,
    )


def investigate_blame(repo_root: str | Path, file_path: str, line: int) -> BlameResult:
    """Git blame for one line -- who/what last changed it, per Git's own history."""
    blame = blame_line(repo_root, file_path, line)
    if blame is None:
        return BlameResult(resolution_status=ResolutionStatus.UNRESOLVED, file_path=file_path, line=line)

    return BlameResult(
        resolution_status=ResolutionStatus.RESOLVED,
        file_path=file_path,
        line=line,
        commit_hash=blame.commit_hash,
        author_name=blame.author_name,
        committed_at=blame.committed_at,
        message=blame.message,
    )
