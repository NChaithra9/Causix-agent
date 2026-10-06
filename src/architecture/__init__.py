"""Phase 7: deterministic architecture change detection.

    previous revision -> ArchitectureSnapshot ┐
                                              ├-> ArchitectureDiffEngine -> ArchitectureChangeResult
    current revision  -> ArchitectureSnapshot ┘            (added / removed / modified + evidence)

A snapshot holds the architecture *facts* deterministic extraction can
establish at one exact commit (services, APIs, events, databases, tables,
declared dependencies, and the relationships between them), read straight
from Git. The diff is plain set arithmetic. Every change carries evidence
(file, line, revision, commit, relationship). No LLM, embeddings or
heuristic scoring is involved, and nothing here says what a change *means*.

Public API:
    detect_architecture_changes(repo_path, previous_revision, current_revision)
        -> ArchitectureChangeResult
    build_snapshot(repo_path, revision) -> ArchitectureSnapshot
    ArchitectureDiffEngine(previous, current).diff()
    ArchitectureSnapshotStore(connection)   # revision-isolated Neo4j persistence
"""

from .analyzer import detect_architecture_changes
from .diff import ArchitectureDiffEngine, DiffOutcome, summarise
from .models import (
    ArchEntity,
    ArchitectureChange,
    ArchitectureChangeResult,
    ArchitectureChangeStatus,
    ArchitectureEvidence,
    ArchitectureSnapshot,
    ArchRelationship,
    ChangeCategory,
    ChangeSummary,
    ChangeType,
    EntityType,
    UnresolvedItem,
)
from .serialization import snapshot_content_hash, snapshot_from_dict, snapshot_to_dict
from .snapshot import build_snapshot, build_snapshot_from_tree
from .source import GitSourceTree, MemorySourceTree
from .store import ArchitectureSnapshotStore, SnapshotConflictError

__all__ = [
    "ArchEntity",
    "ArchRelationship",
    "ArchitectureChange",
    "ArchitectureChangeResult",
    "ArchitectureChangeStatus",
    "ArchitectureDiffEngine",
    "ArchitectureEvidence",
    "ArchitectureSnapshot",
    "ArchitectureSnapshotStore",
    "ChangeCategory",
    "ChangeSummary",
    "ChangeType",
    "DiffOutcome",
    "EntityType",
    "GitSourceTree",
    "MemorySourceTree",
    "SnapshotConflictError",
    "UnresolvedItem",
    "build_snapshot",
    "build_snapshot_from_tree",
    "detect_architecture_changes",
    "snapshot_content_hash",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "summarise",
]
