"""Exception -> Code Location -- Phase 3, requirement 3.

Maps one parsed :class:`~src.rca.models.StackFrame` onto an actual node in
the Phase 1/2 code graph (a Method, Function, or -- if neither can be
pinned down -- the containing Class), by querying Neo4j directly. Never
invents a location: any frame that can't be tied to a real, ingested file
and definition comes back ``UNRESOLVED`` with a plain-language ``reason``.

Resolution is entirely property-based Cypher (repository name/remote/path,
file path, definition line/name) -- it does not require recomputing the
Phase 2 identity hashes, so it works regardless of exactly how the caller
identifies "the repository" (name, remote URL, or local root path all
resolve the same way here).
"""

from __future__ import annotations

from src.graph.connection import Neo4jConnection

from .models import CodeLocation, ResolutionStatus, StackFrame

__all__ = ["resolve_stack_frame", "resolve_repository_id"]


def resolve_repository_id(connection: Neo4jConnection, repository: str | None) -> tuple[str | None, str | None]:
    """Find the Repository node id matching ``repository`` (name, remote URL,
    or root path). Returns ``(repo_id, reason)`` -- ``repo_id`` is ``None``
    with a ``reason`` when it can't be resolved unambiguously.

    When ``repository`` is ``None``: if exactly one Repository node exists in
    the graph, use it (still deterministic -- there is nothing else it could
    mean); if there are zero or more than one, refuse to guess.
    """
    if repository:
        rows = connection.execute_read(
            "MATCH (r:Repository) "
            "WHERE r.name = $repository OR r.remote_url = $repository OR r.root_path = $repository "
            "RETURN r.id AS id",
            {"repository": repository},
        )
    else:
        rows = connection.execute_read("MATCH (r:Repository) RETURN r.id AS id")

    if not rows:
        return None, f"No Repository node matches {repository!r}" if repository else "No Repository node found in the graph"
    if len(rows) > 1:
        return None, (
            f"Repository {repository!r} is ambiguous ({len(rows)} matching nodes)"
            if repository
            else "Multiple Repository nodes exist and none was specified -- cannot pick one without guessing"
        )
    return rows[0]["id"], None


def _path_candidates(connection: Neo4jConnection, repo_id: str, frame_file: str) -> list[dict]:
    """Files in this repository whose path matches ``frame_file`` exactly, or
    (when the stack trace carries an absolute/partially-qualified path) whose
    path is a suffix of it -- never a substring/fuzzy match."""
    exact = connection.execute_read(
        "MATCH (r:Repository {id: $repo_id})-[:CONTAINS]->(f:File) WHERE f.path = $path "
        "RETURN f.id AS id, f.path AS path",
        {"repo_id": repo_id, "path": frame_file},
    )
    if exact:
        return exact

    all_files = connection.execute_read(
        "MATCH (r:Repository {id: $repo_id})-[:CONTAINS]->(f:File) RETURN f.id AS id, f.path AS path",
        {"repo_id": repo_id},
    )
    normalized = frame_file.replace("\\", "/")
    return [row for row in all_files if normalized.endswith("/" + row["path"]) or normalized == row["path"]]


def resolve_stack_frame(connection: Neo4jConnection, repository: str | None, frame: StackFrame) -> CodeLocation:
    """Resolve one :class:`StackFrame` to a :class:`CodeLocation`.

    Steps: find the Repository -> find the File (by path, exact then
    suffix match) -> find the innermost Method/Function/Class whose
    definition line is the closest one at-or-before the frame's line
    (optionally narrowed by the frame's function name, when it names a
    known definition in that file).
    """
    repo_id, reason = resolve_repository_id(connection, repository)
    if repo_id is None:
        return CodeLocation(resolution_status=ResolutionStatus.UNRESOLVED, repository=repository, reason=reason)

    file_candidates = _path_candidates(connection, repo_id, frame.file)
    if not file_candidates:
        return CodeLocation(
            resolution_status=ResolutionStatus.UNRESOLVED,
            repository=repository,
            reason=f"No File node in this repository matches path {frame.file!r}",
        )
    if len(file_candidates) > 1:
        return CodeLocation(
            resolution_status=ResolutionStatus.UNRESOLVED,
            repository=repository,
            reason=f"Path {frame.file!r} matches {len(file_candidates)} files in this repository -- ambiguous",
        )

    file_id = file_candidates[0]["id"]
    file_path = file_candidates[0]["path"]

    definitions = connection.execute_read(
        "MATCH (f:File {id: $file_id}) "
        "OPTIONAL MATCH (f)-[:CONTAINS]->(fn:Function) "
        "OPTIONAL MATCH (f)-[:CONTAINS]->(c:Class) "
        "OPTIONAL MATCH (c)-[:CONTAINS]->(m:Method) "
        "RETURN "
        "  collect(DISTINCT {id: fn.id, name: fn.name, line: fn.line_number, label: 'Function'}) AS functions, "
        "  collect(DISTINCT {id: c.id, name: c.name, line: c.line_number, label: 'Class'}) AS classes, "
        "  collect(DISTINCT {id: m.id, name: m.name, class_id: m.class_id, line: m.line_number, label: 'Method', "
        "  class_name: c.name}) AS methods",
        {"file_id": file_id},
    )

    candidates: list[dict] = []
    if definitions:
        row = definitions[0]
        for entry in row.get("functions") or []:
            if entry.get("id"):
                candidates.append(entry)
        for entry in row.get("classes") or []:
            if entry.get("id"):
                candidates.append(entry)
        for entry in row.get("methods") or []:
            if entry.get("id"):
                qualified = f"{entry.get('class_name')}.{entry.get('name')}" if entry.get("class_name") else entry.get("name")
                candidates.append({**entry, "qualified_name": qualified})

    if not candidates:
        return CodeLocation(
            resolution_status=ResolutionStatus.PARTIALLY_RESOLVED,
            repository=repository,
            file_path=file_path,
            file_id=file_id,
            line=frame.line,
            reason="File resolved, but it has no parsed Class/Method/Function definitions in the graph",
        )

    chosen = _choose_definition(candidates, frame)
    if chosen is None:
        return CodeLocation(
            resolution_status=ResolutionStatus.PARTIALLY_RESOLVED,
            repository=repository,
            file_path=file_path,
            file_id=file_id,
            line=frame.line,
            reason="File resolved, but no definition starts at or before the failing line",
        )

    qualified_name = chosen.get("qualified_name", chosen.get("name"))
    return CodeLocation(
        resolution_status=ResolutionStatus.RESOLVED,
        repository=repository,
        file_path=file_path,
        file_id=file_id,
        line=frame.line,
        node_id=chosen["id"],
        node_label=chosen["label"],
        qualified_name=qualified_name,
    )


def _choose_definition(candidates: list[dict], frame: StackFrame) -> dict | None:
    """Pick the innermost definition enclosing ``frame.line``.

    Prefers an exact name match with the frame's function (methods'
    ``name`` is the bare method name, e.g. ``"process_refund"``, which is
    exactly what a traceback's "in ..." records) whose line is at-or-before
    the frame's line; falls back to the definition with the greatest
    ``line`` that is still <= the frame's line -- the nearest enclosing
    definition, the same heuristic ``ast`` scoping implies.
    """
    named = [c for c in candidates if c.get("line") is not None and c["line"] <= frame.line and c.get("name") == frame.function]
    pool = named if named else [c for c in candidates if c.get("line") is not None and c["line"] <= frame.line]
    if not pool:
        return None
    return max(pool, key=lambda c: c["line"])
