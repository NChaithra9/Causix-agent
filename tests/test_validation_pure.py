"""Phase 6 unit tests that need neither Docker nor real subprocess scenarios:
models, bounded logs, exit-code classification, output parsing, the
ResultEvaluator decision table, readiness polling (fake clock), the
orchestrator's cleanup guarantees (fake environment), Docker command
construction (fake CLI) and the Phase 4/5 bridge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.validation import (
    CommandRun,
    DependencyKind,
    DependencySpec,
    DockerEnvironment,
    EnvironmentStatus,
    ExecutionStatus,
    ReadinessKind,
    ReadinessSpec,
    ResultEvaluator,
    StartupResult,
    StartupStatus,
    TestExecutionResult,
    Timeouts,
    ValidationOrchestrator,
    ValidationRequest,
    ValidationStatus,
    build_validation_request,
)
from src.validation.dependencies import ResolvedDependency, resolve_placeholders
from src.validation.docker import DockerCli
from src.validation.errors import DependencyError, EnvironmentError_
from src.validation.evaluator import EvaluationInput
from src.validation.executor import classify_exit, parse_test_output
from src.validation.logs import BoundedBuffer, LogCollector, truncate_text
from src.validation.revision import PreparedRepository
from src.validation.runner import ApplicationRunner

# ------------------------------------------------------------------- helpers


def run_of(command="cmd", code=0, *, timed_out=False, error=None, out="", err="") -> CommandRun:
    return CommandRun(
        command=command,
        exit_code=None if timed_out else code,
        status=classify_exit(None if timed_out else code, timed_out),
        stdout=out,
        stderr=err,
        timed_out=timed_out,
        error=error,
    )


def test_run_of(code=0, *, timed_out=False, failed=()):
    base = run_of("pytest", code, timed_out=timed_out)
    return TestExecutionResult(
        **base.__dict__, tests_passed=1, tests_failed=len(failed), failed_tests=list(failed)
    )


test_run_of.__test__ = False  # a helper, not a test


def request(**kw) -> ValidationRequest:
    kw.setdefault("repository", "/repo")
    kw.setdefault("test_commands", ("pytest",))
    return ValidationRequest(**kw)


class FakeHandle:
    def __init__(self, alive_script=(True,), logs=""):
        self._alive = list(alive_script)
        self._logs = logs
        self.stopped = False

    def is_running(self):
        return self._alive.pop(0) if len(self._alive) > 1 else self._alive[0]

    def logs(self):
        return self._logs

    def stop(self):
        self.stopped = True


class FakeEnv:
    isolation = "none"

    def __init__(self, handle=None, runs=None, boom_on=None, destroy_error=None):
        self.handle = handle or FakeHandle()
        self.runs = runs or {}
        self.boom_on = boom_on
        self.destroy_error = destroy_error
        self.commands = []
        self.destroyed = False

    def run(self, command, *, timeout, env=None):
        self.commands.append((command, timeout, dict(env or {})))
        if self.boom_on and self.boom_on in command:
            raise RuntimeError("boom")
        return self.runs.get(command) or run_of(command)

    def start_background(self, command, *, env=None):
        self.commands.append(("BG:" + command, 0, dict(env or {})))
        return self.handle

    def app_address(self, port):
        return "127.0.0.1", port

    def infrastructure_logs(self):
        return "env-log"

    def destroy(self):
        self.destroyed = True
        if self.destroy_error:
            raise RuntimeError(self.destroy_error)


class FakePrepared(PreparedRepository):
    def __init__(self, tmp: Path):
        super().__init__(path=tmp, commit="abc123", _root=tmp)
        self.cleaned = False

    def cleanup(self):
        self.cleaned = True


class FakeDeps:
    def __init__(self, start_error=None):
        self.network_name = "net-1"
        self.start_error = start_error
        self.stopped = False

    def start(self, specs):
        if self.start_error:
            raise DependencyError(self.start_error)
        return {
            s.name: ResolvedDependency(s.name, s.name, 6379, f"redis://{s.name}:6379")
            for s in specs
        }

    def logs(self):
        return {"cache": "redis-log"}

    def stop(self):
        self.stopped = True
        return []


def orchestrate(tmp_path, env, req=None, deps=None, clock=None):
    prepared = FakePrepared(tmp_path)
    orch = ValidationOrchestrator(
        repository_preparer=lambda *a, **k: prepared,
        environment_factory=lambda r, p, n, t: env,
        dependency_factory=lambda: deps,
        application_runner=ApplicationRunner(sleep=lambda s: None),
        **({"clock": clock} if clock else {}),
    )
    return orch.validate(req or request()), prepared


# ------------------------------------------------------------------- models


def test_request_requires_a_test_command():
    with pytest.raises(ValueError):
        ValidationRequest(repository="/r", test_commands=())
    with pytest.raises(ValueError):
        ValidationRequest(repository="/r", test_commands=("  ",))


def test_request_rejects_option_like_revision_and_orphan_readiness():
    with pytest.raises(ValueError):
        request(revision="--upload-pack=evil")
    with pytest.raises(ValueError):
        request(readiness=ReadinessSpec(ReadinessKind.PROCESS))  # no start_command


def test_readiness_and_dependency_and_timeout_validation():
    with pytest.raises(ValueError):
        ReadinessSpec(ReadinessKind.HTTP)
    with pytest.raises(ValueError):
        ReadinessSpec(ReadinessKind.LOG)
    with pytest.raises(ValueError):
        DependencySpec("Bad Name", DependencyKind.REDIS)
    with pytest.raises(ValueError):
        Timeouts(build=0)
    with pytest.raises(ValueError):
        request(dependencies=(DependencySpec("a", DependencyKind.REDIS),) * 2)


# --------------------------------------------------------------------- logs


def test_truncate_keeps_head_and_tail_with_marker():
    text = "A" * 1000 + "MIDDLE" + "Z" * 1000
    out = truncate_text(text, 200)
    assert out.startswith("A") and out.endswith("Z") and "truncated" in out
    assert len(out) < 400
    assert truncate_text("short", 200) == "short"


def test_bounded_buffer_never_exceeds_its_limit():
    buf = BoundedBuffer(100)
    for _ in range(1000):
        buf.append(b"x" * 50)
    text = buf.text()
    assert "truncated" in text and len(text) < 300


def test_log_collector_sections():
    logs = LogCollector(10_000)
    logs.add_build(run_of("make", 0, out="built"))
    logs.add_test(run_of("pytest", 1, out="1 failed", err="trace"))
    logs.add_infrastructure("dependency:db", "ready")
    logs.add_infrastructure("empty", "   ")
    logs.application = "app-out"
    built = logs.build()
    assert "built" in built.build_logs and "[exit code 0]" in built.build_logs
    assert "--- stderr ---" in built.test_logs and "trace" in built.test_logs
    assert "dependency:db" in built.infrastructure_logs and "empty" not in built.infrastructure_logs
    assert built.application_logs == "app-out"


# ----------------------------------------------------- exit codes and parsing


@pytest.mark.parametrize(
    ("code", "timed_out", "expected"),
    [
        (0, False, ExecutionStatus.PASSED),
        (1, False, ExecutionStatus.FAILED),
        (5, False, ExecutionStatus.FAILED),
        (127, False, ExecutionStatus.ERROR),
        (126, False, ExecutionStatus.ERROR),
        (None, False, ExecutionStatus.ERROR),
        (0, True, ExecutionStatus.TIMEOUT),
        (None, True, ExecutionStatus.TIMEOUT),
    ],
)
def test_classify_exit(code, timed_out, expected):
    assert classify_exit(code, timed_out) is expected


def test_parse_pytest_output():
    out = (
        "FAILED tests/t.py::test_a - assert 1 == 2\nERROR tests/t.py::test_b\n"
        "== 1 failed, 4 passed, 1 error in 0.5s =="
    )
    assert parse_test_output(out) == (4, 1, 1, ["tests/t.py::test_a", "tests/t.py::test_b"])
    assert parse_test_output("..\n2 passed in 0.01s")[:3] == (2, 0, 0)


def test_parse_unittest_output():
    out = (
        "FAIL: test_add (tests.test_logic.T.test_add)\n"
        "Ran 3 tests in 0.001s\n\nFAILED (failures=1, errors=1)"
    )
    assert parse_test_output(out) == (1, 1, 1, ["tests.test_logic.T.test_add"])
    assert parse_test_output("Ran 2 tests in 0.1s\n\nOK")[:3] == (2, 0, 0)


def test_unrecognised_output_is_unknown_not_zero():
    assert parse_test_output("no summary here") == (None, None, None, [])


# ---------------------------------------------------------------- evaluator


def evaluate(**kw):
    return ResultEvaluator().evaluate(EvaluationInput(**kw))


def ready():
    return StartupResult(StartupStatus.READY)


def test_all_green_is_passed():
    result = evaluate(
        build_result=run_of("b"), startup_result=ready(), test_results=[test_run_of(0)]
    )
    assert result.status is ValidationStatus.PASSED and result.exit_code == 0


def test_build_failure_is_build_failed_with_its_exit_code():
    result = evaluate(build_result=run_of("b", 3))
    assert result.status is ValidationStatus.BUILD_FAILED and result.exit_code == 3


def test_build_that_never_ran_is_infrastructure_not_build_failure():
    broken = CommandRun("b", None, ExecutionStatus.ERROR, error="no docker")
    assert evaluate(build_result=broken).status is ValidationStatus.INFRASTRUCTURE_FAILED


def test_build_timeout_is_timeout():
    assert evaluate(build_result=run_of("b", timed_out=True)).status is ValidationStatus.TIMEOUT


def test_startup_failure_after_good_build():
    result = evaluate(
        build_result=run_of("b"), startup_result=StartupResult(StartupStatus.FAILED, detail="died")
    )
    assert result.status is ValidationStatus.STARTUP_FAILED


def test_startup_timeout_is_timeout():
    result = evaluate(startup_result=StartupResult(StartupStatus.TIMEOUT, detail="slow"))
    assert result.status is ValidationStatus.TIMEOUT


def test_test_failure_after_good_startup_is_failed():
    result = evaluate(startup_result=ready(), test_results=[test_run_of(1, failed=["t::a"])])
    assert result.status is ValidationStatus.FAILED
    assert result.exit_code == 1 and result.failed_tests == ["t::a"]


def test_test_timeout_and_test_command_error():
    assert evaluate(test_results=[test_run_of(timed_out=True)]).status is ValidationStatus.TIMEOUT
    assert evaluate(test_results=[test_run_of(127)]).status is ValidationStatus.ERROR


def test_timeout_beats_failure_and_error_beats_failure():
    assert (
        evaluate(test_results=[test_run_of(1), test_run_of(timed_out=True)]).status
        is ValidationStatus.TIMEOUT
    )
    assert (
        evaluate(test_results=[test_run_of(1), test_run_of(127)]).status is ValidationStatus.ERROR
    )


def test_overall_timeout_and_setup_errors_and_unexpected():
    assert (
        evaluate(overall_timeout=True, build_result=run_of("b")).status is ValidationStatus.TIMEOUT
    )
    assert evaluate(infrastructure_error="x").status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert evaluate(unexpected_error="x", infrastructure_error="y").status is ValidationStatus.ERROR


def test_nothing_executed_is_never_passed():
    assert evaluate(test_results=[]).status is ValidationStatus.ERROR


def test_output_text_cannot_flip_the_status():
    """An all-green looking output with a non-zero exit is still FAILED; a scary-looking
    output with exit 0 is still PASSED -- only the exit code decides."""
    green_but_failing = test_run_of(1)
    green_but_failing.stdout = "100 passed in 1s"
    assert evaluate(test_results=[green_but_failing]).status is ValidationStatus.FAILED
    scary_but_passing = test_run_of(0)
    scary_but_passing.stdout = "FAILED everything"
    assert evaluate(test_results=[scary_but_passing]).status is ValidationStatus.PASSED


# --------------------------------------------------------------- readiness


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def runner_with(clock, http=None, tcp=None):
    return ApplicationRunner(
        http_probe=http or (lambda url: None),
        tcp_probe=tcp or (lambda h, p: False),
        clock=clock,
        sleep=clock.sleep,
    )


def test_http_readiness_waits_for_the_health_endpoint():
    clock, answers = Clock(), iter([None, 503, 200])
    runner = runner_with(clock, http=lambda url: next(answers))
    result, handle = runner.start(
        FakeEnv(), "serve", ReadinessSpec(ReadinessKind.HTTP, port=80, path="/health"), timeout=30
    )
    assert result.status is StartupStatus.READY and "200" in result.detail
    assert clock.now == pytest.approx(1.0)  # two polls of 0.5s, no fixed sleep


def test_http_expected_statuses_are_respected():
    clock = Clock()
    runner = runner_with(clock, http=lambda url: 401)
    spec = ReadinessSpec(ReadinessKind.HTTP, port=80, expected_statuses=(401,))
    assert runner.start(FakeEnv(), "serve", spec, timeout=5)[0].ready


def test_tcp_log_and_process_readiness():
    clock = Clock()
    assert (
        runner_with(clock, tcp=lambda h, p: True)
        .start(FakeEnv(), "s", ReadinessSpec(ReadinessKind.TCP, port=1), timeout=5)[0]
        .ready
    )
    env = FakeEnv(handle=FakeHandle(logs="booting\nServer started\n"))
    assert (
        runner_with(Clock())
        .start(
            env, "s", ReadinessSpec(ReadinessKind.LOG, log_pattern=r"Server started"), timeout=5
        )[0]
        .ready
    )
    result, _ = runner_with(Clock()).start(
        FakeEnv(), "s", ReadinessSpec(ReadinessKind.PROCESS, settle_seconds=2.0), timeout=10
    )
    assert result.ready and result.duration >= 2.0


def test_process_that_dies_fails_fast_without_waiting_for_the_timeout():
    clock = Clock()
    env = FakeEnv(handle=FakeHandle(alive_script=(True, False)))
    result, _ = runner_with(clock).start(
        env, "s", ReadinessSpec(ReadinessKind.HTTP, port=80), timeout=60
    )
    assert result.status is StartupStatus.FAILED and clock.now < 5


def test_never_ready_times_out():
    clock = Clock()
    result, _ = runner_with(clock).start(
        FakeEnv(), "s", ReadinessSpec(ReadinessKind.HTTP, port=80), timeout=3
    )
    assert result.status is StartupStatus.TIMEOUT and clock.now >= 3


# ------------------------------------------------------------- orchestrator


def test_orchestrator_passes_when_everything_passes(tmp_path):
    env = FakeEnv(runs={"pytest": run_of("pytest", out="2 passed in 0.1s")})
    result, prepared = orchestrate(
        tmp_path,
        env,
        request(
            build_command="build",
            start_command="serve",
            readiness=ReadinessSpec(ReadinessKind.PROCESS, settle_seconds=0),
        ),
    )
    assert result.status is ValidationStatus.PASSED
    assert [c[0] for c in env.commands] == ["build", "BG:serve", "pytest"]
    assert result.revision == "abc123" and result.evidence.revision == "abc123"
    assert result.evidence.tests_passed == 2 and result.evidence.application_started is True
    assert result.environment_status is EnvironmentStatus.DESTROYED
    assert env.destroyed and env.handle.stopped and prepared.cleaned


def test_failed_build_skips_start_and_tests_but_still_cleans_up(tmp_path):
    env = FakeEnv(runs={"build": run_of("build", 2)})
    result, prepared = orchestrate(
        tmp_path, env, request(build_command="build", start_command="serve")
    )
    assert result.status is ValidationStatus.BUILD_FAILED
    assert [c[0] for c in env.commands] == ["build"]
    assert env.destroyed and prepared.cleaned


def test_startup_failure_stops_before_tests_and_keeps_application_logs(tmp_path):
    env = FakeEnv(handle=FakeHandle(alive_script=(False,), logs="Traceback: bad config"))
    result, prepared = orchestrate(
        tmp_path,
        env,
        request(start_command="serve", readiness=ReadinessSpec(ReadinessKind.TCP, port=9)),
    )
    assert result.status is ValidationStatus.STARTUP_FAILED
    assert "bad config" in result.logs.application_logs and "bad config" in result.logs.startup_logs
    assert not any(c[0] == "pytest" for c in env.commands)
    assert env.destroyed and env.handle.stopped and prepared.cleaned


def test_test_timeout_still_cleans_up_and_skips_remaining_tests(tmp_path):
    env = FakeEnv(runs={"slow": run_of("slow", timed_out=True)})
    result, prepared = orchestrate(tmp_path, env, request(test_commands=("slow", "after")))
    assert result.status is ValidationStatus.TIMEOUT
    assert [c[0] for c in env.commands] == ["slow"]
    assert env.destroyed and prepared.cleaned


def test_unexpected_exception_becomes_error_and_still_cleans_up(tmp_path):
    env = FakeEnv(boom_on="pytest")
    result, prepared = orchestrate(tmp_path, env)
    assert result.status is ValidationStatus.ERROR
    assert "RuntimeError: boom" in result.errors[0]
    assert env.destroyed and prepared.cleaned
    assert result.environment_status is EnvironmentStatus.DESTROYED


def test_all_test_commands_run_even_if_an_earlier_one_fails(tmp_path):
    env = FakeEnv(runs={"first": run_of("first", 1), "second": run_of("second", 0)})
    result, _ = orchestrate(tmp_path, env, request(test_commands=("first", "second")))
    assert result.status is ValidationStatus.FAILED
    assert [c[0] for c in env.commands] == ["first", "second"]
    assert result.test_result.command == "first"  # the decisive run


def test_environment_destroy_failure_is_reported_not_hidden(tmp_path):
    env = FakeEnv(destroy_error="docker rm failed")
    result, prepared = orchestrate(tmp_path, env)
    assert result.status is ValidationStatus.PASSED  # the run itself passed ...
    assert (
        result.environment_status is EnvironmentStatus.DESTROY_FAILED
    )  # ... but the leak is visible
    assert any("docker rm failed" in w for w in result.warnings)
    assert prepared.cleaned


def test_environment_creation_failure_is_infrastructure_failed(tmp_path):
    prepared = FakePrepared(tmp_path)

    def factory(*args):
        raise EnvironmentError_("docker is not running")

    orch = ValidationOrchestrator(
        repository_preparer=lambda *a, **k: prepared, environment_factory=factory
    )
    result = orch.validate(request())
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert result.environment_status is EnvironmentStatus.CREATE_FAILED
    assert prepared.cleaned


def test_preparation_failure_is_infrastructure_failed():
    from src.validation.errors import PreparationError

    def prepare(*a, **k):
        raise PreparationError("revision 'nope' was not found")

    result = ValidationOrchestrator(repository_preparer=prepare).validate(request())
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert result.environment_status is EnvironmentStatus.NOT_CREATED
    assert "nope" in result.errors[0]


def test_dependencies_are_started_injected_logged_and_always_stopped(tmp_path):
    env, deps = FakeEnv(), FakeDeps()
    req = request(
        dependencies=(DependencySpec("cache", DependencyKind.REDIS),),
        environment_variables={"REDIS_URL": "{cache.url}", "HOST": "{cache.host}"},
    )
    result, _ = orchestrate(tmp_path, env, req, deps=deps)
    assert result.status is ValidationStatus.PASSED
    assert env.commands[0][2] == {"REDIS_URL": "redis://cache:6379", "HOST": "cache"}
    assert (
        "redis-log" in result.logs.infrastructure_logs
        and "env-log" in result.logs.infrastructure_logs
    )
    assert deps.stopped


def test_dependency_start_failure_is_infrastructure_failed_and_stops_deps(tmp_path):
    deps = FakeDeps(start_error="redis never became ready")
    req = request(dependencies=(DependencySpec("cache", DependencyKind.REDIS),))
    result, prepared = orchestrate(tmp_path, FakeEnv(), req, deps=deps)
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert deps.stopped and prepared.cleaned


def test_unknown_placeholder_is_an_error():
    with pytest.raises(DependencyError):
        resolve_placeholders({"X": "{nope.host}"}, {})


def test_overall_timeout_stops_the_run(tmp_path):
    clock = Clock()
    env = FakeEnv()
    real_run = env.run

    def slow_run(command, **kw):
        clock.now += 100  # each command "takes" 100s of the 50s budget
        return real_run(command, **kw)

    env.run = slow_run
    result, prepared = orchestrate(
        tmp_path,
        env,
        request(build_command="build", test_commands=("t1", "t2"), timeouts=Timeouts(overall=50)),
        clock=clock,
    )
    assert result.status is ValidationStatus.TIMEOUT
    assert [c[0] for c in env.commands] == ["build"]
    assert env.destroyed and prepared.cleaned


def test_step_timeout_is_capped_by_the_remaining_overall_budget(tmp_path):
    clock = Clock()
    env = FakeEnv()
    result, _ = orchestrate(
        tmp_path, env, request(timeouts=Timeouts(test=500, overall=30)), clock=clock
    )
    assert env.commands[0][1] <= 30


def test_missing_changed_file_is_a_warning(tmp_path):
    result, _ = orchestrate(tmp_path, FakeEnv(), request(changed_files=("nope.py",)))
    assert any("nope.py" in w for w in result.warnings)


def test_result_is_json_serialisable_and_has_a_summary(tmp_path):
    import json

    result, _ = orchestrate(tmp_path, FakeEnv())
    data = json.loads(json.dumps(result.to_dict()))
    assert data["status"] == "PASSED" and data["evidence"]["revision"] == "abc123"
    assert data["test_result"]["command"] == "pytest"
    assert "Validation: PASSED" in result.evidence.summary()


# ------------------------------------------------------------------- docker


class FakeDockerRunner:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, argv, *, timeout, **kw):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if self.fail_on and self.fail_on in joined:
            return run_of(joined, 1, err=f"{self.fail_on} exploded")
        if argv[1:3] == ["image", "inspect"]:
            return run_of(joined, 1)  # image not present -> triggers a pull
        if argv[1] == "port":
            return run_of(joined, out="127.0.0.1:49153\n")
        return run_of(joined)


def docker_env(tmp_path, runner, **req_kw):
    req = request(
        readiness=ReadinessSpec(ReadinessKind.HTTP, port=8000),
        start_command="serve",
        limits=req_kw.pop("limits", request().limits),
        **req_kw,
    )
    return DockerEnvironment.create(req, tmp_path, "net-1", 60, cli=DockerCli(runner=runner)), req


def test_docker_create_builds_a_bounded_isolated_container(tmp_path):
    runner = FakeDockerRunner()
    env, req = docker_env(tmp_path, runner)
    pull, run_cmd, cp = runner.calls[1], runner.calls[2], runner.calls[3]
    assert pull[1:] == ["pull", "python:3.12-slim"]
    flat = " ".join(run_cmd)
    for expected in (
        "run -d --rm",
        f"rootfix.validation={req.validation_id}",
        "--memory 2g",
        "--memory-swap 2g",
        "--cpus 2.0",
        "--pids-limit 1024",
        "no-new-privileges",
        "--network net-1",
        "-p 127.0.0.1::8000/tcp",
        "--entrypoint sleep",
    ):
        assert expected in flat, expected
    assert run_cmd[-1] == str(int(req.timeouts.overall + 120))  # bounded lifetime
    assert "-v" not in run_cmd and "--volume" not in run_cmd  # copied in, never bind-mounted
    assert cp[1] == "cp" and cp[2] == f"{tmp_path}/."


def test_docker_exec_passes_env_and_reports_the_users_command(tmp_path):
    runner = FakeDockerRunner()
    env, _ = docker_env(tmp_path, runner)
    run = env.run("pytest -q", timeout=5, env={"A": "1"})
    call = runner.calls[-1]
    assert call[:4] == ["docker", "exec", "-w", "/workspace"] and ["-e", "A=1"] == call[4:6]
    assert call[-3:] == ["sh", "-c", "pytest -q"] and run.command == "pytest -q"


def test_docker_background_and_port_lookup(tmp_path):
    runner = FakeDockerRunner()
    env, _ = docker_env(tmp_path, runner)
    handle = env.start_background("uvicorn app:app", env={"X": "y"})
    assert "-d" in runner.calls[-1] and runner.calls[-1][-1] == "uvicorn app:app"
    assert handle.is_running()
    assert env.app_address(8000) == ("127.0.0.1", 49153)
    with pytest.raises(EnvironmentError_):
        env.app_address(9999)  # not published


def test_docker_destroy_removes_container_and_is_idempotent(tmp_path):
    runner = FakeDockerRunner()
    env, _ = docker_env(tmp_path, runner)
    env.destroy()
    env.destroy()
    assert sum(1 for c in runner.calls if c[1] == "rm") == 1
    assert runner.calls[-1][:3] == ["docker", "rm", "-f"]


def test_docker_failed_copy_removes_the_container(tmp_path):
    runner = FakeDockerRunner(fail_on=" cp ")
    with pytest.raises(EnvironmentError_):
        docker_env(tmp_path, runner)
    assert any(c[1] == "rm" for c in runner.calls)  # no orphan left behind


def test_docker_pull_failure_is_an_environment_error(tmp_path):
    with pytest.raises(EnvironmentError_, match="pull"):
        docker_env(tmp_path, FakeDockerRunner(fail_on=" pull "))


def test_docker_rejects_a_dockerfile_outside_the_repository(tmp_path):
    req = request(dockerfile="../evil/Dockerfile")
    with pytest.raises(EnvironmentError_):
        DockerEnvironment.create(req, tmp_path, None, 10, cli=DockerCli(runner=FakeDockerRunner()))


# --------------------------------------------------------- Phase 4/5 bridge


def phase_objects(
    related=("tests/test_pay.py",), impact_tests=("tests/test_pay.py", "tests/test_x.py")
):
    location = SimpleNamespace(file_path="src/pay.py", qualified_name="PaymentService.charge")
    localization = SimpleNamespace(
        primary_location=SimpleNamespace(location=location),
        related_tests=[SimpleNamespace(test_file=f) for f in related],
    )
    impact = SimpleNamespace(tests=[SimpleNamespace(file=f) for f in impact_tests])
    return localization, impact


def test_request_is_built_from_phase_4_and_5_facts():
    localization, impact = phase_objects()
    req = build_validation_request(
        "/repo", revision="abc", localization=localization, impact=impact
    )
    assert req.target_location == "src/pay.py::PaymentService.charge"
    assert req.changed_files == ("src/pay.py",)
    assert req.test_commands == ("pytest tests/test_pay.py tests/test_x.py",)  # de-duplicated
    assert req.revision == "abc"


def test_explicit_test_commands_and_extra_fields_win():
    localization, impact = phase_objects()
    req = build_validation_request(
        "/repo",
        localization=localization,
        impact=impact,
        test_commands=["make test"],
        build_command="make",
        changed_files=["a.py"],
    )
    assert req.test_commands == ("make test",) and req.build_command == "make"
    assert req.changed_files == ("a.py",)


def test_no_linked_tests_and_no_command_refuses_to_guess():
    localization, impact = phase_objects(related=(), impact_tests=())
    with pytest.raises(ValueError, match="no test_commands"):
        build_validation_request("/repo", localization=localization, impact=impact)
