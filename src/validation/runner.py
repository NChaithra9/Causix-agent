"""Application startup with deterministic readiness detection.

The application is started in the background and polled until a concrete
readiness condition holds (health endpoint, open port, known log line, or --
weakest -- the process staying alive). The poll loop is bounded by the
startup timeout and fails fast if the process dies. There are no blind sleeps
standing in for "probably ready".
"""

from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from .environment import BackgroundHandle, Environment
from .models import ReadinessKind, ReadinessSpec, StartupResult, StartupStatus

__all__ = ["ApplicationRunner", "probe_http", "probe_tcp"]


def probe_http(url: str, timeout: float = 2.0) -> int | None:
    """HTTP status of a GET, or ``None`` when nothing answered."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return None


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class ApplicationRunner:
    def __init__(
        self,
        *,
        http_probe: Callable[[str], int | None] = probe_http,
        tcp_probe: Callable[[str, int], bool] = probe_tcp,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_probe
        self._tcp = tcp_probe
        self._clock = clock
        self._sleep = sleep

    def start(
        self,
        env: Environment,
        command: str,
        readiness: ReadinessSpec,
        *,
        timeout: float,
        variables: Mapping[str, str] | None = None,
    ) -> tuple[StartupResult, BackgroundHandle]:
        """Start ``command`` and wait for readiness. The handle is always returned
        so the caller can collect logs and stop the process."""
        started = self._clock()
        handle = env.start_background(command, env=variables)
        deadline = started + timeout
        alive_since: float | None = None

        def result(status: StartupStatus, detail: str) -> StartupResult:
            return StartupResult(
                status=status,
                duration=self._clock() - started,
                readiness_kind=readiness.kind,
                detail=detail,
            )

        while True:
            if not handle.is_running():
                return (
                    result(StartupStatus.FAILED, "the application exited before it became ready"),
                    handle,
                )
            if alive_since is None:
                alive_since = self._clock()

            ready, detail = self._check(env, handle, readiness, alive_since)
            if ready:
                return result(StartupStatus.READY, detail), handle
            if self._clock() >= deadline:
                return (
                    result(StartupStatus.TIMEOUT, f"not ready after {timeout:.1f}s ({detail})"),
                    handle,
                )
            self._sleep(readiness.poll_interval)

    def _check(
        self, env: Environment, handle: BackgroundHandle, spec: ReadinessSpec, alive_since: float
    ) -> tuple[bool, str]:
        if spec.kind is ReadinessKind.HTTP:
            assert spec.port is not None
            host, port = env.app_address(spec.port)
            url = f"http://{host}:{port}{spec.path}"
            status = self._http(url)
            good = (
                status in spec.expected_statuses
                if spec.expected_statuses
                else status is not None and 200 <= status < 400
            )
            return good, f"GET {spec.path} -> {status if status is not None else 'no response'}"
        if spec.kind is ReadinessKind.TCP:
            assert spec.port is not None
            host, port = env.app_address(spec.port)
            ok = self._tcp(host, port)
            return ok, f"port {spec.port} {'open' if ok else 'closed'}"
        if spec.kind is ReadinessKind.LOG:
            assert spec.log_pattern is not None
            found = re.search(spec.log_pattern, handle.logs()) is not None
            return found, f"log pattern {spec.log_pattern!r} {'seen' if found else 'not seen'}"
        alive_for = self._clock() - alive_since
        return alive_for >= spec.settle_seconds, f"process alive for {alive_for:.1f}s"
