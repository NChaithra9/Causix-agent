"""Phase 2: the RootFix Neo4j knowledge graph.

    Phase 1 output (src.repository.Repository)
            v
    Graph Mapper        (src.graph.mapper)       -- pure, no DB
            v
    Neo4j Ingestion      (src.graph.ingestion)    -- MERGE-based, idempotent
            v
    Knowledge Graph      (Neo4j)

Nothing in this package invents data: every node/relationship it writes
traces back to an actual Phase 1 fact. Node labels/relationship types that
don't have an ingestion source yet (Service, API, Event, Test, PR, Jira, ...)
are documented in `schema.py` but never created here.

Public API:
    Neo4jConfig.from_env() -> Neo4jConfig
    Neo4jConnection(config) -- connect() / verify_connectivity() / execute_write() / execute_read() / close()
    ensure_constraints(connection)
    map_repository_to_graph(repo) -> GraphBatch
    ingest_repository(connection, repo) -> IngestionSummary
"""

from .config import Neo4jConfig, Neo4jConfigError
from .connection import Neo4jConnection
from .constraints import CONSTRAINT_STATEMENTS, ensure_constraints
from .ingestion import IngestionSummary, ingest_batch, ingest_repository
from .mapper import map_repository_to_graph
from .records import GraphBatch, NodeRecord, RelationshipRecord
from .schema import NodeLabel

__all__ = [
    "CONSTRAINT_STATEMENTS",
    "GraphBatch",
    "IngestionSummary",
    "Neo4jConfig",
    "Neo4jConfigError",
    "Neo4jConnection",
    "NodeLabel",
    "NodeRecord",
    "RelationshipRecord",
    "ensure_constraints",
    "ingest_batch",
    "ingest_repository",
    "map_repository_to_graph",
]
