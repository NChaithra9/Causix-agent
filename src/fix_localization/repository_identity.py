"""Repository Identification -- Phase 4, requirement 3.

Resolves the real Repository node (name, id, remote URL, root path) backing
a :class:`~src.rca.models.CodeLocation`, by property-based Cypher against
the Phase 2 graph -- the same convention ``src.rca.code_resolution`` already
uses. Never invents a repository: an unresolved lookup comes back
UNRESOLVED with nothing fabricated.
"""

from __future__ import annotations

from src.graph.connection import Neo4jConnection

from .models import RepositoryIdentity, ResolutionStatus

__all__ = ["get_repository_identity"]


def get_repository_identity(connection: Neo4jConnection, repository: str | None) -> RepositoryIdentity:
    if not repository:
        return RepositoryIdentity(resolution_status=ResolutionStatus.UNRESOLVED)

    rows = connection.execute_read(
        "MATCH (r:Repository) "
        "WHERE r.name = $repository OR r.remote_url = $repository OR r.root_path = $repository "
        "RETURN r.id AS id, r.name AS name, r.remote_url AS remote_url, r.root_path AS root_path",
        {"repository": repository},
    )
    if len(rows) != 1:
        return RepositoryIdentity(resolution_status=ResolutionStatus.UNRESOLVED)

    row = rows[0]
    return RepositoryIdentity(
        resolution_status=ResolutionStatus.RESOLVED,
        name=row["name"],
        repository_id=row["id"],
        remote_url=row["remote_url"],
        root_path=row["root_path"],
    )
