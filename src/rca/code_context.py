"""Code Context -- Phase 3, requirement 4.

Once a :class:`~src.rca.models.CodeLocation` is resolved, gather
deterministic context around it: the containing function/method, the
containing class (if any), and a small window of the actual surrounding
source lines. Facts only -- no explanation of what the code does, no
guessing when the source file isn't available.

Reads the real source file from disk (via the repository's root path) and
uses ``ast`` -- exactly like ``src.parser.python_parser`` -- to find the
precise start/end line range of the enclosing definition, rather than
re-deriving it from the (start-line-only) graph properties.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .models import CodeContext, CodeLocation, ResolutionStatus

__all__ = ["build_code_context"]

DEFAULT_CONTEXT_LINES = 3


def build_code_context(
    repo_root: str | Path, location: CodeLocation, *, context_lines: int = DEFAULT_CONTEXT_LINES
) -> CodeContext:
    """Build a :class:`CodeContext` for a RESOLVED (or PARTIALLY_RESOLVED)
    :class:`CodeLocation`. Returns ``UNRESOLVED`` context, with nothing
    fabricated, when the location itself is unresolved or the source file
    can't actually be read."""
    if location.resolution_status == ResolutionStatus.UNRESOLVED or not location.file_path:
        return CodeContext(resolution_status=ResolutionStatus.UNRESOLVED)

    file_path = Path(repo_root) / location.file_path
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError:
        return CodeContext(
            resolution_status=ResolutionStatus.PARTIALLY_RESOLVED,
        )

    source_lines_all = source.splitlines()
    target_line = location.line

    containing_function: str | None = None
    containing_class: str | None = None
    def_start: int | None = None
    def_end: int | None = None

    if target_line is not None:
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            tree = None

        if tree is not None:
            best_func: ast.AST | None = None
            best_class_name: str | None = None

            def _visit(node: ast.AST, current_class: str | None) -> None:
                nonlocal best_func, best_class_name
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.ClassDef):
                        _visit(child, child.name)
                    elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        start = child.lineno
                        end = getattr(child, "end_lineno", child.lineno)
                        if start <= target_line <= end:
                            if best_func is None or start >= getattr(best_func, "lineno"):
                                best_func = child
                                best_class_name = current_class
                        _visit(child, current_class)

            _visit(tree, None)

            if best_func is not None:
                containing_function = best_func.name
                containing_class = best_class_name
                def_start = best_func.lineno
                def_end = getattr(best_func, "end_lineno", best_func.lineno)

    if target_line is None:
        return CodeContext(resolution_status=ResolutionStatus.PARTIALLY_RESOLVED)

    window_start = max(1, target_line - context_lines)
    window_end = min(len(source_lines_all), target_line + context_lines)
    snippet = source_lines_all[window_start - 1 : window_end]

    status = ResolutionStatus.RESOLVED if containing_function else ResolutionStatus.PARTIALLY_RESOLVED
    return CodeContext(
        resolution_status=status,
        containing_function=containing_function,
        containing_class=containing_class,
        start_line=def_start if def_start is not None else window_start,
        end_line=def_end if def_end is not None else window_end,
        source_lines=snippet,
    )
