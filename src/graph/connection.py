"""A clean Neo4j connection layer -- Phase 2, requirement 2.

Wraps the official Neo4j Python driver with exactly the operations RootFix
needs: connect, verify connectivity, run a query, close. No new database
abstraction or ORM -- this is a thin, testable wrapper, nothing more.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase, RoutingControl
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from .config import Neo4jConfig

__all__ = ["Neo4jConnection"]


class Neo4jConnection:
    """A single Neo4j connection, configured via :class:`Neo4jConfig`.

    Usage::

        config = Neo4jConfig.from_env()
        with Neo4jConnection(config) as conn:
            conn.execute_write("MERGE (n:Thing {id: $id})", {"id": "1"})

    Or manage the lifecycle explicitly with ``connect()``/``close()``.
    """

    def __init__(self, config: Neo4jConfig):
        self._config = config
        self._driver: Driver | None = None

    def connect(self) -> None:
        """Create the underlying driver, if it doesn't already exist.

        This doesn't itself verify the server is reachable -- the Neo4j
        driver connects lazily. Call :meth:`verify_connectivity` for that.
        """
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
                connection_timeout=self._config.connection_timeout,
            )

    def verify_connectivity(self) -> bool:
        """Whether the configured Neo4j server is actually reachable and authenticates.

        Returns ``False`` (never raises) for any connectivity or auth
        failure -- a bad URI, wrong credentials, or no server running --
        so callers (and tests) can fail cleanly rather than crash.
        """
        self.connect()
        try:
            self._driver.verify_connectivity()
            return True
        except (ServiceUnavailable, Neo4jError, ValueError, OSError):
            return False

    def execute_write(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a write query and return its result records as plain dicts."""
        return self._execute(query, parameters, routing=RoutingControl.WRITE)

    def execute_read(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read query and return its result records as plain dicts."""
        return self._execute(query, parameters, routing=RoutingControl.READ)

    def _execute(
        self, query: str, parameters: dict[str, Any] | None, *, routing: RoutingControl
    ) -> list[dict[str, Any]]:
        self.connect()
        result = self._driver.execute_query(
            query, parameters or {}, routing_=routing, database_=self._config.database
        )
        return [record.data() for record in result.records]

    def close(self) -> None:
        """Release the underlying driver's resources. Safe to call more than once."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> Neo4jConnection:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
