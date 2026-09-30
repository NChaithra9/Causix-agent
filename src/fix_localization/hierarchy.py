"""Class and Function/Method Localization -- Phase 4, requirements 5 & 6.

Given a resolved :class:`~src.rca.models.CodeLocation`, determines the
exact owning class (if any) and the exact method/function, each with its
precise source line range -- via ``ast`` on the real source file, the same
approach ``src.rca.code_context`` already uses for the enclosing function.
This module additionally resolves the *class's own* line range, which
Phase 3 never needed.

Never invents a class where there is none: a location that resolves to a
top-level function reports ``class_localization`` as UNRESOLVED with
``class_name=None`` on the method localization -- exactly the "no owning
class" case Phase 4 asks to distinguish clearly.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.rca.models import CodeLocation, ResolutionStatus

from .models import ClassLocalization, MethodLocalization

__all__ = ["localize_class_and_method"]


def localize_class_and_method(
    repo_root: str | Path, location: CodeLocation
) -> tuple[ClassLocalization, MethodLocalization]:
    unresolved_class = ClassLocalization(resolution_status=ResolutionStatus.UNRESOLVED)
    unresolved_method = MethodLocalization(resolution_status=ResolutionStatus.UNRESOLVED)

    if location.resolution_status == ResolutionStatus.UNRESOLVED or not location.file_path:
        return unresolved_class, unresolved_method

    file_path = Path(repo_root) / location.file_path
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError):
        return unresolved_class, unresolved_method

    # A location resolved straight to a Class node (no method/function pinned
    # down for the failing line) -- report the class, and a method that
    # explicitly has nothing to point to.
    if location.node_label == "Class":
        class_def = _find_class(tree, location.qualified_name)
        if class_def is None:
            return unresolved_class, unresolved_method
        class_loc = ClassLocalization(
            resolution_status=ResolutionStatus.RESOLVED,
            name=class_def.name,
            file_path=location.file_path,
            start_line=class_def.lineno,
            end_line=getattr(class_def, "end_lineno", class_def.lineno),
        )
        return class_loc, MethodLocalization(resolution_status=ResolutionStatus.UNRESOLVED)

    target_line = location.line
    if target_line is None:
        return unresolved_class, unresolved_method

    best_func: ast.AST | None = None
    best_class: ast.ClassDef | None = None

    def _visit(node: ast.AST, current_class: ast.ClassDef | None) -> None:
        nonlocal best_func, best_class
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                _visit(child, child)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = child.lineno
                end = getattr(child, "end_lineno", child.lineno)
                if start <= target_line <= end:
                    if best_func is None or start >= getattr(best_func, "lineno"):
                        best_func = child
                        best_class = current_class
                _visit(child, current_class)

    _visit(tree, None)

    if best_func is None:
        return unresolved_class, unresolved_method

    method_loc = MethodLocalization(
        resolution_status=ResolutionStatus.RESOLVED,
        name=best_func.name,
        qualified_name=f"{best_class.name}.{best_func.name}" if best_class else best_func.name,
        kind="Method" if best_class else "Function",
        class_name=best_class.name if best_class else None,
        file_path=location.file_path,
        start_line=best_func.lineno,
        end_line=getattr(best_func, "end_lineno", best_func.lineno),
        failing_line=target_line,
    )

    if best_class is None:
        return ClassLocalization(resolution_status=ResolutionStatus.UNRESOLVED), method_loc

    class_loc = ClassLocalization(
        resolution_status=ResolutionStatus.RESOLVED,
        name=best_class.name,
        file_path=location.file_path,
        start_line=best_class.lineno,
        end_line=getattr(best_class, "end_lineno", best_class.lineno),
    )
    return class_loc, method_loc


def _find_class(tree: ast.Module, name: str | None) -> ast.ClassDef | None:
    if not name:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None
