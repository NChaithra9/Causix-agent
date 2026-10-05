"""Architecture-level impact -- Phase 5, requirements 7-11 and 18.

Starting from every impacted piece of code (the changed method, its
callers, and the classes/files they live in -- the "anchors"), follows only
relationships that exist in the graph:

    Service  -[CONTAINS]->  code            (which service owns the code)
    owner    -[EXPOSES]->   API             (owner = code, class, file or Service)
    owner    -[PUBLISHES|CONSUMES]-> Event
    owner    -[ACCESSES]->  Database | Table
    Database -[CONTAINS]->  Table
    Service  -[DEPENDS_ON|CALLS]-> Service  (downstream services)

Every arrow above is followed in its stored direction; the only deliberate
reversal in the engine is the caller traversal in ``code_graph``. The
current Phase 2 mapper does not create Service/API/Event/Database/Table
nodes, so on such a graph every query here simply returns nothing -- and
``coverage`` reports those categories UNRESOLVED instead of "empty".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.graph.connection import Neo4jConnection

from .models import (
    Directness,
    GraphRelationship,
    ImpactCategory,
    ImpactItem,
    PathDirection,
    PathStep,
)
from .paths import PathCollector

__all__ = ["Anchor", "ArchitectureImpact", "trace_architecture"]

_OWNER_EDGE_TARGETS: dict[str, tuple[ImpactCategory, set[str]]] = {
    GraphRelationship.EXPOSES.value: (ImpactCategory.API, {"API"}),
    GraphRelationship.PUBLISHES.value: (ImpactCategory.EVENT, {"Event"}),
    GraphRelationship.CONSUMES.value: (ImpactCategory.EVENT, {"Event"}),
    GraphRelationship.ACCESSES.value: (ImpactCategory.DATABASE, {"Database", "Table"}),
}


@dataclass
class Anchor:
    """An impacted code element that architecture relationships can hang off."""

    id: str
    paths: list[list[PathStep]]  # every preserved route from the changed code to this anchor


@dataclass
class ArchitectureImpact:
    services: list[ImpactItem] = field(default_factory=list)
    apis: list[ImpactItem] = field(default_factory=list)
    events: list[ImpactItem] = field(default_factory=list)
    databases: list[ImpactItem] = field(default_factory=list)
    tables: list[ImpactItem] = field(default_factory=list)
    downstream_services: list[ImpactItem] = field(default_factory=list)


class _Items:
    """Items keyed by id; keeps the shallowest sighting."""

    def __init__(self) -> None:
        self.by_id: dict[str, ImpactItem] = {}

    def offer(self, item: ImpactItem) -> None:
        existing = self.by_id.get(item.id)
        if existing is None or item.depth < existing.depth:
            self.by_id[item.id] = item

    def sorted(self) -> list[ImpactItem]:
        return sorted(self.by_id.values(), key=lambda i: (i.depth, i.name, i.id))


def _step(node_id: str, name: str, category: ImpactCategory, label: str, rel: str, direction: PathDirection) -> PathStep:
    return PathStep(node_id=node_id, name=name, category=category, node_label=label, relationship=rel, direction=direction)


def trace_architecture(
    connection: Neo4jConnection,
    anchors: dict[str, Anchor],
    collector: PathCollector,
    *,
    max_downstream_depth: int,
) -> ArchitectureImpact:
    result = ArchitectureImpact()
    if not anchors:
        return result

    services = _Items()
    apis, events, databases, tables, downstream = _Items(), _Items(), _Items(), _Items(), _Items()
    owner_paths: dict[str, list[list[PathStep]]] = {a.id: a.paths for a in anchors.values()}
    service_paths: dict[str, list[list[PathStep]]] = {}

    # --- Services that own the impacted code (Service -CONTAINS-> ... -> code) ---
    rows = connection.execute_read(
        "MATCH (s:Service)-[:CONTAINS*1..4]->(x) WHERE x.id IN $ids "
        "RETURN DISTINCT s.id AS id, coalesce(s.name, s.id) AS name, x.id AS anchor_id",
        {"ids": list(anchors)},
    )
    for row in rows:
        for path in anchors[row["anchor_id"]].paths:
            steps = path + [
                _step(row["id"], row["name"], ImpactCategory.SERVICE, "Service",
                      GraphRelationship.CONTAINS.value, PathDirection.REVERSE)
            ]
            collector.add(row["id"], ImpactCategory.SERVICE, steps)
            service_paths.setdefault(row["id"], []).append(steps)
        services.offer(
            ImpactItem(
                id=row["id"], name=row["name"], category=ImpactCategory.SERVICE, node_label="Service",
                depth=min(len(p) for p in anchors[row["anchor_id"]].paths),
                relationship=GraphRelationship.CONTAINS.value,
            )
        )
    owner_paths.update(service_paths)

    # --- APIs, events, databases/tables (owner -[rel]-> target) ---
    rows = connection.execute_read(
        "MATCH (o)-[r:EXPOSES|PUBLISHES|CONSUMES|ACCESSES]->(t) "
        "WHERE o.id IN $ids AND (t:API OR t:Event OR t:Database OR t:Table) "
        "RETURN o.id AS owner_id, type(r) AS rel, t.id AS id, coalesce(t.name, t.id) AS name, labels(t)[0] AS label",
        {"ids": list(owner_paths)},
    )
    db_paths: dict[str, list[list[PathStep]]] = {}
    for row in rows:
        spec = _OWNER_EDGE_TARGETS.get(row["rel"])
        if spec is None or row["label"] not in spec[1]:
            continue  # relationship/label combination the schema does not define -> not claimed
        category = ImpactCategory.TABLE if row["label"] == "Table" else spec[0]
        bucket = {ImpactCategory.API: apis, ImpactCategory.EVENT: events,
                  ImpactCategory.DATABASE: databases, ImpactCategory.TABLE: tables}[category]
        depth = None
        for path in owner_paths[row["owner_id"]]:
            steps = path + [_step(row["id"], row["name"], category, row["label"], row["rel"], PathDirection.FORWARD)]
            collector.add(row["id"], category, steps)
            depth = len(steps) - 1 if depth is None else min(depth, len(steps) - 1)
            if category == ImpactCategory.DATABASE:
                db_paths.setdefault(row["id"], []).append(steps)
        if depth is not None:
            bucket.offer(
                ImpactItem(id=row["id"], name=row["name"], category=category, node_label=row["label"],
                           depth=depth, relationship=row["rel"], detail={"role": row["rel"]})
            )

    # --- Tables contained in an impacted database (Database -[CONTAINS]-> Table) ---
    if db_paths:
        rows = connection.execute_read(
            "MATCH (d:Database)-[:CONTAINS]->(t:Table) WHERE d.id IN $ids "
            "RETURN d.id AS db_id, t.id AS id, coalesce(t.name, t.id) AS name",
            {"ids": list(db_paths)},
        )
        for row in rows:
            depth = None
            for path in db_paths[row["db_id"]]:
                steps = path + [_step(row["id"], row["name"], ImpactCategory.TABLE, "Table",
                                      GraphRelationship.CONTAINS.value, PathDirection.FORWARD)]
                collector.add(row["id"], ImpactCategory.TABLE, steps)
                depth = len(steps) - 1 if depth is None else min(depth, len(steps) - 1)
            tables.offer(
                ImpactItem(id=row["id"], name=row["name"], category=ImpactCategory.TABLE, node_label="Table",
                           depth=depth or 0, relationship=GraphRelationship.CONTAINS.value)
            )

    # --- Downstream services (Service -[DEPENDS_ON|CALLS]-> Service), bounded and cycle-safe ---
    if service_paths and max_downstream_depth >= 1:
        visited = set(service_paths)  # services already impacted are not re-reported as downstream
        frontier = list(service_paths)
        paths_of: dict[str, list[list[PathStep]]] = dict(service_paths)
        for level in range(1, max_downstream_depth + 1):
            if not frontier:
                break
            rows = connection.execute_read(
                "MATCH (a:Service)-[r:DEPENDS_ON|CALLS]->(b:Service) WHERE a.id IN $frontier "
                "RETURN a.id AS source_id, b.id AS id, coalesce(b.name, b.id) AS name, type(r) AS rel",
                {"frontier": frontier},
            )
            next_frontier: list[str] = []
            new_paths: dict[str, list[list[PathStep]]] = {}
            for row in rows:
                if row["id"] in service_paths:
                    continue
                if row["id"] in visited and row["id"] not in new_paths:
                    continue  # reached at a shallower level already
                directness = Directness.DIRECT if level == 1 else Directness.INDIRECT
                depth = None
                for path in paths_of.get(row["source_id"], []):
                    steps = path + [_step(row["id"], row["name"], ImpactCategory.DOWNSTREAM_SERVICE, "Service",
                                          row["rel"], PathDirection.FORWARD)]
                    collector.add(row["id"], ImpactCategory.DOWNSTREAM_SERVICE, steps)
                    new_paths.setdefault(row["id"], []).append(steps)
                    depth = len(steps) - 1 if depth is None else min(depth, len(steps) - 1)
                downstream.offer(
                    ImpactItem(id=row["id"], name=row["name"], category=ImpactCategory.DOWNSTREAM_SERVICE,
                               node_label="Service", depth=depth or level, relationship=row["rel"],
                               directness=directness)
                )
                if row["id"] not in visited:
                    visited.add(row["id"])
                    next_frontier.append(row["id"])
            paths_of.update(new_paths)
            frontier = next_frontier

    result.services = services.sorted()
    result.apis = apis.sorted()
    result.events = events.sorted()
    result.databases = databases.sorted()
    result.tables = tables.sorted()
    result.downstream_services = downstream.sorted()
    return result
