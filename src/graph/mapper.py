"""Graph Mapper -- Phase 2, requirement 10: translates Phase 1's structured
facts into plain, DB-agnostic graph records (`NodeRecord`/`RelationshipRecord`),
with no Neo4j calls anywhere in this module. `src.graph.ingestion` is the
only place that turns these into actual Cypher.

Structural relationships (Repository-CONTAINS->File, File-CONTAINS->Class/
Function, Class-CONTAINS->Method) are built directly from Phase 1's
structured objects (`SourceFileInfo`, `ParsedFile`, `ClassInfo`, ...) --
there's no ambiguity in "this class was found in this file", so there's
nothing to resolve.

CALLS and IMPORTS relationships come from Phase 1's already-resolved
`Relationship` facts (`repository.relationships`). Only ``resolved=True``
relationships ever become graph edges: an unresolved call (Phase 1 couldn't
tie it to a real function/method) never gets an edge to a node -- there is
no such node, and none is invented, per the "do not guess" rule. Instead,
each node's `unresolved_calls` property records the raw, as-written call
expressions that couldn't be resolved, so that fact isn't silently lost --
just never turned into a fabricated edge.

MODIFIES relationships come from `repository.git_history.file_changes`, the
same way.
"""

from __future__ import annotations

from src.parser.models import ParsedFile, Relationship, RelationshipType
from src.parser.relationships import module_name_for_path
from src.repository import Repository

from . import identity
from .records import GraphBatch, NodeRecord, RelationshipRecord
from .schema import NodeLabel

__all__ = ["map_repository_to_graph"]


def map_repository_to_graph(repo: Repository) -> GraphBatch:
    """Build a :class:`GraphBatch` (nodes + relationships) for one `Repository`.

    Calling this twice on the same Phase 1 output produces an identical
    batch (same node ids, same properties, same relationships) -- that's
    what makes ingestion idempotent: it's Neo4j's `MERGE`, applied to
    always-the-same ids, that guarantees no duplicates, and this function is
    what guarantees the ids are always the same for the same source entity.
    """
    nodes: list[NodeRecord] = []
    relationships: list[RelationshipRecord] = []

    repo_id = identity.repository_id(remote_url=repo.info.remote_url, root_path=repo.info.root_path)
    nodes.append(
        NodeRecord(
            label=NodeLabel.REPOSITORY,
            id=repo_id,
            properties={
                "name": repo.info.name,
                "root_path": repo.info.root_path,
                "remote_url": repo.info.remote_url,
                "current_branch": repo.info.current_branch,
                "head_commit": repo.info.head_commit,
            },
        )
    )

    # Unresolved calls, collected up front so Method/Function nodes can carry
    # them as a property -- see the module docstring.
    unresolved_calls_by_source: dict[tuple[str, str], list[str]] = {}
    for rel in repo.relationships:
        if rel.relationship_type == RelationshipType.CALLS and not rel.resolved:
            key = (rel.source_file, rel.source)
            unresolved_calls_by_source.setdefault(key, []).append(rel.raw_call or rel.target)

    # --- Files, and everything structurally inside them -------------------

    file_id_by_relative_path: dict[str, str] = {}
    # (file_path, local_qualified_name) -> (node id, label), for CONTAINS/CALLS
    # targets that stay within one file (a class name, "Class.method", or a
    # top-level function name). The label is kept alongside the id because a
    # resolved call target can be either a Method or a Function (or, in
    # principle, a Class -- a constructor call).
    local_symbol_to_node_id: dict[tuple[str, str], tuple[str, NodeLabel]] = {}
    # (module_name, symbol_name) -> (node id, label), for cross-file
    # resolution -- mirrors exactly how src.parser.relationships resolves
    # imports, and covers both functions and classes (an imported class can
    # be called too, e.g. `PaymentService()`).
    module_symbol_to_node_id: dict[tuple[str, str], tuple[str, NodeLabel]] = {}

    for source_file in repo.source_files:
        this_file_id = identity.file_id(repo_id, source_file.relative_path)
        file_id_by_relative_path[source_file.relative_path] = this_file_id
        nodes.append(
            NodeRecord(
                label=NodeLabel.FILE,
                id=this_file_id,
                properties={
                    "path": source_file.relative_path,
                    "relative_path": source_file.relative_path,
                    "name": source_file.file_name,
                    "language": source_file.language,
                    "line_count": source_file.line_count,
                    "repository_id": repo_id,
                },
            )
        )
        relationships.append(
            RelationshipRecord(
                start_label=NodeLabel.REPOSITORY,
                start_id=repo_id,
                rel_type=RelationshipType.CONTAINS.value,
                end_label=NodeLabel.FILE,
                end_id=this_file_id,
            )
        )

    parsed_files_by_path: dict[str, ParsedFile] = {pf.file_path: pf for pf in repo.files if not pf.error}

    for parsed_file in parsed_files_by_path.values():
        this_file_id = file_id_by_relative_path.get(parsed_file.file_path)
        if this_file_id is None:
            # Defensive only -- every parsed file should already have a
            # matching SourceFileInfo from the same repository scan.
            continue

        module = module_name_for_path(parsed_file.file_path)

        for cls in parsed_file.classes:
            this_class_id = identity.class_id(this_file_id, cls.name)
            local_symbol_to_node_id[(parsed_file.file_path, cls.name)] = (this_class_id, NodeLabel.CLASS)
            module_symbol_to_node_id[(module, cls.name)] = (this_class_id, NodeLabel.CLASS)
            nodes.append(
                NodeRecord(
                    label=NodeLabel.CLASS,
                    id=this_class_id,
                    properties={
                        "name": cls.name,
                        "file_id": this_file_id,
                        "line_number": cls.line,
                        "bases": list(cls.bases),
                    },
                )
            )
            relationships.append(
                RelationshipRecord(
                    start_label=NodeLabel.FILE,
                    start_id=this_file_id,
                    rel_type=RelationshipType.CONTAINS.value,
                    end_label=NodeLabel.CLASS,
                    end_id=this_class_id,
                )
            )

            for method in cls.methods:
                this_method_id = identity.method_id(this_class_id, method.name)
                qualified = f"{cls.name}.{method.name}"
                local_symbol_to_node_id[(parsed_file.file_path, qualified)] = (this_method_id, NodeLabel.METHOD)
                nodes.append(
                    NodeRecord(
                        label=NodeLabel.METHOD,
                        id=this_method_id,
                        properties={
                            "name": method.name,
                            "class_id": this_class_id,
                            "file_id": this_file_id,
                            "line_number": method.line,
                            "args": list(method.args),
                            "is_async": method.is_async,
                            "unresolved_calls": unresolved_calls_by_source.get(
                                (parsed_file.file_path, qualified), []
                            ),
                        },
                    )
                )
                relationships.append(
                    RelationshipRecord(
                        start_label=NodeLabel.CLASS,
                        start_id=this_class_id,
                        rel_type=RelationshipType.CONTAINS.value,
                        end_label=NodeLabel.METHOD,
                        end_id=this_method_id,
                    )
                )

        for func in parsed_file.functions:
            this_function_id = identity.function_id(this_file_id, func.name, func.line)
            local_symbol_to_node_id[(parsed_file.file_path, func.name)] = (this_function_id, NodeLabel.FUNCTION)
            module_symbol_to_node_id[(module, func.name)] = (this_function_id, NodeLabel.FUNCTION)
            nodes.append(
                NodeRecord(
                    label=NodeLabel.FUNCTION,
                    id=this_function_id,
                    properties={
                        "name": func.name,
                        "file_id": this_file_id,
                        "line_number": func.line,
                        "args": list(func.args),
                        "is_async": func.is_async,
                        "unresolved_calls": unresolved_calls_by_source.get(
                            (parsed_file.file_path, func.name), []
                        ),
                    },
                )
            )
            relationships.append(
                RelationshipRecord(
                    start_label=NodeLabel.FILE,
                    start_id=this_file_id,
                    rel_type=RelationshipType.CONTAINS.value,
                    end_label=NodeLabel.FUNCTION,
                    end_id=this_function_id,
                )
            )

    # --- CALLS and IMPORTS, from Phase 1's already-resolved relationships -

    for rel in repo.relationships:
        if rel.relationship_type == RelationshipType.CALLS:
            _add_call_relationship(
                rel,
                relationships,
                local_symbol_to_node_id=local_symbol_to_node_id,
                module_symbol_to_node_id=module_symbol_to_node_id,
            )
        elif rel.relationship_type == RelationshipType.IMPORTS:
            _add_import_relationship(rel, relationships, file_id_by_relative_path=file_id_by_relative_path)
        # CONTAINS is already fully represented above, from the structured
        # objects directly -- skip it here to avoid duplicate edges.

    # --- Commits and Commit -> MODIFIES -> File ---------------------------

    commit_id_by_hash: dict[str, str] = {}
    for commit in repo.git_history.commits:
        this_commit_id = identity.commit_id(repo_id, commit.commit_hash)
        commit_id_by_hash[commit.commit_hash] = this_commit_id
        nodes.append(
            NodeRecord(
                label=NodeLabel.COMMIT,
                id=this_commit_id,
                properties={
                    "hash": commit.commit_hash,
                    "message": commit.message,
                    "author": commit.author_name,
                    "author_email": commit.author_email,
                    "timestamp": commit.committed_at.isoformat(),
                    "repository_id": repo_id,
                },
            )
        )

    for rel in repo.git_history.file_changes:
        this_commit_id = commit_id_by_hash.get(rel.source)
        this_file_id = file_id_by_relative_path.get(rel.target)
        if this_commit_id is None or this_file_id is None:
            continue
        relationships.append(
            RelationshipRecord(
                start_label=NodeLabel.COMMIT,
                start_id=this_commit_id,
                rel_type=RelationshipType.MODIFIES.value,
                end_label=NodeLabel.FILE,
                end_id=this_file_id,
            )
        )

    return GraphBatch(nodes=nodes, relationships=relationships)


def _add_call_relationship(
    rel: Relationship,
    relationships: list[RelationshipRecord],
    *,
    local_symbol_to_node_id: dict[tuple[str, str], tuple[str, NodeLabel]],
    module_symbol_to_node_id: dict[tuple[str, str], tuple[str, NodeLabel]],
) -> None:
    if not rel.resolved:
        # Phase 1 already marked this unresolved -- never invent a target
        # node for it. (Its raw text was already captured on the caller's
        # `unresolved_calls` property above.)
        return

    source_entry = local_symbol_to_node_id.get((rel.source_file, rel.source))
    if source_entry is None:
        return  # defensive: the caller should always have a node by this point
    source_node_id, source_label = source_entry

    target_entry = local_symbol_to_node_id.get((rel.source_file, rel.target))
    if target_entry is None and "." in rel.target:
        module, _, symbol = rel.target.rpartition(".")
        target_entry = module_symbol_to_node_id.get((module, symbol))

    if target_entry is None:
        # Phase 1 marked this resolved, but the mapper can't find a matching
        # node -- shouldn't normally happen; skip rather than guess.
        return
    target_node_id, target_label = target_entry

    relationships.append(
        RelationshipRecord(
            start_label=source_label,
            start_id=source_node_id,
            rel_type=RelationshipType.CALLS.value,
            end_label=target_label,
            end_id=target_node_id,
        )
    )


def _add_import_relationship(
    rel: Relationship,
    relationships: list[RelationshipRecord],
    *,
    file_id_by_relative_path: dict[str, str],
) -> None:
    if not rel.resolved:
        return
    source_node_id = file_id_by_relative_path.get(rel.source_file)
    target_node_id = file_id_by_relative_path.get(rel.target)
    if source_node_id is None or target_node_id is None:
        return
    relationships.append(
        RelationshipRecord(
            start_label=NodeLabel.FILE,
            start_id=source_node_id,
            rel_type=RelationshipType.IMPORTS.value,
            end_label=NodeLabel.FILE,
            end_id=target_node_id,
        )
    )
