"""Deterministic Python source parsing, built on the standard library ``ast`` module.

This is the entry point for Step 4 of RootFix's pipeline:

    repository path -> find .py files -> parse each file -> structured facts

Nothing in this module uses an LLM or guesses anything: every fact returned
comes straight from Python's abstract syntax tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .models import ClassInfo, FunctionInfo, ImportInfo, MethodInfo, ParsedFile, SourceFileInfo

# Directories we never want to walk into when scanning a repository -- noise,
# not source code.
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    "site-packages",
}


def find_python_files(
    repo_path: str | Path, excluded_dirs: set[str] | None = None
) -> list[Path]:
    """Recursively find every ``.py`` file under ``repo_path``.

    Directories in ``excluded_dirs`` (default: :data:`DEFAULT_EXCLUDED_DIRS`)
    are skipped entirely, so virtual environments, caches, and similar
    clutter never get parsed.
    """
    excluded = excluded_dirs if excluded_dirs is not None else DEFAULT_EXCLUDED_DIRS
    root = Path(repo_path)
    py_files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in excluded for part in path.parts):
            continue
        py_files.append(path)
    return py_files


def _function_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the argument names of a function/method definition, in order.

    Covers positional-only, regular, ``*args``, keyword-only, and
    ``**kwargs`` parameters. Default values and type annotations are not
    captured -- names only, to keep this step simple.
    """
    a = node.args
    names = [arg.arg for arg in a.posonlyargs]
    names += [arg.arg for arg in a.args]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    names += [arg.arg for arg in a.kwonlyargs]
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    return names


def _extract_imports(tree: ast.Module) -> list[ImportInfo]:
    """Extract every ``import`` and ``from ... import ...`` statement in a module.

    Uses ``ast.walk`` (not just top-level statements) so imports nested
    inside ``if``/``try`` blocks -- a common real-world pattern -- are still
    picked up.
    """
    imports: list[ImportInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # e.g. `import os` or `import os.path as osp`
            for alias in node.names:
                imports.append(
                    ImportInfo(module=None, name=alias.name, alias=alias.asname, line=node.lineno)
                )
        elif isinstance(node, ast.ImportFrom):
            # e.g. `from foo.bar import baz`; `node.level` > 0 for relative
            # imports like `from . import x` or `from ..pkg import y`.
            module = ("." * node.level) + (node.module or "")
            for alias in node.names:
                imports.append(
                    ImportInfo(module=module, name=alias.name, alias=alias.asname, line=node.lineno)
                )
    return imports


def _extract_classes_and_functions(
    tree: ast.Module,
) -> tuple[list[ClassInfo], list[FunctionInfo]]:
    """Extract classes (with their methods) and top-level functions.

    Only walks ``tree.body`` (top-level statements), not the full tree, so
    that:
      * a method inside a class is never also reported as a top-level function;
      * a function nested inside another function is not reported at all
        (out of scope for this step -- see the module docstring).
    """
    classes: list[ClassInfo] = []
    functions: list[FunctionInfo] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods: list[MethodInfo] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        MethodInfo(
                            name=item.name,
                            class_name=node.name,
                            line=item.lineno,
                            args=_function_args(item),
                            is_async=isinstance(item, ast.AsyncFunctionDef),
                        )
                    )
            bases = [ast.unparse(base) for base in node.bases]
            classes.append(ClassInfo(name=node.name, line=node.lineno, methods=methods, bases=bases))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(
                FunctionInfo(
                    name=node.name,
                    line=node.lineno,
                    args=_function_args(node),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                )
            )

    return classes, functions


def parse_python_file(file_path: str | Path) -> ParsedFile:
    """Parse a single ``.py`` file into a :class:`~src.parser.models.ParsedFile`.

    Never raises on a file that fails to parse (e.g. a syntax error): the
    problem is recorded on ``ParsedFile.error`` instead, so scanning an
    entire repository doesn't abort because of one bad file.
    """
    path = Path(file_path)
    source = path.read_text(encoding="utf-8")

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return ParsedFile(file_path=str(path), error=f"SyntaxError: {exc}")

    imports = _extract_imports(tree)
    classes, functions = _extract_classes_and_functions(tree)

    return ParsedFile(file_path=str(path), imports=imports, classes=classes, functions=functions)


def parse_repository(repo_path: str | Path) -> list[ParsedFile]:
    """Scan a cloned repository and parse every ``.py`` file in it.

    This is the main entry point for Step 4: given the path to an
    already-cloned repository (Steps 1-3), return one ``ParsedFile`` per
    Python source file found, with file paths reported relative to
    ``repo_path`` where possible.
    """
    root = Path(repo_path)
    results: list[ParsedFile] = []
    for file_path in find_python_files(root):
        parsed = parse_python_file(file_path)
        try:
            parsed.file_path = str(file_path.relative_to(root))
        except ValueError:
            # file_path wasn't actually under root (shouldn't normally
            # happen) -- fall back to whatever parse_python_file recorded.
            pass
        results.append(parsed)
    return results


def scan_source_files(
    repo_path: str | Path, excluded_dirs: set[str] | None = None
) -> list[SourceFileInfo]:
    """Discover source files under ``repo_path`` and record lightweight metadata about each.

    This is the "Repository File Extraction" step: it answers "what files
    are here, and what are they" (path, name, language, size) without
    parsing anything -- ``parse_repository`` is the next step, which does.
    Currently Python-only (``language="python"``), and reuses
    :func:`find_python_files` for discovery, so exclusion rules (``.git``,
    ``.venv``, ``__pycache__``, etc.) stay identical between the two.
    """
    root = Path(repo_path)
    results: list[SourceFileInfo] = []

    for file_path in find_python_files(root, excluded_dirs=excluded_dirs):
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
        except OSError:
            line_count = 0

        try:
            relative_path = str(file_path.relative_to(root))
        except ValueError:
            relative_path = str(file_path)

        results.append(
            SourceFileInfo(
                absolute_path=str(file_path.resolve()),
                relative_path=relative_path,
                file_name=file_path.name,
                language="python",
                line_count=line_count,
            )
        )

    return results
