"""ValidationOrchestrator: the deterministic validation workflow.

    prepare repository (exact revision) -> start dependencies -> create
    environment -> build -> start application + wait for readiness -> run
    tests -> collect logs -> evaluate -> clean up (always)

Every step is bounded by its own timeout and by what is left of the overall
budget. Cleanup runs in ``finally`` and therefore happens after build
failures, startup failures, test failures, timeouts and unexpected
exceptions alike. The final status comes from ``ResultEvaluator`` -- i.e.
from real exit codes -- and never from an LLM.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .dependencies import (
    DependencyProvider,
    ResolvedDependency,
    TestcontainersDependencyProvider,
    resolve_placeholders,
)
from .docker import DockerEnvironment
from .environment import BackgroundHandle, Environment, EnvironmentFactory, LocalProcessEnvironment
from .errors import EnvironmentError_, InfrastructureError
from .evaluator import EvaluationInput, ResultEvaluator, build_evidence
from .executor import BuildExecutor, TestExecutor
from .logs import DEFAULT_LOG_LIMIT, LogCollector
from .models import (
    EnvironmentStatus,
    ReadinessKind,
    ReadinessSpec,
    StartupResult,
    StartupStatus,
    ValidationRequest,
    ValidationResult,
)
from .revision import PreparedRepository, prepare_repository
from .runner import ApplicationRunner

__all__ = ["Backend", "ValidationConfig", "ValidationOrchestrator"]

logger = logging.getLogger(__name__)


class Backend(str, Enum):
    DOCKER = "docker"  # isolated (default)
    LOCAL = "local"  # host subprocesses, NOT isolated -- must be chosen explicitly


@dataclass(frozen=True)
class ValidationConfig:
    backend: Backend = Backend.DOCKER
    workspace_root: Path | None = None  # where temporary checkouts are created
    log_limit_bytes: int = DEFAULT_LOG_LIMIT

    def backend_isolation(self) -> str:
        return "docker" if self.backend is Backend.DOCKER else "none"


@dataclass
class _Run:
    """Mutable state of one run, handed to the evaluator at the end."""

    prepared: PreparedRepository | None = None
    env: Environment | None = None
    deps: DependencyProvider | None = None
    app: BackgroundHandle | None = None
    env_status: EnvironmentStatus = EnvironmentStatus.NOT_CREATED
    data: EvaluationInput = field(default_factory=EvaluationInput)
    warnings: list[str] = field(default_factory=list)


class ValidationOrchestrator:
    def __init__(
        self,
        *,
        config: ValidationConfig | None = None,
        repository_preparer: Callable[..., PreparedRepository] = prepare_repository,
        environment_factory: EnvironmentFactory | None = None,
        dependency_factory: Callable[[], DependencyProvider] | None = None,
        application_runner: ApplicationRunner | None = None,
        evaluator: ResultEvaluator | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or ValidationConfig()
        self._prepare = repository_preparer
        self._custom_environment = environment_factory is not None
        self._environment_factory = environment_factory or self._default_environment_factory
        self._dependency_factory = dependency_factory or (
            lambda: TestcontainersDependencyProvider(log_limit=self._config.log_limit_bytes)
        )
        self._app_runner = application_runner or ApplicationRunner()
        self._evaluator = evaluator or ResultEvaluator()
        self._clock = clock
        self._build_executor = BuildExecutor()
        self._test_executor = TestExecutor()

    # ------------------------------------------------------------------ public

    def validate(self, request: ValidationRequest) -> ValidationResult:
        started = self._clock()
        deadline = started + request.timeouts.overall
        logs = LogCollector(self._config.log_limit_bytes)
        run = _Run()
        try:
            self._execute(request, run, logs, deadline)
        except InfrastructureError as exc:
            run.data.infrastructure_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - the run must always produce a result
            logger.exception("unexpected error during validation %s", request.validation_id)
            run.data.unexpected_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._collect_and_cleanup(run, logs)

        duration = self._clock() - started
        evaluation = self._evaluator.evaluate(run.data)
        isolation = run.env.isolation if run.env else self._config.backend_isolation()
        commit = run.prepared.commit if run.prepared else None
        evidence = build_evidence(
            request, evaluation, run.data, commit=commit, duration=duration, isolation=isolation
        )
        return ValidationResult(
            validation_id=request.validation_id,
            status=evaluation.status,
            environment_status=run.env_status,
            build_result=run.data.build_result,
            startup_result=run.data.startup_result,
            test_results=run.data.test_results,
            exit_code=evaluation.exit_code,
            duration=duration,
            logs=logs.build(),
            failed_tests=evaluation.failed_tests,
            errors=evaluation.errors,
            warnings=run.warnings,
            evidence=evidence,
            revision=commit,
            isolation=isolation,
        )

    # ---------------------------------------------------------------- the steps

    def _remaining(self, deadline: float) -> float:
        return deadline - self._clock()

    def _execute(
        self, request: ValidationRequest, run: _Run, logs: LogCollector, deadline: float
    ) -> None:
        t = request.timeouts
        self._preflight(request)

        run.prepared = self._prepare(
            request.repository,
            request.revision,
            request.patch,
            workspace_root=self._config.workspace_root,
        )
        for changed in request.changed_files:
            if not (run.prepared.path / changed).exists():
                run.warnings.append(f"changed file {changed!r} does not exist at this revision")

        resolved: dict[str, ResolvedDependency] = {}
        network: str | None = None
        if request.dependencies:
            run.deps = self._dependency_factory()
            resolved = run.deps.start(request.dependencies)
            network = run.deps.network_name

        if self._remaining(deadline) <= 0:
            run.data.overall_timeout = True
            return
        try:
            run.env = self._environment_factory(
                request, run.prepared.path, network, min(t.build, self._remaining(deadline))
            )
        except InfrastructureError:
            run.env_status = EnvironmentStatus.CREATE_FAILED
            raise
        run.env_status = EnvironmentStatus.CREATED
        if run.env.isolation == "none":
            run.warnings.append("NOT ISOLATED: commands ran directly on the host")
        variables = resolve_placeholders(request.environment_variables, resolved)

        if request.build_command:
            if not self._budget_left(run, deadline):
                return
            build = self._build_executor.run(
                run.env,
                request.build_command,
                timeout=min(t.build, self._remaining(deadline)),
                env=variables,
            )
            run.data.build_result = build
            logs.add_build(build)
            if build.status.value != "PASSED":
                return

        if request.start_command:
            if not self._budget_left(run, deadline):
                return
            readiness = request.readiness
            if readiness is None:
                readiness = ReadinessSpec(ReadinessKind.PROCESS)
                run.warnings.append(
                    "no readiness check was configured; "
                    "readiness is only that the process stayed alive"
                )
            startup, run.app = self._app_runner.start(
                run.env,
                request.start_command,
                readiness,
                timeout=min(t.startup, self._remaining(deadline)),
                variables=variables,
            )
            run.data.startup_result = startup
            logs.startup = run.app.logs()
            if not startup.ready:
                return
        else:
            run.data.startup_result = StartupResult(StartupStatus.NOT_REQUIRED)

        for command in request.test_commands:
            if not self._budget_left(run, deadline):
                return
            result = self._test_executor.run(
                run.env,
                command,
                timeout=min(t.test, self._remaining(deadline)),
                env=variables,
            )
            run.data.test_results.append(result)
            logs.add_test(result)
            if result.timed_out:
                return

    def _preflight(self, request: ValidationRequest) -> None:
        """Reject impossible combinations before anything is started."""
        if self._custom_environment or self._config.backend is not Backend.LOCAL:
            return
        if request.dependencies:
            raise EnvironmentError_("dependencies need the Docker backend")
        if request.dockerfile:
            raise EnvironmentError_("a dockerfile needs the Docker backend")

    def _budget_left(self, run: _Run, deadline: float) -> bool:
        if self._remaining(deadline) <= 0:
            run.data.overall_timeout = True
            return False
        return True

    # -------------------------------------------------------- logs and cleanup

    def _collect_and_cleanup(self, run: _Run, logs: LogCollector) -> None:
        """Always runs. Failures here become warnings; they never mask the result."""
        if run.app is not None:
            self._safely(
                run, "application logs", lambda: setattr(logs, "application", run.app.logs())
            )
            self._safely(run, "stopping the application", run.app.stop)
        if run.env is not None:
            self._safely(
                run,
                "environment logs",
                lambda: logs.add_infrastructure("environment", run.env.infrastructure_logs()),
            )
        if run.deps is not None:
            self._safely(run, "dependency logs", lambda: self._add_dependency_logs(run, logs))

        if run.env is not None:
            try:
                run.env.destroy()
                run.env_status = EnvironmentStatus.DESTROYED
            except Exception as exc:  # noqa: BLE001
                run.env_status = EnvironmentStatus.DESTROY_FAILED
                run.warnings.append(f"environment cleanup failed: {exc}")
        if run.deps is not None:
            try:
                run.warnings.extend(run.deps.stop())
            except Exception as exc:  # noqa: BLE001
                run.warnings.append(f"dependency cleanup failed: {exc}")
        if run.prepared is not None:
            self._safely(run, "removing the checkout", run.prepared.cleanup)

    @staticmethod
    def _add_dependency_logs(run: _Run, logs: LogCollector) -> None:
        assert run.deps is not None
        for name, text in run.deps.logs().items():
            logs.add_infrastructure(f"dependency:{name}", text)

    @staticmethod
    def _safely(run: _Run, what: str, action: Callable[[], object]) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001
            run.warnings.append(f"{what} failed: {exc}")

    # ------------------------------------------------------ default environment

    def _default_environment_factory(
        self, request: ValidationRequest, source: Path, network: str | None, setup_timeout: float
    ) -> Environment:
        if self._config.backend is Backend.LOCAL:
            return LocalProcessEnvironment(source, max_bytes=self._config.log_limit_bytes)
        return DockerEnvironment.create(
            request, source, network, setup_timeout, log_limit=self._config.log_limit_bytes
        )
