"""Revision-isolated persistence of architecture snapshots in Neo4j.

Every snapshot lives in its own namespace, inside the existing database but
apart from the code graph:

    (:ArchitectureSnapshot {id, repository, revision, committed_at, content_hash})
        -[:HAS_ENTITY]-> (:ArchitectureEntity {snapshot_id, key, ...})
    (:ArchitectureEntity)-[:ARCHITECTURE_RELATIONSHIP {snapshot_id, rel_type, ...}]->
        (:ArchitectureEntity)

Every node and relationship carries its ``snapshot_id`` (``<repository>@<commit>``)
and every query filters on it, so two revisions -- or two repositories --
can never be mixed. Nothing here touches the Phase 2 code graph, and a
stored snapshot is never overwritten: re-saving identical facts is a no-op,
and saving different facts under the same revision is an error.
"""

from __future__ import annotations

import json
from datetime import datetime

from src.graph import Neo4jConnection

from .models import ArchitectureSnapshot
from .serialization import (
    snapshot_content_hash,
    snapshot_from_dict,
    snapshot_to_dict,
)

__all__ = ["ArchitectureSnapshotStore", "SnapshotConflictError", "snapshot_id"]


class SnapshotConflictError(Exception):
    """A different snapshot is already stored for this repository revision."""


def snapshot_id(repository: str, revision: str) -> str:
    return f"{repository}@{revision}"


class ArchitectureSnapshotStore:
    def __init__(self, connection: Neo4jConnection) -> None:
        self._db = connection
        self._schema_ready = False

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        for statement in (
            "CREATE CONSTRAINT architecture_snapshot_id IF NOT EXISTS "
            "FOR (s:ArchitectureSnapshot) REQUIRE s.id IS UNIQUE",
            "CREATE INDEX architecture_entity_snapshot IF NOT EXISTS "
            "FOR (e:ArchitectureEntity) ON (e.snapshot_id)",
        ):
            self._db.execute_write(statement)
        self._schema_ready = True

    # ------------------------------------------------------------------- write

    def save(self, snapshot: ArchitectureSnapshot) -> bool:
        """Store ``snapshot``. ``True`` when newly stored, ``False`` when the identical
        snapshot was already there. Raises ``SnapshotConflictError`` rather than overwrite."""
        self.ensure_schema()
        sid = snapshot_id(snapshot.repository, snapshot.revision)
        content_hash = snapshot_content_hash(snapshot)
        existing = self._db.execute_read(
            "MATCH (s:ArchitectureSnapshot {id: $id}) RETURN s.content_hash AS content_hash",
            {"id": sid},
        )
        if existing:
            if existing[0]["content_hash"] == content_hash:
                return False
            raise SnapshotConflictError(
                f"a different architecture snapshot is already stored for {sid}; "
                "stored snapshots are never overwritten"
            )

        data = snapshot_to_dict(snapshot)
        self._db.execute_write(
            "CREATE (s:ArchitectureSnapshot {id: $id, repository: $repository, "
            "revision: $revision, "
            "committed_at: $committed_at, content_hash: $content_hash, unresolved: $unresolved})",
            {
                "id": sid,
                "repository": snapshot.repository,
                "revision": snapshot.revision,
                "committed_at": data["committed_at"],
                "content_hash": content_hash,
                "unresolved": json.dumps(data["unresolved"]),
            },
        )
        entities = [
            {
                "key": e["key"],
                "entity_type": e["entity_type"],
                "name": e["name"],
                "properties": json.dumps(e["properties"]),
                "meta": json.dumps(e["meta"]),
                "evidence": json.dumps(e["evidence"]),
            }
            for e in data["entities"]
        ]
        self._db.execute_write(
            "MATCH (s:ArchitectureSnapshot {id: $id}) UNWIND $entities AS e "
            "CREATE (s)-[:HAS_ENTITY]->(:ArchitectureEntity {snapshot_id: $id, key: e.key, "
            "entity_type: e.entity_type, name: e.name, properties: e.properties, meta: e.meta, "
            "evidence: e.evidence})",
            {"id": sid, "entities": entities},
        )
        relationships = [
            {
                "rel_type": r["rel_type"],
                "source": r["source"],
                "target": r["target"],
                "properties": json.dumps(r["properties"]),
                "evidence": json.dumps(r["evidence"]),
            }
            for r in data["relationships"]
        ]
        if relationships:
            self._db.execute_write(
                "UNWIND $rels AS r "
                "MATCH (a:ArchitectureEntity {snapshot_id: $id, key: r.source}) "
                "MATCH (b:ArchitectureEntity {snapshot_id: $id, key: r.target}) "
                "CREATE (a)-[:ARCHITECTURE_RELATIONSHIP {snapshot_id: $id, rel_type: r.rel_type, "
                "properties: r.properties, evidence: r.evidence}]->(b)",
                {"id": sid, "rels": relationships},
            )
        return True

    # -------------------------------------------------------------------- read

    def load(self, repository: str, revision: str) -> ArchitectureSnapshot | None:
        sid = snapshot_id(repository, revision)
        header = self._db.execute_read(
            "MATCH (s:ArchitectureSnapshot {id: $id}) RETURN s", {"id": sid}
        )
        if not header:
            return None
        node = header[0]["s"]
        entities = self._db.execute_read(
            "MATCH (e:ArchitectureEntity {snapshot_id: $id}) RETURN e ORDER BY e.key", {"id": sid}
        )
        rels = self._db.execute_read(
            "MATCH (a:ArchitectureEntity {snapshot_id: $id})"
            "-[r:ARCHITECTURE_RELATIONSHIP {snapshot_id: $id}]->"
            "(b:ArchitectureEntity {snapshot_id: $id}) "
            "RETURN a.key AS source, b.key AS target, r.rel_type AS rel_type, "
            "r.properties AS properties, r.evidence AS evidence ORDER BY rel_type, source, target",
            {"id": sid},
        )
        return snapshot_from_dict(
            {
                "repository": node["repository"],
                "revision": node["revision"],
                "committed_at": node.get("committed_at"),
                "entities": [
                    {
                        "key": row["e"]["key"],
                        "entity_type": row["e"]["entity_type"],
                        "name": row["e"]["name"],
                        "properties": json.loads(row["e"]["properties"]),
                        "meta": json.loads(row["e"]["meta"]),
                        "evidence": json.loads(row["e"]["evidence"]),
                    }
                    for row in entities
                ],
                "relationships": [
                    {
                        "rel_type": row["rel_type"],
                        "source": row["source"],
                        "target": row["target"],
                        "properties": json.loads(row["properties"]),
                        "evidence": json.loads(row["evidence"]),
                    }
                    for row in rels
                ],
                "unresolved": json.loads(node.get("unresolved") or "[]"),
            }
        )

    def revisions(self, repository: str) -> list[tuple[str, datetime | None]]:
        """Stored revisions of one repository, oldest commit first."""
        rows = self._db.execute_read(
            "MATCH (s:ArchitectureSnapshot {repository: $repository}) "
            "RETURN s.revision AS revision, s.committed_at AS committed_at "
            "ORDER BY committed_at, revision",
            {"repository": repository},
        )
        return [
            (
                r["revision"],
                datetime.fromisoformat(r["committed_at"]) if r["committed_at"] else None,
            )
            for r in rows
        ]
