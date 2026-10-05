"""Top-level Phase 5 orchestration: the deterministic Impact Analysis Engine.

    Changed method/function (from a Phase 4 localization, a Phase 3 code
    location, or repository / file / method names)
            v
    Reverse CALLS traversal  -> direct + indirect callers   (code_graph.py)
            v
    Containing classes, files, repository                    (code_graph.py)
            v
    Services -> APIs -> Events -> Databases -> Tables
    -> downstream services                                   (architecture.py)
            v
    Tests                                                    (tests_impact.py)
            v
    ImpactAnalysisResult: items + preserved paths + what is UNRESOLVED

``analyze_impact()`` is the single entry point. No LLM, no embeddings, no
name-based guessing: an element appears in the result only because a graph
relationship (or Phase 4's documented test-naming rule) put it there, and
the path that did so is kept.
"""

from __future__ import annotations

from src.fix_localization.models import FixLocalizationResult
from src.fix_localization.related_tests import is_test_path
from src.graph.connection import Neo4jConnection
from src.rca.models import CodeLocation, ResolutionStatus

from .architecture import Anchor, trace_architecture
from .code_graph import (
    CodeNode,
    build_caller_graph,
    fetch_code_nodes,
    find_unresolved_call_sites,
    has_more_callers,
)
from .coverage import assess_coverage
from .models import (
    GraphRelationship,
    ImpactAnalysisResult,
    ImpactCategory,
    ImpactItem,
    PathDirection,
    PathStep,
    UnresolvedItem,
)
from .paths import PathCollector, enumerate_chains
from .resolution import resolve_changed_node
from .tests_impact import trace_naming_convention_tests, trace_validating_tests

__all__ = ["analyze_impact", "DEFAULT_MAX_DEPTH", "DEFAULT_MAX_DOWNSTREAM_DEPTH", "DEFAULT_MAX_PATHS_PER_ITEM"]

DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_DOWNSTREAM_DEPTH = 3
DEFAULT_MAX_PATHS_PER_ITEM = 10


def analyze_impact(
    connection: Neo4jConnection,
    *,
    localization: FixLocalizationResult | None = None,
    location: CodeLocation | None = None,
    repository: str | None = None,
    file_path: str | None = None,
    method: str | None = None,
    class_name: str | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_downstream_depth: int = DEFAULT_MAX_DOWNSTREAM_DEPTH,
    max_paths_per_item: int = DEFAULT_MAX_PATHS_PER_ITEM,
) -> ImpactAnalysisResult:
    """Trace the technical blast radius of changing one method / function.

    Provide ``localization`` (Phase 4 result, used directly), or ``location``
    (a resolved Phase 3 code location), or ``method`` (optionally with
    ``repository`` / ``file_path`` / ``class_name``).

    ``max_depth`` bounds the caller traversal (0 = the changed code only);
    ``max_downstream_depth`` bounds service-to-service traversal;
    ``max_paths_per_item`` caps how many distinct paths are kept per element.
    """
    node_id, reason = resolve_changed_node(
        connection,
        localization=localization,
        location=location,
        repository=repository,
        file_path=file_path,
        method=method,
        class_name=class_name,
    )
    if node_id is None:
        return ImpactAnalysisResult(
            resolution_status=ResolutionStatus.UNRESOLVED,
            max_depth=max_depth,
            unresolved_items=[UnresolvedItem(ImpactCategory.PRIMARY_CHANGE, reason or "Changed code not resolved")],
        )

    graph = build_caller_graph(connection, node_id, max_depth)
    nodes = fetch_code_nodes(connection, list(graph.depth)) if graph else {}
    if graph is None or node_id not in nodes:
        return ImpactAnalysisResult(
            resolution_status=ResolutionStatus.UNRESOLVED,
            max_depth=max_depth,
            unresolved_items=[
                UnresolvedItem(ImpactCategory.PRIMARY_CHANGE, "The changed node is not a Method or Function in the graph")
            ],
        )

    collector = PathCollector(max_paths_per_item)
    unresolved: list[UnresolvedItem] = []
    primary = nodes[node_id]

    # --- classify every node reached by the reverse traversal ---------------
    category_of: dict[str, ImpactCategory] = {node_id: ImpactCategory.PRIMARY_CHANGE}
    for nid, depth in graph.depth.items():
        if nid == node_id or nid not in nodes:
            continue
        if nodes[nid].file_path and is_test_path(nodes[nid].file_path):
            category_of[nid] = ImpactCategory.TEST
        else:
            category_of[nid] = ImpactCategory.DIRECT_CALLER if depth == 1 else ImpactCategory.INDIRECT_CALLER

    # --- preserved paths to every code node (primary-first) ------------------
    code_paths: dict[str, list[list[PathStep]]] = {}
    paths_truncated = False
    for nid in graph.depth:
        if nid not in nodes:
            continue
        chains, truncated = enumerate_chains(nid, node_id, graph.predecessors, max_paths_per_item)
        paths_truncated = paths_truncated or truncated
        code_paths[nid] = [_chain_to_steps(chain, nodes, category_of) for chain in chains]

    # --- callers and test-callers -------------------------------------------
    callers: list[ImpactItem] = []
    tests: list[ImpactItem] = []
    for nid, node in nodes.items():
        category = category_of.get(nid)
        if nid == node_id or category is None:
            continue
        item = _code_item(node, category, graph.depth[nid], GraphRelationship.CALLS.value)
        for steps in code_paths.get(nid, []):
            collector.add(nid, category, steps)
        (tests if category == ImpactCategory.TEST else callers).append(item)

    # --- containing classes and files (of the changed code and non-test callers) ---
    class_paths: dict[str, list[list[PathStep]]] = {}
    file_paths: dict[str, list[list[PathStep]]] = {}
    class_nodes: dict[str, CodeNode] = {}
    class_depth: dict[str, int] = {}
    file_depth: dict[str, int] = {}
    file_names: dict[str, str] = {}
    for nid, node in nodes.items():
        if category_of.get(nid) in (None, ImpactCategory.TEST):
            continue
        for base in code_paths.get(nid, []):
            steps = list(base)
            if node.class_id and node.class_name:
                steps = steps + [
                    PathStep(node.class_id, node.class_name, ImpactCategory.CLASS, "Class",
                             GraphRelationship.CONTAINS.value, PathDirection.REVERSE)
                ]
                class_paths.setdefault(node.class_id, []).append(steps)
                class_nodes[node.class_id] = node
                class_depth[node.class_id] = min(class_depth.get(node.class_id, len(steps) - 1), len(steps) - 1)
            if node.file_id and node.file_path:
                file_steps = steps + [
                    PathStep(node.file_id, node.file_path, ImpactCategory.FILE, "File",
                             GraphRelationship.CONTAINS.value, PathDirection.REVERSE)
                ]
                file_paths.setdefault(node.file_id, []).append(file_steps)
                file_names[node.file_id] = node.file_path
                file_depth[node.file_id] = min(file_depth.get(node.file_id, len(file_steps) - 1), len(file_steps) - 1)

    classes: list[ImpactItem] = []
    for class_id, class_member in class_nodes.items():
        if class_id == primary.class_id:
            continue  # the changed code's own class is already part of primary_change
        classes.append(
            ImpactItem(id=class_id, name=class_member.class_name or class_id, category=ImpactCategory.CLASS,
                       node_label="Class", depth=class_depth[class_id], relationship=GraphRelationship.CONTAINS.value,
                       repository=class_member.repository_name, file=class_member.file_path,
                       class_name=class_member.class_name)
        )
        for steps in class_paths[class_id]:
            collector.add(class_id, ImpactCategory.CLASS, steps)
    files: list[ImpactItem] = []
    for file_id, path in file_names.items():
        if file_id == primary.file_id:
            continue
        repo_name = next((n.repository_name for n in nodes.values() if n.file_id == file_id), None)
        files.append(
            ImpactItem(id=file_id, name=path, category=ImpactCategory.FILE, node_label="File",
                       depth=file_depth[file_id], relationship=GraphRelationship.CONTAINS.value,
                       repository=repo_name, file=path)
        )
        for steps in file_paths[file_id]:
            collector.add(file_id, ImpactCategory.FILE, steps)

    # --- anchors for architecture / test lookups ------------------------------
    anchors: dict[str, Anchor] = {}
    for nid, paths in code_paths.items():
        if category_of.get(nid) in (None, ImpactCategory.TEST):
            continue
        anchors[nid] = Anchor(nid, paths)
    for class_id, paths in class_paths.items():
        anchors[class_id] = Anchor(class_id, paths)
    for file_id, paths in file_paths.items():
        anchors[file_id] = Anchor(file_id, paths)

    architecture = trace_architecture(connection, anchors, collector, max_downstream_depth=max_downstream_depth)

    # --- tests ---------------------------------------------------------------
    tests += trace_validating_tests(connection, {a.id: a.paths for a in anchors.values()}, collector)
    found_tests = {(t.file, t.name) for t in tests}
    tests += trace_naming_convention_tests(
        connection, primary, code_paths.get(node_id, []), collector, found_tests
    )

    # --- what the graph could not tell us -------------------------------------
    category_status, coverage_unresolved = assess_coverage(connection, primary.repository_id)
    unresolved += coverage_unresolved

    if has_more_callers(connection, graph):
        unresolved.append(
            UnresolvedItem(
                ImpactCategory.INDIRECT_CALLER,
                f"More callers exist beyond max_depth={graph.max_depth}; increase max_depth to traverse further",
            )
        )
    for caller_name, call_text in find_unresolved_call_sites(connection, primary.name, set(graph.depth)):
        unresolved.append(
            UnresolvedItem(
                ImpactCategory.DIRECT_CALLER,
                f"{caller_name} has an unresolved call '{call_text}' that ends in '{primary.name}'; "
                "no CALLS edge exists, so it is not counted as impacted",
            )
        )
    if paths_truncated or collector.capped_terminals:
        unresolved.append(
            UnresolvedItem(
                ImpactCategory.PRIMARY_CHANGE,
                f"Some elements have more than {max_paths_per_item} distinct paths; only the first "
                f"{max_paths_per_item} per element are kept (raise max_paths_per_item to keep more)",
            )
        )

    # --- assemble --------------------------------------------------------------
    callers.sort(key=lambda i: (i.depth, i.qualified_name or i.name, i.id))
    classes.sort(key=lambda i: (i.depth, i.name, i.id))
    files.sort(key=lambda i: (i.depth, i.name, i.id))
    tests.sort(key=lambda i: (i.depth, i.file or "", i.name, i.id))

    return ImpactAnalysisResult(
        resolution_status=ResolutionStatus.PARTIALLY_RESOLVED if unresolved else ResolutionStatus.RESOLVED,
        max_depth=graph.max_depth,
        primary_change=_code_item(primary, ImpactCategory.PRIMARY_CHANGE, 0, None),
        callers=callers,
        classes=classes,
        files=files,
        services=architecture.services,
        apis=architecture.apis,
        events=architecture.events,
        databases=architecture.databases,
        tables=architecture.tables,
        downstream_services=architecture.downstream_services,
        tests=tests,
        impact_paths=collector.paths,
        unresolved_items=unresolved,
        category_status=category_status,
    )


def _code_item(node: CodeNode, category: ImpactCategory, depth: int, relationship: str | None) -> ImpactItem:
    return ImpactItem(
        id=node.id,
        name=node.qualified_name,
        category=category,
        node_label=node.label,
        depth=depth,
        relationship=relationship,
        repository=node.repository_name,
        file=node.file_path,
        class_name=node.class_name,
        method=node.name,
        qualified_name=node.qualified_name,
    )


def _chain_to_steps(
    chain: list[str], nodes: dict[str, CodeNode], category_of: dict[str, ImpactCategory]
) -> list[PathStep]:
    steps: list[PathStep] = []
    for index, nid in enumerate(chain):
        node = nodes[nid]
        steps.append(
            PathStep(
                node_id=nid,
                name=node.qualified_name,
                category=category_of.get(nid, ImpactCategory.INDIRECT_CALLER),
                node_label=node.label,
                relationship=None if index == 0 else GraphRelationship.CALLS.value,
                direction=None if index == 0 else PathDirection.REVERSE,
            )
        )
    return steps
