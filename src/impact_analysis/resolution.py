"""Resolving the changed code location -- Phase 5, requirement 1.

Accepts any of: a Phase 4 :class:`~src.fix_localization.models.FixLocalizationResult`
(used directly, nothing reconstructed), a Phase 3 :class:`~src.rca.models.CodeLocation`,
or a simple repository / file / method triple. Always ends at one real
Method or Function node in the graph, or an explicit reason it could not.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.fix_localization.models import FixLocalizationResult
from src.graph.connection import Neo4jConnection
from src.rca.code_resolution import resolve_repository_id
from src.rca.models import CodeLocation, ResolutionStatus

__all__ = ["resolve_changed_node"]


@dataclass
class _Resolved:
    node_id: str | None
    reason: str | None = None


def resolve_changed_node(
    connection: Neo4jConnection,
    *,
    localization: FixLocalizationResult | None = None,
    location: CodeLocation | None = None,
    repository: str | None = None,
    file_path: str | None = None,
    method: str | None = None,
    class_name: str | None = None,
) -> tuple[str | None, str | None]:
    """Returns ``(node_id, reason)``; ``node_id`` is None exactly when ``reason`` explains why."""
    if localization is not None:
        primary = localization.primary_location
        if primary is None or localization.resolution_status == ResolutionStatus.UNRESOLVED:
            return None, "The Phase 4 localization is UNRESOLVED, so there is no changed location to analyze"
        location = primary.location

    if location is not None:
        return _from_location(location)

    if method:
        result = _from_names(connection, repository, file_path, method, class_name)
        return result.node_id, result.reason

    return None, "No changed location was given (need a localization, a code location, or a method name)"


def _from_location(location: CodeLocation) -> tuple[str | None, str | None]:
    if location.resolution_status != ResolutionStatus.RESOLVED or not location.node_id:
        return None, location.reason or "The given code location is not resolved to a graph node"
    if location.node_label not in ("Method", "Function"):
        return None, (
            f"The location resolves to a {location.node_label}, not a Method or Function; "
            "class-level changes are not supported -- pass the specific method or function"
        )
    return location.node_id, None


def _from_names(
    connection: Neo4jConnection,
    repository: str | None,
    file_path: str | None,
    method: str,
    class_name: str | None,
) -> _Resolved:
    if "." in method and class_name is None:
        class_name, _, method = method.rpartition(".")

    repo_id, reason = resolve_repository_id(connection, repository)
    if repo_id is None:
        return _Resolved(None, reason)

    params = {"repo_id": repo_id, "file": file_path, "method": method, "class_name": class_name}
    candidates: list[str] = []

    candidates += [
        row["id"]
        for row in connection.execute_read(
            "MATCH (:Repository {id: $repo_id})-[:CONTAINS]->(f:File)-[:CONTAINS]->(c:Class)"
            "-[:CONTAINS]->(m:Method {name: $method}) "
            "WHERE ($file IS NULL OR f.path = $file) AND ($class_name IS NULL OR c.name = $class_name) "
            "RETURN DISTINCT m.id AS id",
            params,
        )
    ]
    if class_name is None:
        candidates += [
            row["id"]
            for row in connection.execute_read(
                "MATCH (:Repository {id: $repo_id})-[:CONTAINS]->(f:File)-[:CONTAINS]->(fn:Function {name: $method}) "
                "WHERE ($file IS NULL OR f.path = $file) "
                "RETURN DISTINCT fn.id AS id",
                params,
            )
        ]

    if not candidates:
        return _Resolved(None, f"No Method or Function named {method!r} found for the given repository/file/class")
    if len(candidates) > 1:
        return _Resolved(
            None,
            f"{len(candidates)} Methods/Functions named {method!r} match -- give a file path or class name "
            "to disambiguate",
        )
    return _Resolved(candidates[0])
