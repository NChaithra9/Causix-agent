"""GitHub / Git repository ingestion -- Phase 1, requirement 1.

Handles identifying and getting access to a repository (either already
cloned locally, or cloned fresh from a remote URL such as a GitHub repo),
finding its root directory, and reading basic repository metadata (remote
URL, current branch, HEAD commit hash) -- all deterministically, straight
from Git via GitPython, never guessed or inferred.

*Scanning* a repository's source files is a separate concern already
handled by Step 4 (``src.parser.scan_source_files`` /
``src.parser.parse_repository``); this module only identifies *which*
directory is the repository, not what's inside it.
"""

from __future__ import annotations

from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from .models import RepositoryInfo

__all__ = ["clone_repository", "find_repository_root", "get_repository_info"]


def clone_repository(source: str, dest_path: str | Path) -> Path:
    """Get local access to a repository, cloning it if necessary.

    If ``source`` is already a path to an existing local directory, it's
    returned as-is (resolved to an absolute path) -- this covers the common
    case of a repository that Steps 1-3 (or the user) already checked out.
    Otherwise ``source`` is treated as a clone URL (a GitHub HTTPS/SSH URL,
    or any other URL/path ``git clone`` understands) and cloned into
    ``dest_path``.
    """
    source_path = Path(source)
    if source_path.exists() and source_path.is_dir():
        return source_path.resolve()

    dest = Path(dest_path)
    dest.mkdir(parents=True, exist_ok=True)
    Repo.clone_from(source, str(dest))
    return dest.resolve()


def find_repository_root(path: str | Path) -> Path:
    """Walk upward from ``path`` to find the nearest directory containing ``.git``.

    Falls back to ``path`` itself (resolved) when no ``.git`` is found in
    any parent -- RootFix can still parse a plain directory of source files
    even when it isn't (or isn't yet) a Git repository.
    """
    current = Path(path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def get_repository_info(repo_path: str | Path) -> RepositoryInfo:
    """Read basic, deterministic metadata about a repository.

    ``is_git_repository`` is ``False`` (and the Git-specific fields stay
    ``None``) when ``repo_path`` isn't inside a Git repository at all. Each
    individual field beyond that is only populated when Git can actually
    answer it (e.g. a repo with no remote configured yields
    ``remote_url=None``, not a guess).
    """
    root = find_repository_root(repo_path)
    info = RepositoryInfo(root_path=str(root), name=root.name)

    try:
        repo = Repo(str(root), search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return info

    info.is_git_repository = True

    try:
        info.remote_url = repo.remotes.origin.url
    except (AttributeError, ValueError):
        info.remote_url = None

    try:
        info.current_branch = repo.active_branch.name
    except TypeError:
        # Detached HEAD -- there's genuinely no current branch name.
        info.current_branch = None

    try:
        info.head_commit = repo.head.commit.hexsha
    except (ValueError, GitCommandError):
        # No commits yet.
        info.head_commit = None

    return info
