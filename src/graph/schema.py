"""The RootFix graph schema -- Phase 2, requirement 3.

Defines every node label and relationship type the knowledge graph is
designed to hold. Only a subset is actually populated by the current
ingestion pipeline (see ``POPULATED_NODE_LABELS`` /
``POPULATED_RELATIONSHIP_TYPES`` below) -- the rest exist so the schema
doesn't need to change shape when later ingestion work (services, APIs,
tests, PRs, Jira, ...) adds real data for them. Nothing here fabricates
data; a label or relationship type only ever gets a node/edge in the graph
when ``src.graph.mapper`` finds an actual Phase 1 fact to back it.
"""

from __future__ import annotations

from enum import Enum

from src.parser.models import RelationshipType

__all__ = [
    "NodeLabel",
    "RelationshipType",
    "PLANNED_RELATIONSHIP_TYPES",
    "POPULATED_NODE_LABELS",
    "POPULATED_RELATIONSHIP_TYPES",
]


class NodeLabel(str, Enum):
    """Every node label the schema is designed for.

    Populated today, from Phase 1 facts:
        REPOSITORY, FILE, CLASS, METHOD, FUNCTION, COMMIT

    Planned for later ingestion work (no source exists yet -- deliberately
    not created by this phase's ingestion, per the instruction not to invent
    placeholder nodes just to make the graph look complete):
        SERVICE, API, EVENT, DATABASE, TABLE, TEST, JIRA, PR
    """

    REPOSITORY = "Repository"
    FILE = "File"
    CLASS = "Class"
    METHOD = "Method"
    FUNCTION = "Function"
    COMMIT = "Commit"

    # Planned -- see module docstring. Not populated by this phase.
    SERVICE = "Service"
    API = "API"
    EVENT = "Event"
    DATABASE = "Database"
    TABLE = "Table"
    TEST = "Test"
    JIRA = "Jira"
    PR = "PR"


# The relationship types Phase 1 actually produces facts for today, reusing
# `src.parser.models.RelationshipType` directly rather than a parallel
# enum -- CONTAINS, CALLS, IMPORTS, MODIFIES.
POPULATED_RELATIONSHIP_TYPES = frozenset(RelationshipType)

POPULATED_NODE_LABELS = frozenset(
    {
        NodeLabel.REPOSITORY,
        NodeLabel.FILE,
        NodeLabel.CLASS,
        NodeLabel.METHOD,
        NodeLabel.FUNCTION,
        NodeLabel.COMMIT,
    }
)

# Relationship types the broader system design calls for, with no ingestion
# source yet. Named here only so the schema documents where they'll attach
# once that ingestion work exists -- creating none of them is this phase's
# correct behavior, not a gap.
PLANNED_RELATIONSHIP_TYPES = frozenset(
    {
        "EXPOSES",  # Service -> API
        "PUBLISHES",  # Service -> Event
        "ACCESSES",  # Service -> Database
        "CONTAINS",  # PR -> Commit (same relationship type as the structural CONTAINS
        #                above -- Neo4j relationship types aren't scoped to a
        #                particular pair of node labels, so PR-CONTAINS->Commit
        #                queries consistently alongside Repository-CONTAINS->File)
        "VALIDATES",  # Test -> Method
        "RELATED_TO",  # Jira -> Commit
    }
)
