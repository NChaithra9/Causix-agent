"""Dependency Traversal -- Phase 3, requirement 7.

Uses the existing ``File -IMPORTS-> File`` relationships (Phase 2) to find
components related to the failing file. Only ever reports what's actually
represented in the graph -- never infers an architectural relationship the
ingestion pipeline didn't already record.
"""

from __future__ import annotations

from src.graph.connection import Neo4jConnection

from .models import DependencyResult, ResolutionStatus

__all__ = ["traverse_dependencies"]


def traverse_dependencies(connection: Neo4jConnection, file_id: str) -> DependencyResult:
    """Direct (one-hop) IMPORTS relationships in both directions for ``file_id``."""
    file_rows = connection.execute_read("MATCH (f:File {id: $id}) RETURN f.path AS path", {"id": file_id})
    if not file_rows:
        return DependencyResult(resolution_status=ResolutionStatus.UNRESOLVED, file_id=file_id)

    imports_rows = connection.execute_read(
        "MATCH (f:File {id: $id})-[:IMPORTS]->(dep:File) RETURN dep.path AS path", {"id": file_id}
    )
    imported_by_rows = connection.execute_read(
        "MATCH (dep:File)-[:IMPORTS]->(f:File {id: $id}) RETURN dep.path AS path", {"id": file_id}
    )

    imports = sorted({row["path"] for row in imports_rows})
    imported_by = sorted({row["path"] for row in imported_by_rows})

    status = ResolutionStatus.RESOLVED if (imports or imported_by) else ResolutionStatus.PARTIALLY_RESOLVED
    return DependencyResult(
        resolution_status=status,
        file_id=file_id,
        file_path=file_rows[0]["path"],
        imports=imports,
        imported_by=imported_by,
    )
