"""Shared pytest fixtures.

`neo4j_connection` backs the graph integration tests
(`test_graph_connection.py`, `test_graph_ingestion.py`). It reads
NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD the normal way
(`Neo4jConfig.from_env()`) and skips those tests cleanly -- rather than
failing -- when Neo4j isn't configured or isn't reachable, so the rest of
the suite (everything that doesn't need a real database) always runs.

To actually exercise these tests: `docker compose up -d`, then set
NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD (see `.env.template`) before running
pytest.

WARNING: this fixture wipes the *entire* configured database before and
after each test that uses it (`MATCH (n) DETACH DELETE n`), so it can start
from a known-empty state and stay isolated between tests. Point it at a
disposable local/dev Neo4j (e.g. the one `docker-compose.yml` starts) --
never a shared or production instance.
"""

from __future__ import annotations

import pytest

from src.graph import Neo4jConfig, Neo4jConfigError, Neo4jConnection


@pytest.fixture
def neo4j_connection():
    try:
        config = Neo4jConfig.from_env()
    except Neo4jConfigError:
        pytest.skip(
            "Neo4j is not configured (NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD) -- "
            "start it with `docker compose up -d` and set those environment "
            "variables to run graph integration tests."
        )

    connection = Neo4jConnection(config)
    if not connection.verify_connectivity():
        connection.close()
        pytest.skip(
            "Neo4j is configured but not reachable at the configured NEO4J_URI -- "
            "start it with `docker compose up -d`."
        )

    connection.execute_write("MATCH (n) DETACH DELETE n")
    yield connection
    connection.execute_write("MATCH (n) DETACH DELETE n")
    connection.close()
