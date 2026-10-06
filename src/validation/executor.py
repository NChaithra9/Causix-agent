"""Deterministic command execution: run, bound, record, classify.

The status of a command is decided from its exit code and whether it timed
out -- nothing else. Test-output parsing (counts, failed test names) is
recorded as evidence only and never changes a status.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from typing import IO, Protocol

from .logs import DEFAULT_LOG_LIMIT, BoundedBuffer
from .models import CommandRun, ExecutionStatus, TestExecutionResult

__all__ = [
    "BuildExecutor",
    "CommandRunner",
    "TestExecutor",
    "classify_exit",
    "parse_test_output",
    "run_process",
    "spawn_process",
]

# Shell conventions: 126 = found but not executable, 127 = command not found. Those mean
# the command could not even run, which is an error rather than a failing test.
_COULD_NOT_RUN = (126, 127)


def classify_exit(exit_code: int | None, timed_out: bool) -> ExecutionStatus:
    if timed_out:
        return ExecutionStatus.TIMEOUT
    if exit_code is None:
        return ExecutionStatus.ERROR
    if exit_code == 0:
        return ExecutionStatus.PASSED
    if exit_code in _COULD_NOT_RUN:
        return ExecutionStatus.ERROR
    return ExecutionStatus.FAILED


def _display(command: str | Sequence[str]) -> str:
    return command if isinstance(command, str) else shlex.join(command)


def _pump(stream: IO[bytes], sink: BoundedBuffer) -> threading.Thread:
    def run() -> None:
        try:
            # read1 returns as soon as *any* data is available; read(n) would block until n bytes
            # or EOF, so a live application's output would not show up until it exited.
            for chunk in iter(lambda: stream.read1(4096), b""):
                sink.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        proc.kill()


def spawn_process(
    command: str | Sequence[str],
    *,
    shell: bool,
    cwd: str | None,
    env: Mapping[str, str] | None,
    max_bytes: int,
) -> tuple[subprocess.Popen, BoundedBuffer, BoundedBuffer, list[threading.Thread]]:
    """Start a process in its own process group with bounded stdout/stderr capture."""
    proc = subprocess.Popen(
        command,
        shell=shell,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = BoundedBuffer(max_bytes), BoundedBuffer(max_bytes)
    threads = [_pump(proc.stdout, out), _pump(proc.stderr, err)]  # type: ignore[arg-type]
    return proc, out, err, threads


def run_process(
    command: str | Sequence[str],
    *,
    timeout: float,
    shell: bool = False,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_LOG_LIMIT,
) -> CommandRun:
    """Run a command to completion, or kill its whole process group on timeout."""
    display = _display(command)
    started = time.monotonic()
    try:
        proc, out, err, threads = spawn_process(
            command, shell=shell, cwd=cwd, env=env, max_bytes=max_bytes
        )
    except OSError as exc:
        return CommandRun(
            command=display,
            exit_code=None,
            status=ExecutionStatus.ERROR,
            duration=time.monotonic() - started,
            error=f"could not start command: {exc}",
        )

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    kill_process_group(proc)  # on timeout, and to reap stragglers after a normal exit
    proc.wait()
    for thread in threads:
        thread.join(timeout=5)

    exit_code = None if timed_out else proc.returncode
    return CommandRun(
        command=display,
        exit_code=exit_code,
        status=classify_exit(exit_code, timed_out),
        stdout=out.text(),
        stderr=err.text(),
        duration=time.monotonic() - started,
        timed_out=timed_out,
    )


class CommandRunner(Protocol):
    """Anything that can run a shell command somewhere (an Environment does)."""

    def run(
        self, command: str, *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CommandRun: ...


class BuildExecutor:
    """Runs the build/install command inside the environment."""

    def run(
        self,
        runner: CommandRunner,
        command: str,
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> CommandRun:
        return runner.run(command, timeout=timeout, env=env)


# ------------------------------------------------------------------ test output

_COUNT = re.compile(r"(\d+) (passed|failed|errors?)\b")
_PYTEST_LINE = re.compile(r"\bin \d+(?:\.\d+)?s\b")
_PYTEST_FAIL = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_UNITTEST_RESULT = re.compile(r"^FAILED \(([^)]*)\)", re.MULTILINE)
_UNITTEST_FAIL = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def parse_test_output(output: str) -> tuple[int | None, int | None, int | None, list[str]]:
    """Best-effort ``(passed, failed, errored, failed_test_names)`` from pytest/unittest output.

    Counts are ``None`` when no summary line is recognised -- unknown is never
    reported as zero. This is evidence only; it does not decide pass/fail.
    """
    # pytest: the last line that looks like "... 2 failed, 40 passed in 1.2s ..."
    for line in reversed(output.splitlines()):
        if _PYTEST_LINE.search(line) and _COUNT.search(line):
            counts: dict[str, int] = {}
            for number, word in _COUNT.findall(line):
                counts["error" if word.startswith("error") else word] = int(number)
            names = list(dict.fromkeys(m.split(" - ")[0] for m in _PYTEST_FAIL.findall(output)))
            return counts.get("passed", 0), counts.get("failed", 0), counts.get("error", 0), names

    # unittest: "Ran N tests in 0.01s" then "FAILED (failures=1, errors=2)" or "OK"
    ran = _UNITTEST_RAN.search(output)
    if ran:
        total = int(ran.group(1))
        failures = errors = 0
        result = _UNITTEST_RESULT.search(output)
        if result:
            for key, number in re.findall(r"(failures|errors)=(\d+)", result.group(1)):
                if key == "failures":
                    failures = int(number)
                else:
                    errors = int(number)
        names = list(dict.fromkeys(dotted for _, dotted in _UNITTEST_FAIL.findall(output)))
        return max(total - failures - errors, 0), failures, errors, names
    return None, None, None, []


class TestExecutor:
    """Runs a test command and records the facts. Status is the exit code's."""

    __test__ = False  # not a pytest test class

    def run(
        self,
        runner: CommandRunner,
        command: str,
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> TestExecutionResult:
        run = runner.run(command, timeout=timeout, env=env)
        passed, failed, errored, names = parse_test_output(run.stdout + "\n" + run.stderr)
        return TestExecutionResult(
            command=run.command,
            exit_code=run.exit_code,
            status=run.status,
            stdout=run.stdout,
            stderr=run.stderr,
            duration=run.duration,
            timed_out=run.timed_out,
            error=run.error,
            tests_passed=passed,
            tests_failed=failed,
            tests_errored=errored,
            failed_tests=names,
        )
