"""Split Python source into function/class/method chunks for embedding."""
import ast
from pathlib import Path

from pydantic import BaseModel

SKIP_DIRS = {".venv", "venv", "env", "__pycache__", "site-packages", "node_modules", ".git"}


class CodeChunk(BaseModel):
    id: str            # stable id, same as location
    file: str          # path relative to the repo root
    name: str          # "func", "Class" or "Class.method"
    kind: str          # "function" | "class" | "method"
    start_line: int
    end_line: int
    text: str

    @property
    def location(self) -> str:
        return f"{self.file}::{self.name}"


def _make(rel: str, name: str, kind: str, node: ast.AST, source: str) -> CodeChunk:
    return CodeChunk(
        id=f"{rel}::{name}", file=rel, name=name, kind=kind,
        start_line=node.lineno, end_line=node.end_lineno or node.lineno,
        text=ast.get_source_segment(source, node) or "",
    )


def chunk_python_source(source: str, rel_path: str) -> list[CodeChunk]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    chunks: list[CodeChunk] = []
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, funcs):
            chunks.append(_make(rel_path, node.name, "function", node, source))
        elif isinstance(node, ast.ClassDef):
            chunks.append(_make(rel_path, node.name, "class", node, source))
            for item in node.body:
                if isinstance(item, funcs):
                    chunks.append(_make(rel_path, f"{node.name}.{item.name}", "method", item, source))
    return chunks


def chunk_repository(root: str | Path) -> list[CodeChunk]:
    root = Path(root)
    chunks: list[CodeChunk] = []
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(p in SKIP_DIRS or p.startswith(".") for p in rel_parts[:-1]):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        chunks.extend(chunk_python_source(source, path.relative_to(root).as_posix()))
    return chunks
