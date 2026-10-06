"""The isolated-environment abstraction the orchestrator works against.

``Environment`` is the small interface every backend implements:
``DockerEnvironment`` (``docker.py``) is the isolated one;
``LocalProcessEnvironment`` below runs on the host with **no isolation** and
exists for the unit tests and for trusted/offline use -- it must be chosen
explicitly (``ValidationConfig(backend=Backend.LOCAL)``).
"""

from __future__ import annotations

import os
import signal
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from .executor import kill_process_group, run_process, spawn_process
from .logs import DEFAULT_LOG_LIMIT
from .models import CommandRun, ValidationRequest

__all__ = [
    "BackgroundHandle",
    "Environment",
    "EnvironmentFactory",
    "LocalProcessEnvironment",
]


class BackgroundHandle(Protocol):
    """A long-running process (the application) inside an environment."""

    def is_running(self) -> bool: ...

    def logs(self) -> str: ...

    def stop(self) -> None: ...


class Environment(Protocol):
    isolation: str  # "docker" | "none"

    def run(
        self, command: str, *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CommandRun: ...

    def start_background(
        self, command: str, *, env: Mapping[str, str] | None = None
    ) -> BackgroundHandle: ...

    def app_address(self, port: int) -> tuple[str, int]:
        """Where the host can reach ``port`` of the application."""
        ...

    def infrastructure_logs(self) -> str: ...

    def destroy(self) -> None: ...


# (request, source_dir, network_name, setup_timeout) -> Environment
EnvironmentFactory = Callable[[ValidationRequest, Path, "str | None", float], Environment]


class _LocalBackground:
    def __init__(self, command: str, cwd: Path, env: Mapping[str, str], max_bytes: int) -> None:
        self._proc, self._out, self._err, self._threads = spawn_process(
            command, shell=True, cwd=str(cwd), env=env, max_bytes=max_bytes
        )

    def is_running(self) -> bool:
        return self._proc.poll() is None

    def logs(self) -> str:
        return self._out.text() + self._err.text()

    def stop(self) -> None:
        if self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
                self._proc.wait(timeout=3)
            except Exception:  # noqa: BLE001 - escalate to SIGKILL on any failure
                kill_process_group(self._proc)
                self._proc.wait()
        else:
            kill_process_group(self._proc)  # reap children left behind


class LocalProcessEnvironment:
    """Runs commands as host subprocesses in the prepared source directory.

    NOT ISOLATED: commands have the developer's privileges and network.
    """

    isolation = "none"

    def __init__(self, workdir: Path, *, max_bytes: int = DEFAULT_LOG_LIMIT) -> None:
        self._workdir = workdir
        self._max_bytes = max_bytes
        self._background: list[_LocalBackground] = []

    def _env(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        return {**os.environ, **(extra or {})}

    def run(
        self, command: str, *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CommandRun:
        return run_process(
            command,
            timeout=timeout,
            shell=True,
            cwd=str(self._workdir),
            env=self._env(env),
            max_bytes=self._max_bytes,
        )

    def start_background(
        self, command: str, *, env: Mapping[str, str] | None = None
    ) -> BackgroundHandle:
        handle = _LocalBackground(command, self._workdir, self._env(env), self._max_bytes)
        self._background.append(handle)
        return handle

    def app_address(self, port: int) -> tuple[str, int]:
        return "127.0.0.1", port

    def infrastructure_logs(self) -> str:
        return ""

    def destroy(self) -> None:
        for handle in self._background:
            handle.stop()
        self._background.clear()
