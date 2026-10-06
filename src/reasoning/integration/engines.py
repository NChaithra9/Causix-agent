"""Chooses real engines (Neo4j + Docker) or the Phase 1-7 stubs.

CAUSIX_ENGINES: "stub" (always stubs), "real" (fail loudly if Neo4j is unreachable) or
"auto" (default: real when NEO4J_PASSWORD is set and Neo4j answers, otherwise stubs).
"""
import logging
import os
from dataclasses import dataclass, field

from src.reasoning.facts import FactsProvider, StubFactsProvider
from src.reasoning.integration.architecture import GitArchitectureProvider
from src.reasoning.integration.locations import LocationIndex, NormalizedFacts
from src.reasoning.integration.validation_runner import ValidationScenarioRunner

log = logging.getLogger("causix.engines")


class RepoContext:
    """The repository most recently indexed (set by POST /index)."""

    def __init__(self) -> None:
        self.path: str | None = None

    def get(self) -> str | None:
        return self.path


class EngineConfigError(RuntimeError):
    pass


@dataclass
class Engines:
    mode: str                                    # "stub" | "real"
    facts: FactsProvider
    impact_provider: object | None               # None -> keep the interim CodeCallGraph
    scenario_runner: object
    architecture_provider: GitArchitectureProvider
    connection: object | None = None
    notes: list[str] = field(default_factory=list)

    def ingest(self, repo_path: str) -> bool:
        """Load a repository into the Neo4j graph (idempotent). False when stubs are active."""
        if self.connection is None:
            return False
        from src.graph import ingest_repository
        from src.repository import build_repository_facts
        ingest_repository(self.connection, build_repository_facts(repo_path))
        return True


def _connect():
    from src.graph.config import Neo4jConfig
    from src.graph.connection import Neo4jConnection
    conn = Neo4jConnection(Neo4jConfig.from_env())
    conn.connect()
    return conn


def build_engines(context: RepoContext, locations: LocationIndex, stub_runner,
                  mode: str | None = None, connect=_connect) -> Engines:
    mode = (mode or os.getenv("CAUSIX_ENGINES", "auto")).lower()
    arch = GitArchitectureProvider(context.get)
    stub = Engines("stub", StubFactsProvider(), None, stub_runner, arch)
    if mode == "stub":
        return stub
    if mode not in ("auto", "real"):
        raise EngineConfigError(f"CAUSIX_ENGINES must be stub, real or auto (got {mode!r})")
    if mode == "auto" and not os.getenv("NEO4J_PASSWORD"):
        stub.notes.append("NEO4J_PASSWORD not set: using stub facts and impact.")
        return stub
    try:
        conn = connect()
    except Exception as exc:
        if mode == "real":
            raise EngineConfigError(f"Neo4j is required (CAUSIX_ENGINES=real) but unreachable: {exc}") from exc
        log.warning("Neo4j unreachable, using stubs: %s", exc)
        stub.notes.append(f"Neo4j unreachable ({exc}): using stub facts and impact.")
        return stub

    from src.impact_analysis import GraphImpactProvider
    from src.reasoning.integration.engine_facts import EngineFacts
    facts = NormalizedFacts(EngineFacts(conn, context.get), locations)
    real_runner = ValidationScenarioRunner(context.get)
    runner = real_runner if os.getenv("CAUSIX_EXECUTION", "docker").lower() == "docker" else stub_runner
    return Engines("real", facts, GraphImpactProvider(conn), runner, arch, connection=conn)
