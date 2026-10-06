"""Related Tests -- Phase 4, requirement 11.

Uses the existing Phase 1/2 graph directly: a repository's test files are
identified by a reliable, deterministic path/name convention
(``test_*.py`` / ``*_test.py``, or anything under a ``tests``/``test``
directory), and their already-ingested top-level Function nodes (Phase 1
parses test files exactly like any other ``.py`` file -- nothing new to
ingest here) are matched against the localized method/function name by an
explicit substring rule -- never semantic similarity, never invented.
"""

from __future__ import annotations

import re

from src.graph.connection import Neo4jConnection

from .models import RelatedTest

__all__ = ["find_related_tests", "is_test_path"]

_TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]+\.py|[^/]+_test\.py)$")
_TEST_DIR_RE = re.compile(r"(^|/)tests?/")


def _looks_like_test_file(path: str) -> bool:
    return bool(_TEST_FILE_RE.search(path)) or bool(_TEST_DIR_RE.search(path))


def is_test_path(path: str) -> bool:
    """Whether ``path`` follows the repository-structure convention for a test
    file (public so Phase 5's impact analysis applies the identical rule)."""
    return _looks_like_test_file(path)


def find_related_tests(connection: Neo4jConnection, repository_id: str | None, target_name: str | None) -> list[RelatedTest]:
    """``target_name`` is the bare method/function/class name being localized
    (e.g. ``"validate_payment"``). Returns [] rather than guessing when
    either input is missing, or nothing matches."""
    if not repository_id or not target_name:
        return []

    rows = connection.execute_read(
        "MATCH (r:Repository {id: $repo_id})-[:CONTAINS]->(f:File) "
        "OPTIONAL MATCH (f)-[:CONTAINS]->(fn:Function) "
        "OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class)-[:CONTAINS]->(m:Method) "
        "RETURN f.path AS file_path, "
        "collect(DISTINCT fn.name) AS function_names, "
        "collect(DISTINCT m.name) AS method_names",
        {"repo_id": repository_id},
    )

    related: list[RelatedTest] = []
    for row in rows:
        file_path = row["file_path"]
        if not file_path or not _looks_like_test_file(file_path):
            continue
        candidate_names = [n for n in (row.get("function_names") or []) if n] + [
            n for n in (row.get("method_names") or []) if n
        ]
        for name in candidate_names:
            if target_name in name:
                related.append(RelatedTest(test_file=file_path, test_function=name, target=target_name))

    return related
