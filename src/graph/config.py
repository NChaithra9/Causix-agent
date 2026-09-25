"""Neo4j connection configuration -- Phase 2, requirement 2.

Configuration comes entirely from environment variables (never hardcoded
credentials), following the same convention the project's `.env.template`
already uses for its other settings. If `python-dotenv` happens to be
installed (it already is, transitively, via `aetherion-sdk`), a `.env` file
in the working directory is loaded automatically; if it isn't installed,
this still works fine reading plain `os.environ` -- no new configuration
system is introduced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:  # pragma: no cover - trivial, environment-dependent
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

__all__ = ["Neo4jConfig", "Neo4jConfigError"]

DEFAULT_DATABASE = "neo4j"
DEFAULT_CONNECTION_TIMEOUT = 5.0  # seconds -- fails fast rather than hanging


class Neo4jConfigError(RuntimeError):
    """Raised when required Neo4j configuration is missing or invalid."""


@dataclass(frozen=True)
class Neo4jConfig:
    """Everything needed to connect to a Neo4j instance.

    Read from environment variables:
        NEO4J_URI       (required)  e.g. "bolt://localhost:7687"
        NEO4J_USERNAME  (required)  e.g. "neo4j"
        NEO4J_PASSWORD  (required)
        NEO4J_DATABASE  (optional, default "neo4j")
    """

    uri: str
    username: str
    password: str
    database: str = DEFAULT_DATABASE
    connection_timeout: float = DEFAULT_CONNECTION_TIMEOUT

    @classmethod
    def from_env(cls) -> Neo4jConfig:
        """Build config from environment variables.

        Raises :class:`Neo4jConfigError` -- cleanly, with a clear message
        naming exactly what's missing -- rather than failing later with a
        confusing driver-level error.
        """
        uri = os.environ.get("NEO4J_URI", "").strip()
        username = os.environ.get("NEO4J_USERNAME", "").strip()
        password = os.environ.get("NEO4J_PASSWORD", "").strip()
        database = os.environ.get("NEO4J_DATABASE", DEFAULT_DATABASE).strip() or DEFAULT_DATABASE

        missing = [
            name
            for name, value in (("NEO4J_URI", uri), ("NEO4J_USERNAME", username), ("NEO4J_PASSWORD", password))
            if not value
        ]
        if missing:
            raise Neo4jConfigError(
                "Missing required Neo4j environment variable(s): "
                + ", ".join(missing)
                + ". Set them (see .env.template) before connecting to Neo4j."
            )

        return cls(uri=uri, username=username, password=password, database=database)
