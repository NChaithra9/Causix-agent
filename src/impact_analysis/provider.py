"""Adapter from this engine to Person 2's ``ImpactProvider`` contract.

Person 2's ``src.reasoning.impact`` module (``ImpactProvider`` /
``ImpactGraph`` / ``ImpactNode``) lives on their ``p2/phase-5-impact-agent``
branch and is not on ``main`` yet, so it is imported lazily inside
:meth:`GraphImpactProvider.impact_of` -- importing this package never fails
because of it. Nothing in ``src.reasoning`` is modified.

The contract is a flat list of ``ImpactNode(id, kind, name, location,
depth, relation)``. The rich result (paths, unresolved items, KNOWN/UNRESOLVED
status) is available from :func:`analyze_impact` directly; this adapter only
carries the part the contract can express.
"""

from __future__ import annotations

from src.graph.connection import Neo4jConnection

from .analyzer import DEFAULT_MAX_DEPTH, analyze_impact
from .models import ImpactAnalysisResult, ImpactCategory, ImpactItem

__all__ = ["GraphImpactProvider"]

_KIND = {
    ImpactCategory.DIRECT_CALLER: None,  # taken from the node label (method / function)
    ImpactCategory.INDIRECT_CALLER: None,
    ImpactCategory.CLASS: "class",
    ImpactCategory.FILE: "file",
    ImpactCategory.SERVICE: "service",
    ImpactCategory.API: "api",
    ImpactCategory.EVENT: "event",
    ImpactCategory.DATABASE: "database",
    ImpactCategory.TABLE: "table",
    ImpactCategory.DOWNSTREAM_SERVICE: "service",
    ImpactCategory.TEST: "test",
}
_TEST_RELATIONS = {"VALIDATES", "NAMING_CONVENTION"}


class GraphImpactProvider:
    """Implements ``ImpactProvider.impact_of(location)`` on top of :func:`analyze_impact`.

    ``location`` uses Person 2's ``"<file>::<Qualified.name>"`` format.
    """

    def __init__(self, connection: Neo4jConnection, repository: str | None = None, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        self._connection = connection
        self._repository = repository
        self._max_depth = max_depth

    def impact_of(self, location: str):
        from src.reasoning.impact import ImpactGraph, ImpactNode  # lazy: contract not on main yet

        file_path, separator, qualified = location.partition("::")
        if not separator:
            file_path, qualified = None, location
        result = analyze_impact(
            self._connection,
            repository=self._repository,
            file_path=file_path,
            method=qualified,
            max_depth=self._max_depth,
        )
        nodes = [ImpactNode(**fields) for fields in self.to_node_fields(result)]
        return ImpactGraph(changed=location, nodes=nodes)

    @staticmethod
    def to_node_fields(result: ImpactAnalysisResult) -> list[dict]:
        """The contract's node fields for every impacted element except the primary change."""
        groups = [
            result.callers, result.tests, result.classes, result.files, result.services, result.apis,
            result.events, result.databases, result.tables, result.downstream_services,
        ]
        fields: list[dict] = []
        for group in groups:
            for item in group:
                fields.append(
                    {
                        "id": item.id,
                        "kind": _KIND.get(item.category) or (item.node_label or "function").lower(),
                        "name": item.qualified_name or item.name,
                        "location": _location_of(item),
                        "depth": item.depth,
                        "relation": _relation_of(item),
                    }
                )
        return fields


def _location_of(item: ImpactItem) -> str | None:
    if item.category == ImpactCategory.FILE:
        return item.file
    if item.file and (item.qualified_name or item.class_name or item.name):
        return f"{item.file}::{item.qualified_name or item.class_name or item.name}"
    return None


def _relation_of(item: ImpactItem) -> str:
    if item.relationship in _TEST_RELATIONS:
        return "tests"
    return (item.relationship or "calls").lower()
