"""Historical Fixes / Similar Historical Locations -- Phase 4, requirements 9 & 10.

File-level history is *reused* directly from the Phase 3
:class:`~src.rca.models.RCAResult` this localization was built from
(``rca.git_history`` / ``rca.blame``) -- no second Git read for facts Phase
3 already gathered.

Method-scoped history (requirement 10: previous changes to the same
method, not just the same file) is new here: it uses Git's own
``git log -L <start>,<end>:<file>`` line-history feature via GitPython --
a real Git capability, not a heuristic -- to find commits that touched the
exact source line range Phase 4 resolved to. Falls back to an empty list
(never a guess) when that range can't be traced (renamed file, no matching
range in an older revision, etc).

Every label is a plain factual description of the relationship
(``related_commit`` / ``previous_change`` / ``historical_change``) -- never
a claim that a commit "fixed" or "caused" anything.
"""

from __future__ import annotations

import re
from pathlib import Path

from git import GitCommandError

from src.git_history.git_reader import open_repository
from src.rca.models import BlameResult, GitHistoryResult

from .models import HistoricalChange, MethodLocalization

__all__ = ["historical_changes_for_location", "method_scoped_commits"]

_COMMIT_LINE_RE = re.compile(r"^commit ([0-9a-f]{40})")
_AUTHOR_LINE_RE = re.compile(r"^Author:\s+(.*?)\s*(?:<.*>)?\s*$")
_DATE_LINE_RE = re.compile(r"^Date:\s+(.*)$")


def historical_changes_for_location(
    git_history: GitHistoryResult | None, blame: BlameResult | None
) -> list[HistoricalChange]:
    """Reshape Phase 3's already-gathered git facts into Phase 4's factual,
    non-causal historical-change labels. Dedupes by commit hash, preferring
    the more specific label."""
    if git_history is None:
        return []

    by_hash: dict[str, HistoricalChange] = {}

    for change in git_history.recent_commits:
        by_hash[change.commit_hash] = HistoricalChange(
            label="historical_change",
            commit_hash=change.commit_hash,
            file_path=git_history.file_path or "",
            author_name=change.author_name,
            committed_at=change.committed_at,
            message=change.message,
        )

    for change in git_history.changes_before_failure:
        existing = by_hash.get(change.commit_hash)
        by_hash[change.commit_hash] = HistoricalChange(
            label="previous_change",
            commit_hash=change.commit_hash,
            file_path=git_history.file_path or "",
            author_name=change.author_name,
            committed_at=change.committed_at,
            message=change.message,
            method=existing.method if existing else None,
        )

    if blame is not None and blame.commit_hash:
        existing = by_hash.get(blame.commit_hash)
        by_hash[blame.commit_hash] = HistoricalChange(
            label="related_commit",
            commit_hash=blame.commit_hash,
            file_path=blame.file_path or (git_history.file_path or ""),
            author_name=blame.author_name or "",
            committed_at=blame.committed_at,
            message=blame.message or "",
            method=existing.method if existing else None,
        )

    return sorted(by_hash.values(), key=lambda c: c.committed_at, reverse=True)


def method_scoped_commits(repo_root: str | Path, method_localization: MethodLocalization) -> list[HistoricalChange]:
    """Commits that touched the exact resolved line range of one method/
    function, via Git's own ``-L`` line-history -- distinct from (and a
    subset of) the file-level history above."""
    if (
        not method_localization.file_path
        or method_localization.start_line is None
        or method_localization.end_line is None
    ):
        return []

    repo = open_repository(repo_root)
    if repo is None:
        return []

    range_spec = f"{method_localization.start_line},{method_localization.end_line}:{method_localization.file_path}"
    try:
        raw = repo.git.log(f"-L{range_spec}", "--no-color")
    except GitCommandError:
        return []

    changes: list[HistoricalChange] = []
    current_hash: str | None = None
    current_author: str | None = None
    current_date: str | None = None
    current_message_lines: list[str] = []
    in_message = False

    def _flush() -> None:
        if current_hash is None:
            return
        from datetime import datetime

        committed_at = None
        if current_date:
            for fmt in ("%a %b %d %H:%M:%S %Y %z",):
                try:
                    committed_at = datetime.strptime(current_date, fmt)
                except ValueError:
                    committed_at = None
        message = "\n".join(line.strip() for line in current_message_lines if line.strip())
        changes.append(
            HistoricalChange(
                label="historical_change",
                commit_hash=current_hash,
                file_path=method_localization.file_path or "",
                author_name=current_author or "",
                committed_at=committed_at or datetime.min,
                message=message,
                method=method_localization.qualified_name,
            )
        )

    for line in raw.splitlines():
        commit_match = _COMMIT_LINE_RE.match(line)
        if commit_match:
            _flush()
            current_hash = commit_match.group(1)
            current_author = None
            current_date = None
            current_message_lines = []
            in_message = False
            continue
        author_match = _AUTHOR_LINE_RE.match(line)
        if author_match:
            current_author = author_match.group(1)
            continue
        date_match = _DATE_LINE_RE.match(line)
        if date_match:
            current_date = date_match.group(1)
            in_message = True
            continue
        if line.startswith("diff ") or line.startswith("@@"):
            in_message = False
            continue
        if in_message and line.strip():
            current_message_lines.append(line)

    _flush()
    return changes
