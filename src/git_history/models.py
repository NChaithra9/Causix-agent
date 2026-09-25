"""Data models for Git history facts (Phase 1, requirement 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CommitInfo:
    """One commit, with the deterministic facts needed for engineering intelligence.

    ``changed_files`` lists the repo-relative paths of files that commit
    touched -- this is what backs the ``Commit -> MODIFIES -> File``
    relationship.
    """

    commit_hash: str
    author_name: str
    author_email: str
    committed_at: datetime
    message: str
    changed_files: list[str] = field(default_factory=list)


@dataclass
class BlameInfo:
    """Which commit last touched one specific line of one specific file.

    This is what lets RootFix answer "who/what last changed this method" --
    look up the commit at the method's definition line (``ParsedFile``'s
    ``ClassInfo``/``MethodInfo``/``FunctionInfo`` already record that line
    number from Step 4).
    """

    file_path: str
    line_number: int
    commit_hash: str
    author_name: str
    committed_at: datetime
    message: str
