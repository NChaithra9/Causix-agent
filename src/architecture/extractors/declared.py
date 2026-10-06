"""Explicitly declared architecture facts: ``rootfix-architecture.json``.

Events, database access and some service calls cannot be read reliably from
arbitrary code. A repository can state them in a versioned file at its root,
which makes them deterministic, revision-aware facts (the file's own history
*is* the history of the declaration):

    {
      "publishes": ["OrderCreated"],
      "consumes": ["PaymentCompleted"],
      "calls": ["payment-service"],
      "databases": ["orders-db"],
      "tables": ["orders"],
      "apis": [{"method": "GET", "path": "/orders"}]
    }

Unknown keys or malformed values are reported as unresolved, never guessed.
"""

from __future__ import annotations

import json

from ..builder import SnapshotBuilder
from ..models import EntityType, GraphRelationship, api_key
from ..source import SourceTree
from .context import ExtractionContext, add_relationship, ensure_entity, make_evidence

__all__ = ["DECLARATION_FILE", "extract_declared"]

DECLARATION_FILE = "rootfix-architecture.json"

_LISTS = {
    "publishes": (EntityType.EVENT, GraphRelationship.PUBLISHES),
    "consumes": (EntityType.EVENT, GraphRelationship.CONSUMES),
    "calls": (EntityType.SERVICE, GraphRelationship.CALLS),
    "databases": (EntityType.DATABASE, GraphRelationship.ACCESSES),
    "tables": (EntityType.TABLE, GraphRelationship.ACCESSES),
}


def _line_of(text: str, value: str) -> int | None:
    needle = json.dumps(value)
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return None


def extract_declared(tree: SourceTree, ctx: ExtractionContext, builder: SnapshotBuilder) -> None:
    if DECLARATION_FILE not in tree.paths():
        return
    text = tree.read_text(DECLARATION_FILE)
    if text is None:
        builder.unresolved("could not read the declaration file", DECLARATION_FILE)
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        builder.unresolved(f"declaration file is not valid JSON: {exc.msg}", DECLARATION_FILE)
        return
    if not isinstance(data, dict):
        builder.unresolved("declaration file must contain a JSON object", DECLARATION_FILE)
        return

    service = builder.snapshot.entities[f"service:{ctx.service}"]
    for key, value in data.items():
        if key == "apis":
            _apis(builder, ctx, text, service, value)
        elif key in _LISTS:
            _names(builder, ctx, text, service, key, value)
        else:
            builder.unresolved(f"unknown declaration key {key!r}", DECLARATION_FILE)


def _names(builder, ctx, text, service, key, value) -> None:
    entity_type, rel_type = _LISTS[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        builder.unresolved(f"{key!r} must be a list of non-empty strings", DECLARATION_FILE)
        return
    for name in value:
        if entity_type is EntityType.SERVICE and name == ctx.service:
            continue
        evidence = make_evidence(
            ctx,
            "declaration",
            f"{DECLARATION_FILE} declares {key}: {name!r}",
            file=DECLARATION_FILE,
            line=_line_of(text, name),
            relationship=rel_type,
        )
        target = ensure_entity(builder, entity_type, name, evidence)
        add_relationship(builder, rel_type, service, target, evidence)


def _apis(builder, ctx, text, service, value) -> None:
    if not isinstance(value, list):
        builder.unresolved("'apis' must be a list", DECLARATION_FILE)
        return
    for item in value:
        method = item.get("method") if isinstance(item, dict) else None
        path = item.get("path") if isinstance(item, dict) else None
        if not (isinstance(method, str) and isinstance(path, str) and path.startswith("/")):
            builder.unresolved("each api needs a 'method' and a '/path'", DECLARATION_FILE)
            continue
        method = method.upper()
        evidence = make_evidence(
            ctx,
            "declaration",
            f"{DECLARATION_FILE} declares api {method} {path}",
            file=DECLARATION_FILE,
            line=_line_of(text, path),
            relationship=GraphRelationship.EXPOSES,
        )
        api = ensure_entity(
            builder,
            EntityType.API,
            f"{method} {path}",
            evidence,
            key=api_key(method, path),
            properties={"method": method, "path": path},
            meta={"service": ctx.service, "file": DECLARATION_FILE},
        )
        add_relationship(builder, GraphRelationship.EXPOSES, service, api, evidence)
