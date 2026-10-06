"""Snapshot <-> plain JSON-compatible dicts (for the Neo4j store and for saving
snapshots as files). Round-trips exactly, so a stored snapshot is the same
snapshot that was built."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from src.rca.models import EvidenceStatus

from .models import (
    ArchEntity,
    ArchitectureEvidence,
    ArchitectureSnapshot,
    ArchRelationship,
    EntityType,
    GraphRelationship,
    UnresolvedItem,
)

__all__ = [
    "evidence_from_dict",
    "evidence_to_dict",
    "snapshot_content_hash",
    "snapshot_from_dict",
    "snapshot_to_dict",
]


def evidence_to_dict(e: ArchitectureEvidence) -> dict[str, Any]:
    return {
        "source": e.source,
        "status": e.status.value,
        "details": e.details,
        "repository": e.repository,
        "file": e.file,
        "line": e.line,
        "method": e.method,
        "commit": e.commit,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "revision": e.revision,
        "relationship": e.relationship,
    }


def evidence_from_dict(d: dict[str, Any]) -> ArchitectureEvidence:
    return ArchitectureEvidence(
        source=d["source"],
        status=EvidenceStatus(d["status"]),
        details=d["details"],
        repository=d.get("repository"),
        file=d.get("file"),
        line=d.get("line"),
        method=d.get("method"),
        commit=d.get("commit"),
        timestamp=datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else None,
        revision=d.get("revision"),
        relationship=d.get("relationship"),
    )


def snapshot_to_dict(snapshot: ArchitectureSnapshot) -> dict[str, Any]:
    """Canonical (sorted) form: the same facts always give the same dict."""
    return {
        "repository": snapshot.repository,
        "revision": snapshot.revision,
        "committed_at": snapshot.committed_at.isoformat() if snapshot.committed_at else None,
        "entities": [
            {
                "key": e.key,
                "entity_type": e.entity_type.value,
                "name": e.name,
                "properties": dict(sorted(e.properties.items())),
                "meta": dict(sorted(e.meta.items())),
                "evidence": [evidence_to_dict(x) for x in e.evidence],
            }
            for e in sorted(snapshot.entities.values(), key=lambda e: e.key)
        ],
        "relationships": [
            {
                "rel_type": r.rel_type.value,
                "source": r.source,
                "target": r.target,
                "properties": dict(sorted(r.properties.items())),
                "evidence": [evidence_to_dict(x) for x in r.evidence],
            }
            for r in sorted(snapshot.relationships.values(), key=lambda r: r.key)
        ],
        "unresolved": [
            {"reason": u.reason, "file": u.file, "revision": u.revision}
            for u in snapshot.unresolved
        ],
    }


def snapshot_from_dict(data: dict[str, Any]) -> ArchitectureSnapshot:
    snapshot = ArchitectureSnapshot(
        repository=data["repository"],
        revision=data["revision"],
        committed_at=(
            datetime.fromisoformat(data["committed_at"]) if data.get("committed_at") else None
        ),
    )
    for e in data["entities"]:
        snapshot.entities[e["key"]] = ArchEntity(
            key=e["key"],
            entity_type=EntityType(e["entity_type"]),
            name=e["name"],
            properties=dict(e["properties"]),
            meta=dict(e["meta"]),
            evidence=[evidence_from_dict(x) for x in e["evidence"]],
        )
    for r in data["relationships"]:
        rel = ArchRelationship(
            rel_type=GraphRelationship(r["rel_type"]),
            source=r["source"],
            target=r["target"],
            properties=dict(r["properties"]),
            evidence=[evidence_from_dict(x) for x in r["evidence"]],
        )
        snapshot.relationships[rel.key] = rel
    snapshot.unresolved = [UnresolvedItem(**u) for u in data.get("unresolved", [])]
    return snapshot


def snapshot_content_hash(snapshot: ArchitectureSnapshot) -> str:
    payload = json.dumps(snapshot_to_dict(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
