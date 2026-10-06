"""Bounded log capture: validation output can never grow without limit."""

from __future__ import annotations

import threading

from .models import CommandRun, ValidationLogs

__all__ = ["DEFAULT_LOG_LIMIT", "BoundedBuffer", "LogCollector", "truncate_text"]

DEFAULT_LOG_LIMIT = 256 * 1024  # bytes kept per log section


def _marker(dropped: int) -> str:
    return f"\n[... {dropped} bytes truncated ...]\n"


def truncate_text(text: str, limit: int = DEFAULT_LOG_LIMIT) -> str:
    """Keep the start and the end of ``text`` (failures are usually at the end)."""
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    dropped = len(data) - head - tail
    return (
        data[:head].decode("utf-8", errors="replace")
        + _marker(dropped)
        + data[-tail:].decode("utf-8", errors="replace")
    )


class BoundedBuffer:
    """A thread-safe byte sink that keeps only the first and last parts.

    Memory use is capped at ``limit`` bytes no matter how much is written.
    """

    def __init__(self, limit: int = DEFAULT_LOG_LIMIT) -> None:
        self._head_limit = limit // 4
        self._tail_limit = limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self._dropped = 0
        self._lock = threading.Lock()

    def append(self, data: bytes) -> None:
        with self._lock:
            room = self._head_limit - len(self._head)
            if room > 0:
                self._head += data[:room]
                data = data[room:]
            if not data:
                return
            self._tail += data
            excess = len(self._tail) - self._tail_limit
            if excess > 0:
                del self._tail[:excess]
                self._dropped += excess

    def text(self) -> str:
        with self._lock:
            head = bytes(self._head).decode("utf-8", errors="replace")
            tail = bytes(self._tail).decode("utf-8", errors="replace")
            dropped = self._dropped
        return head + (_marker(dropped) if dropped else "") + tail


def render_run(run: CommandRun) -> str:
    """One command's output as a readable log block."""
    parts = [f"$ {run.command}"]
    if run.stdout:
        parts.append(run.stdout.rstrip("\n"))
    if run.stderr:
        parts.append("--- stderr ---\n" + run.stderr.rstrip("\n"))
    if run.error:
        parts.append(f"[error: {run.error}]")
    if run.timed_out:
        parts.append("[timed out]")
    elif run.exit_code is not None:
        parts.append(f"[exit code {run.exit_code}]")
    return "\n".join(parts)


class LogCollector:
    """Accumulates the per-section logs of one run and applies the size limit."""

    def __init__(self, limit: int = DEFAULT_LOG_LIMIT) -> None:
        self._limit = limit
        self._build: list[str] = []
        self._tests: list[str] = []
        self._infra: list[tuple[str, str]] = []
        self.application = ""
        self.startup = ""

    def add_build(self, run: CommandRun) -> None:
        self._build.append(render_run(run))

    def add_test(self, run: CommandRun) -> None:
        self._tests.append(render_run(run))

    def add_infrastructure(self, name: str, text: str) -> None:
        if text.strip():
            self._infra.append((name, text))

    def build(self) -> ValidationLogs:
        infra = "\n\n".join(f"=== {name} ===\n{text.rstrip()}" for name, text in self._infra)
        return ValidationLogs(
            application_logs=truncate_text(self.application, self._limit),
            startup_logs=truncate_text(self.startup, self._limit),
            test_logs=truncate_text("\n\n".join(self._tests), self._limit),
            build_logs=truncate_text("\n\n".join(self._build), self._limit),
            infrastructure_logs=truncate_text(infra, self._limit),
        )
