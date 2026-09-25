"""Deterministic code intelligence for RootFix: parsing (Step 4) + relationships (Step 5).

Step 4 scans a repository's ``.py`` files and extracts structured facts --
imports, classes, methods, top-level functions -- using Python's built-in
``ast`` module. Step 5 takes those facts and derives deterministic
relationships between them (a file containing a class, a class containing a
method, a function/method calling another one).

No LLM, embeddings, or semantic guessing are used anywhere in this package.
Everything returned here is a fact read directly off the source code's
syntax tree, or a bookkeeping relationship derived from such facts.

Public API:
    Step 4 -- parsing:
        parse_repository(repo_path) -> list[ParsedFile]
        parse_python_file(file_path) -> ParsedFile
        find_python_files(repo_path) -> list[Path]

    Step 5 -- relationships:
        extract_repository_relationships(repo_path, parsed_files) -> list[Relationship]
        extract_relationships_for_file(parsed_file, ...) -> list[Relationship]
"""

from .models import (
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    MethodInfo,
    ParsedFile,
    Relationship,
    RelationshipType,
    SourceFileInfo,
)
from .python_parser import find_python_files, parse_python_file, parse_repository, scan_source_files
from .relationships import (
    RepoIndex,
    extract_call_relationships,
    extract_contains_relationships,
    extract_relationships_for_file,
    extract_repository_relationships,
    module_name_for_path,
)

__all__ = [
    # models
    "ClassInfo",
    "FunctionInfo",
    "ImportInfo",
    "MethodInfo",
    "ParsedFile",
    "Relationship",
    "RelationshipType",
    "SourceFileInfo",
    # Step 4 -- parsing
    "find_python_files",
    "parse_python_file",
    "parse_repository",
    "scan_source_files",
    # Step 5 -- relationships
    "RepoIndex",
    "extract_call_relationships",
    "extract_contains_relationships",
    "extract_relationships_for_file",
    "extract_repository_relationships",
    "module_name_for_path",
]
