"""KNOWN vs UNRESOLVED -- Phase 5, requirement 21.

For each category that depends on graph data the current ingestion pipeline
may not have produced (services, APIs, events, databases, tables,
downstream services, tests), asks the graph one question: do relationships
of the needed kind exist *at all*? If not, an empty result for that
category means "cannot tell", and an :class:`UnresolvedItem` says so.
If they do exist, an empty result genuinely means "nothing is connected".
"""

from __future__ import annotations

from src.fix_localization.related_tests import is_test_path
from src.graph.connection import Neo4jConnection

from .models import ImpactCategory, KnowledgeStatus, UnresolvedItem

__all__ = ["assess_coverage"]

_REASONS: dict[ImpactCategory, str] = {
    ImpactCategory.SERVICE: "No Service -[CONTAINS]-> code relationships exist in the graph, so the owning service cannot be determined",
    ImpactCategory.API: "No -[EXPOSES]-> API relationships exist in the graph, so impacted APIs cannot be determined",
    ImpactCategory.EVENT: "No -[PUBLISHES|CONSUMES]-> Event relationships exist in the graph, so impacted events cannot be determined",
    ImpactCategory.DATABASE: "No -[ACCESSES]-> Database/Table relationships exist in the graph, so impacted databases cannot be determined",
    ImpactCategory.TABLE: "No Database -[CONTAINS]-> Table or -[ACCESSES]-> Table relationships exist in the graph, so impacted tables cannot be determined",
    ImpactCategory.DOWNSTREAM_SERVICE: "No Service -[DEPENDS_ON|CALLS]-> Service relationships exist in the graph, so downstream services cannot be determined",
    ImpactCategory.TEST: "No Test -[VALIDATES]-> relationships and no test files exist in the repository, so impacted tests cannot be determined",
}


def assess_coverage(
    connection: Neo4jConnection, repository_id: str | None
) -> tuple[dict[ImpactCategory, KnowledgeStatus], list[UnresolvedItem]]:
    rows = connection.execute_read(
        "RETURN "
        "EXISTS { MATCH (:Service)-[:CONTAINS]->() } AS service_containment, "
        "EXISTS { MATCH ()-[:EXPOSES]->(:API) } AS exposes, "
        "EXISTS { MATCH ()-[:PUBLISHES|CONSUMES]->(:Event) } AS events, "
        "EXISTS { MATCH ()-[:ACCESSES]->(:Database) } AS accesses_database, "
        "EXISTS { MATCH ()-[:ACCESSES]->(:Table) } AS accesses_table, "
        "EXISTS { MATCH (:Database)-[:CONTAINS]->(:Table) } AS database_tables, "
        "EXISTS { MATCH (:Service)-[:DEPENDS_ON|CALLS]->(:Service) } AS service_links, "
        "EXISTS { MATCH (:Test)-[:VALIDATES]->() } AS test_links"
    )
    flags = rows[0] if rows else {}

    has_test_files = False
    if repository_id:
        files = connection.execute_read(
            "MATCH (:Repository {id: $repo_id})-[:CONTAINS]->(f:File) RETURN f.path AS path",
            {"repo_id": repository_id},
        )
        has_test_files = any(is_test_path(row["path"]) for row in files if row["path"])

    known = {
        ImpactCategory.SERVICE: flags.get("service_containment", False),
        ImpactCategory.API: flags.get("exposes", False),
        ImpactCategory.EVENT: flags.get("events", False),
        ImpactCategory.DATABASE: flags.get("accesses_database", False) or flags.get("accesses_table", False),
        ImpactCategory.TABLE: flags.get("database_tables", False) or flags.get("accesses_table", False),
        ImpactCategory.DOWNSTREAM_SERVICE: flags.get("service_links", False),
        ImpactCategory.TEST: flags.get("test_links", False) or has_test_files,
    }

    status = {
        ImpactCategory.PRIMARY_CHANGE: KnowledgeStatus.KNOWN,
        ImpactCategory.DIRECT_CALLER: KnowledgeStatus.KNOWN,
        ImpactCategory.INDIRECT_CALLER: KnowledgeStatus.KNOWN,
        ImpactCategory.CLASS: KnowledgeStatus.KNOWN,
        ImpactCategory.FILE: KnowledgeStatus.KNOWN,
    }
    unresolved: list[UnresolvedItem] = []
    for category, is_known in known.items():
        status[category] = KnowledgeStatus.KNOWN if is_known else KnowledgeStatus.UNRESOLVED
        if not is_known:
            unresolved.append(UnresolvedItem(category=category, reason=_REASONS[category]))
    return status, unresolved
