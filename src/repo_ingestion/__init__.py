"""GitHub / Git repository ingestion -- Phase 1, requirement 1.

Public API:
    clone_repository(source, dest_path) -> Path
    find_repository_root(path) -> Path
    get_repository_info(repo_path) -> RepositoryInfo
"""

from .ingestion import clone_repository, find_repository_root, get_repository_info
from .models import RepositoryInfo

__all__ = [
    "RepositoryInfo",
    "clone_repository",
    "find_repository_root",
    "get_repository_info",
]
