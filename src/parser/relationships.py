"""Deterministic relationship extraction -- Step 5 of RootFix's pipeline.

Takes the structured facts Step 4's parser already produced (``ParsedFile``,
``ClassInfo``, ``MethodInfo``, ``FunctionInfo``, ``ImportInfo``) and derives
relationships between them:

    File   --CONTAINS--> Class
    File   --CONTAINS--> Function
    Class  --CONTAINS--> Method
    Function/Method --CALLS--> Function/Method

The CONTAINS relationships come straight from Step 4's models -- no new
information is needed, so those are always fully resolved. CALLS
relationships depend on *how* a function body is written, which Step 4's
models don't retain (Step 4 only keeps facts about definitions, not their
bodies), so this module re-parses each file's source with ``ast`` to walk
its call expressions. Nothing here changes what Step 4 extracts -- it only
reads Step 4's output plus the raw source text.

This step stays fully deterministic: no LLM, no embeddings, no semantic
guessing. A call is only ever marked ``resolved`` when it can be tied,
mechanically, to a function/class Step 4 already found -- either in the same
file, or in another parsed file via that file's own import statements.
Anything else is recorded with ``resolved=False`` and the call exactly as
written (``raw_call`` / ``target``) -- never an invented target.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .models import ImportInfo, ParsedFile, Relationship, RelationshipType

__all__ = [
    "RepoIndex",
    "module_name_for_path",
    "extract_contains_relationships",
    "extract_call_relationships",
    "extract_relationships_for_file",
    "extract_repository_relationships",
]


# ---------------------------------------------------------------------------
# Module-name bookkeeping, needed to resolve calls across file boundaries
# ---------------------------------------------------------------------------


def module_name_for_path(file_path: str) -> str:
    """Turn a file path (as stored on ``ParsedFile.file_path``) into a dotted module name.

    Examples:
        ``refund_service.py``    -> ``refund_service``
        ``package/utils.py``     -> ``package.utils``
        ``package/__init__.py``  -> ``package``

    This is a plain, syntactic conversion -- it never inspects
    ``sys.path``/packaging config, so it only matches an import statement
    that names the module the same simple way.
    """
    posix_path = Path(file_path).as_posix()
    if posix_path == "__init__.py":
        return ""
    if posix_path.endswith("/__init__.py"):
        posix_path = posix_path[: -len("/__init__.py")]
    elif posix_path.endswith(".py"):
        posix_path = posix_path[: -len(".py")]
    return posix_path.replace("/", ".")


@dataclass
class RepoIndex:
    """Lookup tables built from every parsed file in a repository.

    Used only to resolve *cross-file* calls (e.g. a call to a name brought
    in via ``from foo import bar``). Extracting relationships from a single
    file in isolation doesn't need this at all.
    """

    functions_by_module: dict[str, set[str]] = field(default_factory=dict)
    classes_by_module: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, parsed_files: list[ParsedFile]) -> RepoIndex:
        index = cls()
        for pf in parsed_files:
            if pf.error:
                continue
            module = module_name_for_path(pf.file_path)
            index.functions_by_module[module] = {f.name for f in pf.functions}
            index.classes_by_module[module] = {c.name for c in pf.classes}
        return index

    def has_symbol(self, module: str, name: str) -> bool:
        """Whether `module` is a known parsed file that defines a function or class called `name`."""
        return name in self.functions_by_module.get(module, set()) or name in self.classes_by_module.get(
            module, set()
        )


# ---------------------------------------------------------------------------
# Structural relationships: File -> Class/Function, Class -> Method
# ---------------------------------------------------------------------------


def extract_contains_relationships(parsed_file: ParsedFile) -> list[Relationship]:
    """File-contains-Class, File-contains-Function, Class-contains-Method.

    Pure bookkeeping over facts Step 4 already extracted -- no source
    re-parsing needed, and every relationship produced here is always fully
    resolved (there's no ambiguity in "this class was found in this file").
    """
    relationships: list[Relationship] = []

    for cls in parsed_file.classes:
        relationships.append(
            Relationship(
                source=parsed_file.file_path,
                relationship_type=RelationshipType.CONTAINS,
                target=cls.name,
                source_file=parsed_file.file_path,
                line=cls.line,
            )
        )
        for method in cls.methods:
            relationships.append(
                Relationship(
                    source=cls.name,
                    relationship_type=RelationshipType.CONTAINS,
                    target=method.name,
                    source_file=parsed_file.file_path,
                    line=method.line,
                )
            )

    for func in parsed_file.functions:
        relationships.append(
            Relationship(
                source=parsed_file.file_path,
                relationship_type=RelationshipType.CONTAINS,
                target=func.name,
                source_file=parsed_file.file_path,
                line=func.line,
            )
        )

    return relationships


# ---------------------------------------------------------------------------
# Import bookkeeping local to one file, needed to resolve calls
# ---------------------------------------------------------------------------


def _import_lookup(imports: list[ImportInfo]) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Build two lookups from a file's imports, keyed by the *local* name they bind.

    Returns ``(from_imports, plain_imports)``:
        from_imports:  local_name -> (module, original_name)   for ``from X import Y as Z``
        plain_imports: local_name -> module                    for ``import X as Y``
    """
    from_imports: dict[str, tuple[str, str]] = {}
    plain_imports: dict[str, str] = {}

    for imp in imports:
        local_name = imp.alias or imp.name
        if imp.module is not None:
            from_imports[local_name] = (imp.module, imp.name)
        else:
            # `import foo.bar` (no alias) binds the top-level name `foo` in
            # real Python scoping; an explicit alias binds exactly that name.
            top_level_name = imp.alias or imp.name.split(".")[0]
            plain_imports[top_level_name] = imp.name

    return from_imports, plain_imports


# ---------------------------------------------------------------------------
# Call relationships: Function/Method -> calls -> Function/Method
# ---------------------------------------------------------------------------


def _calls_in_own_scope(node: ast.AST):
    """Yield every ``ast.Call`` directly in this function/method's own body.

    Deliberately does not descend into a nested function, async function,
    class, or lambda defined inside -- those are separate scopes that Step 4
    doesn't track as independent callable entities, so attributing their
    calls to the enclosing function would misrepresent who calls what.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _calls_in_own_scope(child)


def _unparse(node: ast.AST) -> str:
    """Best-effort source text for a node. Never raises."""
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _resolve_call(
    func_expr: ast.expr,
    *,
    parsed_file: ParsedFile,
    current_class: str | None,
    from_imports: dict[str, tuple[str, str]],
    plain_imports: dict[str, str],
    repo_index: RepoIndex | None,
) -> tuple[str, bool]:
    """Work out ``(target, resolved)`` for one call's callee expression.

    Resolution is attempted, in order, for:
      1. ``self.method()`` inside a class -- resolved if that method exists
         on the same class, per Step 4's own facts.
      2. a bare name matching a sibling top-level function in the same file.
      3. a bare name brought in via ``from X import name`` -- resolved
         cross-file via ``repo_index``, only when ``X`` is a repository file
         that actually defines ``name``.
      4. ``module.attr()`` where ``module`` is a plain ``import module``
         alias -- resolved cross-file the same way.
    Anything else -- including any call through an arbitrary object/variable
    (e.g. ``service.process()``), where we have no reliable way to know what
    that object actually is -- is left unresolved, with the target set to
    exactly what was written, never a guess.
    """
    if isinstance(func_expr, ast.Name):
        name = func_expr.id

        if any(f.name == name for f in parsed_file.functions):
            return name, True

        if name in from_imports:
            module, original_name = from_imports[name]
            if repo_index is not None and repo_index.has_symbol(module, original_name):
                return f"{module}.{original_name}", True

        return name, False

    if isinstance(func_expr, ast.Attribute) and isinstance(func_expr.value, ast.Name):
        base_name = func_expr.value.id

        if base_name == "self" and current_class:
            method_name = func_expr.attr
            owning_class = next((c for c in parsed_file.classes if c.name == current_class), None)
            if owning_class and any(m.name == method_name for m in owning_class.methods):
                return f"{current_class}.{method_name}", True
            # We know it's a self-call, but not that it lands on a method
            # Step 4 actually found (could be inherited, dynamic, etc.) --
            # report exactly what was written rather than guessing.
            return f"self.{method_name}", False

        if base_name in plain_imports:
            module = plain_imports[base_name]
            attr = func_expr.attr
            if repo_index is not None and repo_index.has_symbol(module, attr):
                return f"{module}.{attr}", True
            return f"{module}.{attr}", False

        # An attribute call on some other name (e.g. `service.process()`) --
        # we have no deterministic way to know what `service` actually is.
        return f"{base_name}.{func_expr.attr}", False

    # Anything more complex (chained attributes, a call's own result being
    # called, a subscript, etc.) -- unresolved, best-effort source text.
    return _unparse(func_expr), False


def extract_call_relationships(
    parsed_file: ParsedFile,
    *,
    source_path: str | Path,
    repo_index: RepoIndex | None = None,
) -> list[Relationship]:
    """CALLS relationships for one file.

    Re-parses the file at ``source_path`` to walk its call expressions,
    since Step 4's ``ParsedFile`` doesn't retain the AST. ``source_path`` is
    where to actually read the source from on disk, which may differ from
    ``parsed_file.file_path`` when that's stored relative to a repo root
    (see ``extract_repository_relationships``).
    """
    source = Path(source_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))

    from_imports, plain_imports = _import_lookup(parsed_file.imports)
    relationships: list[Relationship] = []

    def handle_callable(
        node: ast.FunctionDef | ast.AsyncFunctionDef, source_name: str, current_class: str | None
    ) -> None:
        for call in _calls_in_own_scope(node):
            target, resolved = _resolve_call(
                call.func,
                parsed_file=parsed_file,
                current_class=current_class,
                from_imports=from_imports,
                plain_imports=plain_imports,
                repo_index=repo_index,
            )
            relationships.append(
                Relationship(
                    source=source_name,
                    relationship_type=RelationshipType.CALLS,
                    target=target,
                    source_file=parsed_file.file_path,
                    line=call.lineno,
                    resolved=resolved,
                    raw_call=_unparse(call),
                )
            )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            handle_callable(node, node.name, current_class=None)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    handle_callable(item, f"{node.name}.{item.name}", current_class=node.name)

    return relationships


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def extract_relationships_for_file(
    parsed_file: ParsedFile,
    *,
    source_path: str | Path | None = None,
    repo_index: RepoIndex | None = None,
) -> list[Relationship]:
    """All relationships (CONTAINS + CALLS) for a single parsed file.

    ``source_path`` defaults to ``parsed_file.file_path`` itself, which is
    fine whenever that path is directly readable on disk (e.g. results from
    ``parse_python_file``). For results from ``parse_repository`` (where
    ``file_path`` is stored relative to the repo root), either pass the real
    path via ``source_path``, or just use
    ``extract_repository_relationships`` instead.

    A file that Step 4 couldn't parse (``parsed_file.error`` is set)
    produces no relationships -- there's nothing reliable to derive from it.
    """
    if parsed_file.error:
        return []

    relationships = extract_contains_relationships(parsed_file)
    relationships += extract_call_relationships(
        parsed_file,
        source_path=source_path if source_path is not None else parsed_file.file_path,
        repo_index=repo_index,
    )
    return relationships


def extract_repository_relationships(
    repo_path: str | Path, parsed_files: list[ParsedFile]
) -> list[Relationship]:
    """All relationships across every parsed file in a repository.

    This is the main Step 5 entry point: pass it ``repo_path`` plus the
    ``parse_repository(repo_path)`` result from Step 4, and get back every
    CONTAINS and CALLS relationship -- with cross-file calls resolved via
    import information wherever that's deterministically possible.
    """
    repo_index = RepoIndex.build(parsed_files)
    root = Path(repo_path)

    relationships: list[Relationship] = []
    for pf in parsed_files:
        relationships += extract_relationships_for_file(
            pf, source_path=root / pf.file_path, repo_index=repo_index
        )
    return relationships
