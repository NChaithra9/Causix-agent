"""Architecture facts that Python source states literally, via ``ast``.

Only literal, unambiguous constructs are recognised:

* **APIs** -- ``@x.get("/p")`` / ``.post`` / ``.put`` / ``.delete`` / ``.patch`` and
  ``@x.route("/p", methods=[...])`` decorators with a string-literal path
  (FastAPI/Flask/Starlette style). A same-file ``APIRouter(prefix=...)`` /
  ``Blueprint(url_prefix=...)`` prefix is applied; prefixes added elsewhere
  (e.g. ``include_router(prefix=...)``) are not.
* **Outbound HTTP calls** -- ``requests``/``httpx``/``client``/``session`` calls whose
  literal URL host is a *known* service name. Unknown hosts are ignored,
  never guessed.
* **Tables** -- ORM classes with ``__tablename__ = "<literal>"``.

Anything dynamic (a non-literal route path) is reported as unresolved.
"""

from __future__ import annotations

import ast
import re

from src.fix_localization.related_tests import is_test_path

from ..builder import SnapshotBuilder
from ..models import ArchEntity, EntityType, GraphRelationship, api_key
from ..source import SourceTree
from .context import ExtractionContext, add_relationship, ensure_entity, make_evidence

__all__ = ["extract_code"]

_VERBS = {"get", "post", "put", "delete", "patch", "head", "options"}
_ROUTE_FACTORIES = {"FastAPI", "APIRouter", "Flask", "Blueprint", "Starlette"}
_PREFIX_KEYWORDS = {"prefix", "url_prefix"}
_HTTP_CLIENTS = {"requests", "httpx", "client", "session", "http"}
_HOST = re.compile(r"^https?://([^/:?#]+)", re.IGNORECASE)


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _url_text(node: ast.AST | None) -> str | None:
    """A string literal, or the literal head of an f-string."""
    if isinstance(node, ast.JoinedStr) and node.values:
        return _literal(node.values[0])
    return _literal(node)


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_routers(module: ast.Module) -> tuple[dict[str, str], set[str]]:
    """Module-level ``name = FastAPI()/APIRouter(prefix=..)/Blueprint(..)`` assignments."""
    prefixes: dict[str, str] = {}
    objects: set[str] = set()
    for node in module.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        if _call_name(node.value) not in _ROUTE_FACTORIES:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                objects.add(target.id)
                for keyword in node.value.keywords:
                    value = _literal(keyword.value)
                    if keyword.arg in _PREFIX_KEYWORDS and value is not None:
                        prefixes[target.id] = value
    return prefixes, objects


def _http_methods(attr: str, call: ast.Call) -> list[str]:
    if attr in _VERBS:
        return [attr.upper()]
    for keyword in call.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, ast.List | ast.Tuple | ast.Set):
            methods = [_literal(item) for item in keyword.value.elts]
            if methods and all(methods):
                return sorted({m.upper() for m in methods if m})
    return ["GET"]


class _Visitor(ast.NodeVisitor):
    def __init__(
        self, path: str, ctx: ExtractionContext, builder: SnapshotBuilder, module: ast.Module
    ) -> None:
        self.path = path
        self.ctx = ctx
        self.builder = builder
        self.prefixes, self.route_objects = _module_routers(module)
        self.scope: list[str] = []
        self.service = builder.snapshot.entities[f"service:{ctx.service}"]
        names = set(ctx.known_services) | {e.name for e in builder.snapshot.services}
        self.known = {n.lower(): n for n in names if n != ctx.service}

    # ---- scopes ----------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._tables(node)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                self._route(decorator, qualname)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    # ---- APIs ------------------------------------------------------------

    def _route(self, decorator: ast.Call, handler: str) -> None:
        func = decorator.func
        if not (isinstance(func, ast.Attribute) and func.attr in _VERBS | {"route", "api_route"}):
            return
        receiver = func.value.id if isinstance(func.value, ast.Name) else None
        first = decorator.args[0] if decorator.args else None
        path = _literal(first)
        if path is None:
            if receiver in self.route_objects:
                self.builder.unresolved(
                    f"route path of {handler!r} is not a string literal "
                    f"(line {decorator.lineno}); the API could not be determined",
                    self.path,
                )
            return
        if not path.startswith("/"):
            return  # e.g. cache.get("key"): not a route
        full_path = self.prefixes.get(receiver or "", "") + path
        for method in _http_methods(func.attr, decorator):
            evidence = make_evidence(
                self.ctx,
                "route_decorator",
                f"@{ast.unparse(func)}({path!r}) on {handler}",
                file=self.path,
                line=decorator.lineno,
                relationship=GraphRelationship.EXPOSES,
            )
            api = ensure_entity(
                self.builder,
                EntityType.API,
                f"{method} {full_path}",
                evidence,
                key=api_key(method, full_path),
                properties={"method": method, "path": full_path},
                meta={"handler": handler, "service": self.ctx.service, "file": self.path},
            )
            add_relationship(self.builder, GraphRelationship.EXPOSES, self.service, api, evidence)

    # ---- outbound calls --------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        self._outbound(node)
        self.generic_visit(node)

    def _outbound(self, call: ast.Call) -> None:
        func = call.func
        if not isinstance(func, ast.Attribute) or not self.known:
            return
        if func.attr not in _VERBS | {"request"}:
            return
        if ast.unparse(func.value).rsplit(".", 1)[-1] not in _HTTP_CLIENTS:
            return
        url_arg = call.args[1] if func.attr == "request" and len(call.args) > 1 else None
        url = _url_text(
            url_arg if func.attr == "request" else (call.args[0] if call.args else None)
        )
        match = _HOST.match(url or "")
        if not match:
            return
        host = match.group(1).lower()
        if host not in self.known:
            return  # an unknown host is never guessed to be a service
        target_name = self.known[host]
        evidence = make_evidence(
            self.ctx,
            "http_call",
            f"{ast.unparse(func)}({url!r}...) calls service {target_name!r}",
            file=self.path,
            line=call.lineno,
            relationship=GraphRelationship.CALLS,
        )
        target = ensure_entity(self.builder, EntityType.SERVICE, target_name, evidence)
        add_relationship(self.builder, GraphRelationship.CALLS, self.service, target, evidence)

    # ---- tables ----------------------------------------------------------

    def _tables(self, node: ast.ClassDef) -> None:
        for statement in node.body:
            if not (isinstance(statement, ast.Assign) and _literal(statement.value)):
                continue
            if not any(
                isinstance(t, ast.Name) and t.id == "__tablename__" for t in statement.targets
            ):
                continue
            name = _literal(statement.value)
            assert name is not None
            evidence = make_evidence(
                self.ctx,
                "orm_model",
                f"class {node.name} maps table {name!r}",
                file=self.path,
                line=statement.lineno,
                relationship=GraphRelationship.ACCESSES,
            )
            table: ArchEntity = ensure_entity(self.builder, EntityType.TABLE, name, evidence)
            add_relationship(
                self.builder, GraphRelationship.ACCESSES, self.service, table, evidence
            )


def extract_code(tree: SourceTree, ctx: ExtractionContext, builder: SnapshotBuilder) -> None:
    for path in tree.paths():
        if not path.endswith(".py") or is_test_path(path):
            continue
        text = tree.read_text(path)
        if text is None:
            builder.unresolved("could not read the source file", path)
            continue
        try:
            module = ast.parse(text, filename=path)
        except SyntaxError as exc:
            builder.unresolved(f"could not parse: {exc.msg} (line {exc.lineno})", path)
            continue
        _Visitor(path, ctx, builder, module).visit(module)
