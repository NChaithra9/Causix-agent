"""Build an architecture snapshot of one repository at one exact revision."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from .builder import SnapshotBuilder
from .extractors import DEFAULT_EXTRACTORS, ExtractionContext
from .extractors.context import ensure_entity, make_evidence
from .models import ArchitectureSnapshot, EntityType
from .source import GitSourceTree, SourceTree

__all__ = ["build_snapshot", "build_snapshot_from_tree"]

Extractor = Callable[[SourceTree, ExtractionContext, SnapshotBuilder], None]


def build_snapshot_from_tree(
    tree: SourceTree,
    *,
    repository: str,
    revision: str,
    committed_at: datetime | None = None,
    service_name: str | None = None,
    known_services: Iterable[str] = (),
    extractors: Iterable[Extractor] = DEFAULT_EXTRACTORS,
) -> ArchitectureSnapshot:
    """Run the extractors over any ``SourceTree``. The repository itself is the
    service that owns the code-level facts (APIs, dependencies, tables, calls)."""
    service = service_name or repository
    ctx = ExtractionContext(
        repository=repository,
        revision=revision,
        service=service,
        known_services=frozenset(known_services),
    )
    builder = SnapshotBuilder(repository, revision, committed_at)
    ensure_entity(
        builder,
        EntityType.SERVICE,
        service,
        make_evidence(ctx, "repository", f"service {service!r} is the repository {repository!r}"),
    )
    for extract in extractors:
        extract(tree, ctx, builder)
    return builder.build()


def build_snapshot(
    repo_path: str | Path,
    revision: str,
    *,
    repository_name: str | None = None,
    service_name: str | None = None,
    known_services: Iterable[str] = (),
    extractors: Iterable[Extractor] = DEFAULT_EXTRACTORS,
) -> ArchitectureSnapshot | None:
    """Snapshot ``revision`` of the Git repository at ``repo_path`` -- read straight from
    Git's object store, so the working tree is never touched. ``None`` when the
    repository or revision does not exist."""
    tree = GitSourceTree.open(repo_path, revision)
    if tree is None:
        return None
    commit = tree.commit
    return build_snapshot_from_tree(
        tree,
        repository=repository_name or Path(repo_path).resolve().name,
        revision=commit.hexsha,
        committed_at=datetime.fromtimestamp(commit.committed_date, tz=UTC),
        service_name=service_name,
        known_services=known_services,
        extractors=extractors,
    )
