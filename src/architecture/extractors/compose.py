"""Services and service-to-service dependencies from ``docker-compose`` files.

Compose is a declared description of a system's parts, so it is a sound
source of architecture facts: each ``services:`` entry is a service (or a
database, when its image is a well-known database image) and ``depends_on``
is a declared dependency. Nothing else is inferred from it.

Requires PyYAML. When it is not installed the compose files are reported as
unresolved (and changes attributed to them are suppressed) rather than
being silently treated as empty.
"""

from __future__ import annotations

import re
from typing import Any

from ..builder import SnapshotBuilder
from ..models import EntityType, GraphRelationship
from ..source import SourceTree
from .context import ExtractionContext, add_relationship, ensure_entity, make_evidence

__all__ = ["DATABASE_IMAGES", "extract_compose"]

_COMPOSE_FILE = re.compile(r"(^|/)(docker-)?compose(\.[\w-]+)?\.ya?ml$")
DATABASE_IMAGES = frozenset({"postgres", "mysql", "mariadb", "mongo", "neo4j"})


def _load_yaml() -> Any | None:
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _engine(image: object) -> str | None:
    if not isinstance(image, str):
        return None
    name = image.rsplit("/", 1)[-1].split(":", 1)[0].split("@", 1)[0].lower()
    return name if name in DATABASE_IMAGES else None


def _line_of_service(text: str, name: str) -> int | None:
    in_services = False
    for number, line in enumerate(text.splitlines(), start=1):
        if re.match(r"^services:\s*$", line):
            in_services = True
        elif in_services and re.match(rf"^\s+{re.escape(name)}:\s*$", line):
            return number
    return None


def extract_compose(tree: SourceTree, ctx: ExtractionContext, builder: SnapshotBuilder) -> None:
    files = [p for p in tree.paths() if _COMPOSE_FILE.search(p)]
    if not files:
        return
    yaml = _load_yaml()
    for path in files:
        if yaml is None:
            builder.unresolved("PyYAML is not installed; docker-compose could not be read", path)
            continue
        text = tree.read_text(path)
        if text is None:
            builder.unresolved("could not read the compose file", path)
            continue
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            builder.unresolved(f"compose file is not valid YAML: {str(exc).splitlines()[0]}", path)
            continue
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, dict):
            continue
        _record(builder, ctx, path, text, services)


def _record(
    builder: SnapshotBuilder, ctx: ExtractionContext, path: str, text: str, services: dict
) -> None:
    entities = {}
    for name, spec in services.items():
        spec = spec if isinstance(spec, dict) else {}
        engine = _engine(spec.get("image"))
        line = _line_of_service(text, str(name))
        evidence = make_evidence(
            ctx, "docker-compose", f"compose service {name!r}", file=path, line=line
        )
        if engine:
            entities[name] = ensure_entity(
                builder, EntityType.DATABASE, str(name), evidence, properties={"engine": engine}
            )
        else:
            entities[name] = ensure_entity(builder, EntityType.SERVICE, str(name), evidence)

    for name, spec in services.items():
        spec = spec if isinstance(spec, dict) else {}
        source = entities[name]
        if source.entity_type is not EntityType.SERVICE:
            continue
        depends = spec.get("depends_on") or []
        targets = list(depends) if isinstance(depends, list | dict) else []
        for target_name in targets:
            line = _line_of_service(text, str(name))
            target = entities.get(target_name)
            if target is None:
                builder.unresolved(
                    f"service {name!r} depends_on unknown service {target_name!r}", path
                )
                continue
            evidence = make_evidence(
                ctx,
                "docker-compose",
                f"compose service {name!r} depends_on {target_name!r}",
                file=path,
                line=line,
                relationship=GraphRelationship.DEPENDS_ON,
            )
            add_relationship(builder, GraphRelationship.DEPENDS_ON, source, target, evidence)
