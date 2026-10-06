"""Temporary backing services (PostgreSQL, Neo4j, Redis) via Testcontainers.

Started for one run on a private Docker network, reachable from the
validation container by their alias, and always destroyed afterwards. No
permanent infrastructure is needed. ``testcontainers`` is imported lazily so
the rest of the validation package works without it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import DependencyError
from .logs import DEFAULT_LOG_LIMIT, truncate_text
from .models import DependencyKind, DependencySpec

__all__ = [
    "DependencyProvider",
    "ResolvedDependency",
    "TestcontainersDependencyProvider",
    "resolve_placeholders",
]


@dataclass(frozen=True)
class _Preset:
    image: str
    port: int
    env: Mapping[str, str]
    health: Sequence[str]  # command run inside the container; exit 0 == ready
    url: str  # template using {host} and {port}
    user: str = ""
    password: str = ""


PRESETS: dict[DependencyKind, _Preset] = {
    DependencyKind.POSTGRES: _Preset(
        image="postgres:16-alpine",
        port=5432,
        env={"POSTGRES_USER": "test", "POSTGRES_PASSWORD": "test", "POSTGRES_DB": "test"},
        health=("pg_isready", "-U", "test", "-d", "test"),
        url="postgresql://test:test@{host}:{port}/test",
        user="test",
        password="test",
    ),
    DependencyKind.NEO4J: _Preset(
        image="neo4j:5-community",
        port=7687,
        env={"NEO4J_AUTH": "neo4j/rootfix-validation"},
        health=("wget", "-q", "-O", "/dev/null", "http://localhost:7474"),
        url="bolt://{host}:{port}",
        user="neo4j",
        password="rootfix-validation",
    ),
    DependencyKind.REDIS: _Preset(
        image="redis:7-alpine",
        port=6379,
        env={},
        health=("redis-cli", "ping"),
        url="redis://{host}:{port}",
    ),
}


@dataclass(frozen=True)
class ResolvedDependency:
    """How the validation container reaches a started dependency."""

    name: str
    host: str  # the network alias
    port: int
    url: str
    user: str = ""
    password: str = ""


_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9-]*)\.(host|port|url|user|password)\}")


def resolve_placeholders(
    values: Mapping[str, str], dependencies: Mapping[str, ResolvedDependency]
) -> dict[str, str]:
    """Substitute ``{name.attr}`` in environment-variable values.

    An unknown dependency name is an error, not silently left as text.
    """

    def sub(match: re.Match[str]) -> str:
        name, attr = match.groups()
        if name not in dependencies:
            raise DependencyError(f"environment variable refers to unknown dependency {name!r}")
        return str(getattr(dependencies[name], attr))

    return {key: _PLACEHOLDER.sub(sub, value) for key, value in values.items()}


class DependencyProvider(Protocol):
    network_name: str | None

    def start(self, specs: Sequence[DependencySpec]) -> dict[str, ResolvedDependency]: ...

    def logs(self) -> dict[str, str]: ...

    def stop(self) -> list[str]:
        """Destroy everything; returns descriptions of anything that failed to clean up."""
        ...


class TestcontainersDependencyProvider:
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        *,
        ready_timeout: float = 120.0,
        log_limit: int = DEFAULT_LOG_LIMIT,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.network_name: str | None = None
        self._ready_timeout = ready_timeout
        self._log_limit = log_limit
        self._clock = clock
        self._sleep = sleep
        self._network: Any = None
        self._containers: dict[str, Any] = {}

    def start(self, specs: Sequence[DependencySpec]) -> dict[str, ResolvedDependency]:
        try:
            from testcontainers.core.container import DockerContainer
            from testcontainers.core.network import Network
        except ImportError as exc:
            raise DependencyError(
                "testcontainers is not installed (pip install 'testcontainers>=4.8')"
            ) from exc

        resolved: dict[str, ResolvedDependency] = {}
        try:
            self._network = Network().create()
            self.network_name = self._network.name
            for spec in specs:
                preset = PRESETS[spec.kind]
                container = (
                    DockerContainer(spec.image or preset.image)
                    .with_envs(**preset.env)
                    .with_network(self._network)
                    .with_network_aliases(spec.name)
                )
                container.start()
                self._containers[spec.name] = container
                self._wait_ready(spec, preset, container)
                resolved[spec.name] = ResolvedDependency(
                    name=spec.name,
                    host=spec.name,
                    port=preset.port,
                    url=preset.url.format(host=spec.name, port=preset.port),
                    user=preset.user,
                    password=preset.password,
                )
        except DependencyError:
            raise
        except Exception as exc:  # noqa: BLE001 - docker/testcontainers raise many types
            raise DependencyError(
                f"could not start dependencies: {type(exc).__name__}: {exc}"
            ) from exc
        return resolved

    def _wait_ready(self, spec: DependencySpec, preset: _Preset, container: Any) -> None:
        wrapped = container.get_wrapped_container()
        deadline = self._clock() + self._ready_timeout
        while True:
            exit_code, _ = wrapped.exec_run(list(preset.health))
            if exit_code == 0:
                return
            if self._clock() >= deadline:
                raise DependencyError(
                    f"dependency {spec.name!r} ({spec.kind.value}) was not ready after "
                    f"{self._ready_timeout:.0f}s"
                )
            self._sleep(1.0)

    def logs(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, container in self._containers.items():
            try:
                raw = container.get_wrapped_container().logs(tail=200)
                out[name] = truncate_text(raw.decode("utf-8", errors="replace"), self._log_limit)
            except Exception as exc:  # noqa: BLE001 - logs are best-effort
                out[name] = f"[could not read logs: {exc}]"
        return out

    def stop(self) -> list[str]:
        problems: list[str] = []
        for name, container in reversed(list(self._containers.items())):
            try:
                container.stop()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"dependency {name!r} did not stop cleanly: {exc}")
        self._containers.clear()
        if self._network is not None:
            try:
                self._network.remove()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"network {self.network_name} was not removed: {exc}")
            self._network = None
        return problems
