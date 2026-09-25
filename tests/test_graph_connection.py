"""Tests for `src.graph.connection` -- the Neo4j connection layer.

`test_invalid_configuration_fails_cleanly` always runs (it deliberately
doesn't need a *working* Neo4j -- quite the opposite). The rest need a real,
reachable Neo4j and skip cleanly via the `neo4j_connection` fixture
(see conftest.py) when one isn't configured/running.
"""

from __future__ import annotations

from src.graph import Neo4jConfig, Neo4jConnection


def test_invalid_configuration_fails_cleanly():
    """A bad URI/unreachable host must make `verify_connectivity()` return False
    -- not raise, and not hang -- so callers can fail cleanly."""
    config = Neo4jConfig(
        uri="bolt://localhost:19999",  # nothing is listening here
        username="neo4j",
        password="wrong",
        connection_timeout=1.0,
    )
    connection = Neo4jConnection(config)

    assert connection.verify_connectivity() is False

    connection.close()  # must not raise even though connect() never truly succeeded


def test_connection_works(neo4j_connection):
    assert neo4j_connection.verify_connectivity() is True


def test_execute_write_and_read_round_trip(neo4j_connection):
    neo4j_connection.execute_write("CREATE (n:__ConnectionTest {id: $id})", {"id": "abc"})

    rows = neo4j_connection.execute_read(
        "MATCH (n:__ConnectionTest {id: $id}) RETURN n.id AS id", {"id": "abc"}
    )

    assert rows == [{"id": "abc"}]


def test_close_is_safe_to_call_more_than_once(neo4j_connection):
    neo4j_connection.close()
    neo4j_connection.close()  # must not raise
