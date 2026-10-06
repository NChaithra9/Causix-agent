"""ResultEvaluator: the deterministic PASS/FAIL decision.

A pure function of recorded execution facts -- exit codes, timeouts, whether
the application became ready, whether setup succeeded. It never looks at
free-text output and never involves an LLM.

Precedence (first match wins):
    unexpected exception                     -> ERROR
    repository/environment/dependency setup  -> INFRASTRUCTURE_FAILED
    any step timed out / overall budget hit  -> TIMEOUT
    build did not pass                       -> BUILD_FAILED
    application did not become ready         -> STARTUP_FAILED
    a test command could not run (126/127)   -> ERROR
    a test command exited non-zero           -> FAILED
    everything passed                        -> PASSED
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    CommandRun,
    ExecutionStatus,
    StartupResult,
    StartupStatus,
    TestExecutionResult,
    ValidationEvidence,
    ValidationRequest,
    ValidationStatus,
)

__all__ = ["Evaluation", "EvaluationInput", "ResultEvaluator", "build_evidence"]


@dataclass
class EvaluationInput:
    unexpected_error: str | None = None
    infrastructure_error: str | None = None
    overall_timeout: bool = False  # the overall budget ran out before the run finished
    build_result: CommandRun | None = None
    startup_result: StartupResult | None = None
    test_results: list[TestExecutionResult] = field(default_factory=list)
    tests_expected: bool = True


@dataclass
class Evaluation:
    status: ValidationStatus
    exit_code: int | None
    errors: list[str]
    failed_tests: list[str]


class ResultEvaluator:
    def evaluate(self, data: EvaluationInput) -> Evaluation:
        failed_tests = [name for run in data.test_results for name in run.failed_tests]

        def done(status: ValidationStatus, exit_code: int | None, *errors: str) -> Evaluation:
            return Evaluation(status, exit_code, [e for e in errors if e], failed_tests)

        if data.unexpected_error:
            return done(ValidationStatus.ERROR, None, data.unexpected_error)
        if data.infrastructure_error:
            return done(ValidationStatus.INFRASTRUCTURE_FAILED, None, data.infrastructure_error)

        build = data.build_result
        if build is not None:
            if build.timed_out:
                return done(ValidationStatus.TIMEOUT, None, f"build timed out: {build.command}")
            if build.exit_code is None:  # the command never ran: the harness, not the code
                return done(
                    ValidationStatus.INFRASTRUCTURE_FAILED,
                    None,
                    build.error or "build could not run",
                )
            if build.status is not ExecutionStatus.PASSED:
                return done(
                    ValidationStatus.BUILD_FAILED,
                    build.exit_code,
                    f"build failed with exit code {build.exit_code}: {build.command}",
                )

        startup = data.startup_result
        if startup is not None and startup.status is not StartupStatus.NOT_REQUIRED:
            if startup.status is StartupStatus.TIMEOUT:
                return done(ValidationStatus.TIMEOUT, None, f"startup timed out: {startup.detail}")
            if startup.status is StartupStatus.FAILED:
                return done(
                    ValidationStatus.STARTUP_FAILED, None, f"startup failed: {startup.detail}"
                )

        if data.overall_timeout:
            return done(
                ValidationStatus.TIMEOUT, None, "the overall validation timeout was reached"
            )

        runs = data.test_results
        for run in runs:
            if run.timed_out:
                return done(
                    ValidationStatus.TIMEOUT, None, f"test command timed out: {run.command}"
                )
        for run in runs:
            if run.status is ExecutionStatus.ERROR:
                return done(
                    ValidationStatus.ERROR,
                    run.exit_code,
                    f"test command could not run (exit {run.exit_code}): {run.command}"
                    + (f" -- {run.error}" if run.error else ""),
                )
        for run in runs:
            if run.status is ExecutionStatus.FAILED:
                return done(
                    ValidationStatus.FAILED,
                    run.exit_code,
                    f"test command failed with exit code {run.exit_code}: {run.command}",
                )
        if not runs and data.tests_expected:
            # Nothing was executed, so nothing can be claimed as passing.
            return done(ValidationStatus.ERROR, None, "no test command was executed")
        return done(ValidationStatus.PASSED, 0)


def build_evidence(
    request: ValidationRequest,
    evaluation: Evaluation,
    data: EvaluationInput,
    *,
    commit: str | None,
    duration: float,
    isolation: str,
) -> ValidationEvidence:
    runs = data.test_results
    counted = [r for r in runs if r.tests_passed is not None]
    startup = data.startup_result
    return ValidationEvidence(
        status=evaluation.status,
        repository=request.repository,
        revision=commit,
        changed_files=list(request.changed_files),
        target_location=request.target_location,
        tests_passed=sum(r.tests_passed or 0 for r in counted) if counted else None,
        tests_failed=(
            sum((r.tests_failed or 0) + (r.tests_errored or 0) for r in counted)
            if counted
            else None
        ),
        failed_tests=evaluation.failed_tests,
        application_started=(
            None
            if startup is None or startup.status is StartupStatus.NOT_REQUIRED
            else startup.ready
        ),
        duration=duration,
        isolation=isolation,
        errors=list(evaluation.errors),
    )
