"""Phase 7 entry point: what architecture changes happened between two revisions?

    previous revision -> snapshot ┐
                                  ├-> deterministic diff -> added / removed / modified -> evidence
    current revision  -> snapshot ┘

Snapshots are read from Git's object store at the exact commits (the working
tree is never touched) and, when a store is given, persisted per revision so
history stays distinguishable and is never rebuilt or overwritten.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.git_history import get_commits_between, get_file_changes_between, open_repository

from .diff import ArchitectureDiffEngine, summarise
from .extractors import DEFAULT_EXTRACTORS
from .extractors.context import ExtractionContext, make_evidence
from .models import (
    ArchitectureChangeResult,
    ArchitectureChangeStatus,
    ArchitectureSnapshot,
    UnresolvedItem,
)
from .snapshot import build_snapshot
from .source import resolve_commit
from .store import ArchitectureSnapshotStore

__all__ = ["detect_architecture_changes"]

_MAX_COMMIT_EVIDENCE = 25


def _unresolved_result(
    repository: str, previous: str, current: str, reason: str
) -> ArchitectureChangeResult:
    return ArchitectureChangeResult(
        status=ArchitectureChangeStatus.UNRESOLVED,
        repository=repository,
        previous_revision=previous,
        current_revision=current,
        unresolved_items=[UnresolvedItem(reason=reason)],
    )


def detect_architecture_changes(
    repo_path: str | Path,
    previous_revision: str,
    current_revision: str,
    *,
    repository_name: str | None = None,
    service_name: str | None = None,
    known_services: Iterable[str] = (),
    store: ArchitectureSnapshotStore | None = None,
    extractors=DEFAULT_EXTRACTORS,
) -> ArchitectureChangeResult:
    """Compare the architecture of ``repo_path`` at two exact revisions.

    Returns ``UNRESOLVED`` (with the reason) rather than raising when the path is not a
    Git repository or a revision does not exist.
    """
    repository = repository_name or Path(repo_path).resolve().name
    repo = open_repository(repo_path)
    if repo is None:
        return _unresolved_result(
            repository, previous_revision, current_revision, f"{repo_path} is not a Git repository"
        )
    commits = {}
    for label, revision in (("previous", previous_revision), ("current", current_revision)):
        commit = resolve_commit(repo, revision)
        if commit is None:
            return _unresolved_result(
                repository,
                previous_revision,
                current_revision,
                f"{label} revision {revision!r} does not exist in the repository",
            )
        commits[label] = commit.hexsha
    previous_sha, current_sha = commits["previous"], commits["current"]

    def snapshot_of(sha: str) -> ArchitectureSnapshot | None:
        if store is not None:
            stored = store.load(repository, sha)
            if stored is not None:
                return stored
        built = build_snapshot(
            repo_path,
            sha,
            repository_name=repository,
            service_name=service_name,
            known_services=known_services,
            extractors=extractors,
        )
        if built is not None and store is not None:
            store.save(built)
        return built

    previous, current = snapshot_of(previous_sha), snapshot_of(current_sha)
    if previous is None or current is None:  # defensive: the commits were just resolved
        return _unresolved_result(
            repository, previous_sha, current_sha, "a snapshot could not be built"
        )

    file_changes = get_file_changes_between(repo_path, previous_sha, current_sha)
    commit_infos = get_commits_between(repo_path, previous_sha, current_sha)
    outcome = ArchitectureDiffEngine(
        previous, current, file_changes=file_changes, commits=commit_infos
    ).diff()

    ctx = ExtractionContext(repository, current_sha, "")
    evidence = []
    for info in commit_infos[:_MAX_COMMIT_EVIDENCE]:
        item = make_evidence(
            ctx,
            "git_commit",
            f"commit {info.commit_hash[:12]} between the revisions: "
            f"{info.message.splitlines()[0] if info.message else ''}",
        )
        item.commit = info.commit_hash
        item.timestamp = info.committed_at
        evidence.append(item)

    unresolved = [*outcome.unresolved, *previous.unresolved, *current.unresolved]
    return ArchitectureChangeResult(
        status=(
            ArchitectureChangeStatus.CHANGES_DETECTED
            if outcome.changes
            else ArchitectureChangeStatus.NO_CHANGES
        ),
        repository=repository,
        previous_revision=previous_sha,
        current_revision=current_sha,
        changes=outcome.changes,
        summary=summarise(outcome.changes),
        evidence=evidence,
        changed_files=file_changes,
        unresolved_items=unresolved,
    )
