"""Declared library dependencies: ``requirements.txt`` and ``pyproject.toml``.

Phase 1 extracts *imports*, not declared dependencies, so this is the
minimum new extraction needed. It only reads what is written in the
manifests -- it never resolves versions or follows ``-r`` includes.
"""

from __future__ import annotations

import re
import tomllib

from ..builder import SnapshotBuilder
from ..models import EntityType, GraphRelationship
from ..source import SourceTree
from .context import ExtractionContext, add_relationship, ensure_entity, make_evidence

__all__ = ["extract_manifests", "normalise_name", "parse_requirement"]

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")
_RUNTIME_REQUIREMENTS = re.compile(
    r"(^|/)requirements\.txt$|(^|/)requirements/(base|main|prod|production|runtime)\.txt$"
)


def normalise_name(name: str) -> str:
    """PEP 503 normalisation, so ``Foo_Bar`` and ``foo-bar`` are one dependency."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(text: str) -> tuple[str, str] | None:
    """``"fastapi>=0.115 ; python_version>'3'"`` -> ``("fastapi", ">=0.115")``."""
    text = re.split(r"\s+--", text.split(" #")[0])[0].strip()
    if not text or text.startswith(("-", ".", "/")) or "://" in text.split("@")[0]:
        return None
    match = _NAME.match(text.split(";")[0])
    if not match:
        return None
    return normalise_name(match.group(1)), re.sub(r"\s+", "", match.group(2))


def _line_of(text: str, needle: str) -> int | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return number
    return None


def _record(
    builder: SnapshotBuilder,
    ctx: ExtractionContext,
    source: str,
    path: str,
    raw: str,
    line: int | None,
) -> None:
    parsed = parse_requirement(raw)
    if parsed is None:
        return
    name, spec = parsed
    service = builder.snapshot.entities[f"service:{ctx.service}"]
    evidence = make_evidence(
        ctx,
        source,
        f"declared dependency {raw.strip()!r}",
        file=path,
        line=line,
        relationship=GraphRelationship.DEPENDS_ON,
    )
    library = ensure_entity(builder, EntityType.DEPENDENCY, name, evidence)
    add_relationship(
        builder, GraphRelationship.DEPENDS_ON, service, library, evidence, {"version": spec}
    )


def extract_manifests(tree: SourceTree, ctx: ExtractionContext, builder: SnapshotBuilder) -> None:
    for path in tree.paths():
        if _RUNTIME_REQUIREMENTS.search(path):
            text = tree.read_text(path)
            if text is None:
                builder.unresolved("could not read the requirements file", path)
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                _record(builder, ctx, "requirements.txt", path, line, number)
        elif path == "pyproject.toml" or path.endswith("/pyproject.toml"):
            text = tree.read_text(path)
            if text is None:
                builder.unresolved("could not read pyproject.toml", path)
                continue
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                builder.unresolved(f"pyproject.toml is not valid TOML: {exc}", path)
                continue
            for raw in data.get("project", {}).get("dependencies", []) or []:
                if isinstance(raw, str):
                    _record(builder, ctx, "pyproject.toml", path, raw, _line_of(text, raw))
