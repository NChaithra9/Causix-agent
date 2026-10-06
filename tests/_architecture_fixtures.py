"""Helpers for the Phase 7 tests: hand-built snapshots and small real Git repos."""

from __future__ import annotations

from pathlib import Path

from git import Actor, Repo

from src.architecture import ArchitectureSnapshot
from src.architecture.builder import SnapshotBuilder
from src.architecture.extractors.context import (
    ExtractionContext,
    add_relationship,
    ensure_entity,
    make_evidence,
)
from src.architecture.models import EntityType, GraphRelationship, api_key


class Snap:
    """Fluent builder for a snapshot with explicit evidence files (no Git needed)."""

    def __init__(self, revision: str, repository: str = "repo", service: str = "svc") -> None:
        self.service_name = service
        self.ctx = ExtractionContext(repository, revision, service)
        self.builder = SnapshotBuilder(repository, revision)
        self._entity(EntityType.SERVICE, service, "repository", None)

    def _entity(self, entity_type, name, file, line, **kw):
        evidence = make_evidence(
            self.ctx, "test", f"{entity_type.value} {name}", file=file, line=line
        )
        return ensure_entity(self.builder, entity_type, name, evidence, **kw)

    def _edge(self, rel, source, target, file, line=1, properties=None):
        evidence = make_evidence(
            self.ctx,
            "test",
            f"{source.name} {rel.value} {target.name}",
            file=file,
            line=line,
            relationship=rel,
        )
        add_relationship(self.builder, rel, source, target, evidence, properties)

    def _svc(self, name):
        return self.builder.snapshot.entities.get(f"service:{name}") or self._entity(
            EntityType.SERVICE, name, "services.yml", 1
        )

    def service(self, name, file="services.yml"):
        self._entity(EntityType.SERVICE, name, file, 1)
        return self

    def calls(self, source, target, file="client.py", line=1):
        self._edge(GraphRelationship.CALLS, self._svc(source), self._svc(target), file, line)
        return self

    def depends_on_service(self, source, target, file="compose.yml"):
        self._edge(GraphRelationship.DEPENDS_ON, self._svc(source), self._svc(target), file)
        return self

    def api(self, method, path, handler=None, file="api.py", line=1, service=None):
        meta = {"service": service or self.service_name, "file": file}
        if handler:
            meta["handler"] = handler
        api = self._entity(
            EntityType.API,
            f"{method} {path}",
            file,
            line,
            key=api_key(method, path),
            properties={"method": method, "path": path},
            meta=meta,
        )
        self._edge(
            GraphRelationship.EXPOSES, self._svc(service or self.service_name), api, file, line
        )
        return self

    def event(self, source, event, role="publishes", file="events.py", line=1):
        rel = GraphRelationship.PUBLISHES if role == "publishes" else GraphRelationship.CONSUMES
        target = self._entity(EntityType.EVENT, event, file, line)
        self._edge(rel, self._svc(source), target, file, line)
        return self

    def database(self, source, name, file="db.py", line=1):
        target = self._entity(EntityType.DATABASE, name, file, line)
        self._edge(GraphRelationship.ACCESSES, self._svc(source), target, file, line)
        return self

    def table(self, source, name, file="models.py", line=1):
        target = self._entity(EntityType.TABLE, name, file, line)
        self._edge(GraphRelationship.ACCESSES, self._svc(source), target, file, line)
        return self

    def database_only(self, name, file="db.py"):
        self._entity(EntityType.DATABASE, name, file, 1)
        return self

    def table_only(self, name, file="models.py"):
        self._entity(EntityType.TABLE, name, file, 1)
        return self

    def depends(self, source, library, version="", file="requirements.txt", line=1):
        target = self._entity(EntityType.DEPENDENCY, library, file, line)
        self._edge(
            GraphRelationship.DEPENDS_ON,
            self._svc(source),
            target,
            file,
            line,
            {"version": version},
        )
        return self

    def unresolved(self, file, reason="could not parse"):
        self.builder.unresolved(reason, file)
        return self

    def build(self) -> ArchitectureSnapshot:
        return self.builder.build()


# --------------------------------------------------------------------- Git


class GitRepo:
    """A throw-away Git repository with a tiny commit helper."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.repo = Repo.init(str(path), initial_branch="main")
        self._actor = Actor("Fixture", "fixture@example.com")

    def commit(
        self, files: dict[str, str | None] | None = None, message: str = "change", moves=()
    ) -> str:
        """Write files (``None`` deletes), apply ``(old, new)`` renames, commit, return the sha."""
        for old, new in moves:
            (self.path / new).parent.mkdir(parents=True, exist_ok=True)
            self.repo.git.mv(old, new)
        for rel, text in (files or {}).items():
            target = self.path / rel
            if text is None:
                self.repo.git.rm("-f", rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            self.repo.git.add(rel)
        return self.repo.index.commit(message, author=self._actor, committer=self._actor).hexsha


# A small, realistic service used by the Git scenarios.
COMPOSE = """\
services:
  service-b:
    image: example/service-b
  service-c:
    image: example/service-c
  orders-db:
    image: postgres:16
"""

ROUTES = """\
from fastapi import FastAPI
import requests

app = FastAPI()


@app.get("/orders")
def list_orders():
    return requests.get("http://service-b/stock").json()
"""

REQUIREMENTS = "fastapi>=0.115\nrequests==2.31.0\n"


def base_files() -> dict[str, str]:
    return {
        "docker-compose.yml": COMPOSE,
        "app/routes.py": ROUTES,
        "requirements.txt": REQUIREMENTS,
    }
