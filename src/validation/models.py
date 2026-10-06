"""Phase 6 data models: what to validate, and the evidence a run produces.

Everything here is plain data. Nothing in this module runs a command, talks
to Docker, or decides whether a run passed -- ``evaluator.py`` does the
latter from the recorded execution facts (exit codes, timeouts), never from
an LLM or from text interpretation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any

__all__ = [
    "CommandRun",
    "DependencyKind",
    "DependencySpec",
    "EnvironmentStatus",
    "ExecutionStatus",
    "ReadinessKind",
    "ReadinessSpec",
    "ResourceLimits",
    "StartupResult",
    "StartupStatus",
    "TestExecutionResult",
    "Timeouts",
    "ValidationEvidence",
    "ValidationLogs",
    "ValidationRequest",
    "ValidationResult",
    "ValidationStatus",
]


class ValidationStatus(str, Enum):
    """The overall outcome of one validation run."""

    PASSED = "PASSED"
    FAILED = "FAILED"  # the application started and at least one test failed
    TIMEOUT = "TIMEOUT"  # a step (or the whole run) exceeded its time budget
    BUILD_FAILED = "BUILD_FAILED"
    STARTUP_FAILED = "STARTUP_FAILED"
    INFRASTRUCTURE_FAILED = "INFRASTRUCTURE_FAILED"  # repo/env/dependencies could not be set up
    ERROR = "ERROR"  # validation could not be completed (unexpected error, test command error)


class ExecutionStatus(str, Enum):
    """The outcome of one executed command."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class EnvironmentStatus(str, Enum):
    NOT_CREATED = "NOT_CREATED"
    CREATED = "CREATED"
    CREATE_FAILED = "CREATE_FAILED"
    DESTROYED = "DESTROYED"
    DESTROY_FAILED = "DESTROY_FAILED"


class StartupStatus(str, Enum):
    READY = "READY"
    FAILED = "FAILED"  # the process exited before it became ready
    TIMEOUT = "TIMEOUT"  # still not ready when the startup timeout expired
    NOT_REQUIRED = "NOT_REQUIRED"  # the request has no start_command


class ReadinessKind(str, Enum):
    HTTP = "HTTP"  # GET a health endpoint
    TCP = "TCP"  # port accepts connections
    LOG = "LOG"  # a known line appears in the application's output
    PROCESS = "PROCESS"  # the process is still alive after a settle period (weakest)


class DependencyKind(str, Enum):
    POSTGRES = "POSTGRES"
    NEO4J = "NEO4J"
    REDIS = "REDIS"


_NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


@dataclass(frozen=True)
class ReadinessSpec:
    """How to tell that the started application is ready for tests."""

    kind: ReadinessKind
    port: int | None = None  # HTTP / TCP: the port the application listens on (in the environment)
    path: str = "/"  # HTTP: the health path
    expected_statuses: tuple[int, ...] = ()  # HTTP: empty means any 2xx/3xx
    log_pattern: str | None = None  # LOG: regular expression searched in the application's output
    poll_interval: float = 0.5
    settle_seconds: float = 1.0  # PROCESS: the process must stay alive this long

    def __post_init__(self) -> None:
        if self.kind in (ReadinessKind.HTTP, ReadinessKind.TCP) and self.port is None:
            raise ValueError(f"{self.kind.value} readiness needs a port")
        if self.kind is ReadinessKind.LOG:
            if not self.log_pattern:
                raise ValueError("LOG readiness needs a log_pattern")
            re.compile(self.log_pattern)  # fail early on an invalid pattern
        if self.poll_interval <= 0:
            raise ValueError("poll_interval must be positive")


@dataclass(frozen=True)
class DependencySpec:
    """A temporary backing service (started with Testcontainers) for one run.

    ``name`` is the network alias and the key used in environment-variable
    placeholders: ``{name.host}``, ``{name.port}``, ``{name.url}``,
    ``{name.user}``, ``{name.password}``.
    """

    name: str
    kind: DependencyKind
    image: str | None = None  # override the preset image

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(
                f"dependency name {self.name!r} must be lowercase letters, digits or '-'"
            )


@dataclass(frozen=True)
class Timeouts:
    """All limits in seconds. ``overall`` caps the whole run."""

    build: float = 600.0
    startup: float = 60.0
    test: float = 600.0
    overall: float = 1800.0

    def __post_init__(self) -> None:
        for name in ("build", "startup", "test", "overall"):
            if getattr(self, name) <= 0:
                raise ValueError(f"timeout {name!r} must be positive")


@dataclass(frozen=True)
class ResourceLimits:
    """Container resource limits (Docker backend). ``None`` means unlimited."""

    memory: str | None = "2g"
    cpus: float | None = 2.0
    pids: int | None = 1024


@dataclass(frozen=True)
class ValidationRequest:
    """Everything needed to reproduce one validation run.

    ``revision`` is a commit, branch or tag. The run always works on a fresh
    clone checked out at exactly that revision (HEAD of ``repository`` when
    omitted) -- never on the developer's working tree, so uncommitted edits
    are not validated. A proposed change can be supplied as ``patch`` (a
    unified diff applied on top of the revision). ``changed_files`` and
    ``target_location`` are recorded as evidence of what is being validated.
    """

    repository: str  # local path or clone URL
    test_commands: tuple[str, ...]
    revision: str | None = None
    changed_files: tuple[str, ...] = ()
    target_location: str | None = None
    patch: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    readiness: ReadinessSpec | None = None
    environment_variables: dict[str, str] = field(default_factory=dict)
    dependencies: tuple[DependencySpec, ...] = ()
    image: str = "python:3.12-slim"
    dockerfile: str | None = None  # path (relative to the repository) to build the image from
    timeouts: Timeouts = field(default_factory=Timeouts)
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    validation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        if not self.repository:
            raise ValueError("repository is required")
        if not self.test_commands or any(not c.strip() for c in self.test_commands):
            # Without a test command there is nothing to execute, and a run that executes
            # nothing must never be reported as PASSED.
            raise ValueError("at least one non-empty test command is required")
        if self.revision is not None and (not self.revision or self.revision.startswith("-")):
            raise ValueError(f"invalid revision {self.revision!r}")
        names = [d.name for d in self.dependencies]
        if len(set(names)) != len(names):
            raise ValueError("dependency names must be unique")
        if self.readiness is not None and not self.start_command:
            raise ValueError("readiness was given but there is no start_command")


# --------------------------------------------------------------------- results


@dataclass
class CommandRun:
    """The recorded facts of one executed command."""

    command: str
    exit_code: int | None  # None when the command never ran or was killed on timeout
    status: ExecutionStatus
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False
    error: str | None = None  # harness-level failure (could not start, ...)


@dataclass
class TestExecutionResult(CommandRun):
    """A test command run. Counts and names are parsed from the output purely
    as evidence; ``status`` comes from the exit code alone."""

    __test__ = False  # not a pytest test class

    tests_passed: int | None = None  # None: the output had no recognisable summary
    tests_failed: int | None = None
    tests_errored: int | None = None
    failed_tests: list[str] = field(default_factory=list)


@dataclass
class StartupResult:
    status: StartupStatus
    duration: float = 0.0
    readiness_kind: ReadinessKind | None = None
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.status is StartupStatus.READY


@dataclass
class ValidationLogs:
    """Bounded logs from every part of the run."""

    application_logs: str = ""
    startup_logs: str = ""
    test_logs: str = ""
    build_logs: str = ""
    infrastructure_logs: str = ""


@dataclass
class ValidationEvidence:
    """The machine-readable summary later components consume."""

    status: ValidationStatus
    repository: str
    revision: str | None  # the exact commit that was checked out
    changed_files: list[str]
    target_location: str | None
    tests_passed: int | None
    tests_failed: int | None
    failed_tests: list[str]
    application_started: bool | None  # None: no application was required
    duration: float
    isolation: str
    errors: list[str]

    def summary(self) -> str:
        """A short human-readable rendering of the same facts."""
        lines = [f"Validation: {self.status.value}"]
        if self.revision:
            lines.append(f"Commit: {self.revision}")
        if self.tests_passed is not None or self.tests_failed is not None:
            lines.append(f"Tests: {self.tests_passed or 0} passed, {self.tests_failed or 0} failed")
        if self.failed_tests:
            lines.append("Failed: " + ", ".join(self.failed_tests))
        if self.application_started is not None:
            lines.append(
                "Application: " + ("started" if self.application_started else "did not start")
            )
        lines.append(f"Duration: {self.duration:.1f}s")
        lines.append(f"Isolation: {self.isolation}")
        lines.extend(f"Error: {e}" for e in self.errors)
        return "\n".join(lines)


@dataclass
class ValidationResult:
    validation_id: str
    status: ValidationStatus
    environment_status: EnvironmentStatus
    build_result: CommandRun | None
    startup_result: StartupResult | None
    test_results: list[TestExecutionResult]
    exit_code: int | None  # exit code of the step that decided the result
    duration: float
    logs: ValidationLogs
    failed_tests: list[str]
    errors: list[str]
    warnings: list[str]
    evidence: ValidationEvidence
    revision: str | None = None
    isolation: str = "none"

    @property
    def test_result(self) -> TestExecutionResult | None:
        """The decisive test run: the first one that did not pass, else the last."""
        for run in self.test_results:
            if run.status is not ExecutionStatus.PASSED:
                return run
        return self.test_results[-1] if self.test_results else None

    @property
    def passed(self) -> bool:
        return self.status is ValidationStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        data = _plain(asdict(self))
        data["test_result"] = _plain(asdict(self.test_result)) if self.test_result else None
        return data


def _plain(value: Any) -> Any:
    """Convert enums/dataclasses to JSON-friendly builtins."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value
