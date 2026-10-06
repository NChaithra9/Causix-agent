"""End-to-end Phase 6 scenarios with REAL processes and a REAL Git repo.

These use the explicit ``Backend.LOCAL`` (no isolation) so they run anywhere,
including CI without Docker; the Docker-specific behaviour is covered in
``test_validation_docker.py``. Everything the orchestrator reports here comes
from actual execution: real exit codes, real timeouts, a real HTTP server.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from git import Repo

from src.validation import (
    Backend,
    DependencyKind,
    DependencySpec,
    EnvironmentStatus,
    ExecutionStatus,
    ReadinessKind,
    ReadinessSpec,
    StartupStatus,
    Timeouts,
    ValidationConfig,
    ValidationOrchestrator,
    ValidationRequest,
    ValidationStatus,
)
from tests._validation_fixtures import (
    PY,
    PYTEST,
    commit_files,
    free_port,
    make_repo,
    port_open,
)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def validate(workspace, repo: Path, **fields):
    port = fields.pop("port", None)
    fields.setdefault("test_commands", (PYTEST,))
    if port is not None:
        fields.setdefault("start_command", f"{PY} app/main.py")
        fields.setdefault("readiness", ReadinessSpec(ReadinessKind.HTTP, port=port, path="/health"))
        fields.setdefault("environment_variables", {"APP_PORT": str(port)})
    orchestrator = ValidationOrchestrator(
        config=ValidationConfig(backend=Backend.LOCAL, workspace_root=workspace)
    )
    return orchestrator.validate(ValidationRequest(repository=str(repo), **fields))


def assert_clean(workspace, result):
    assert result.environment_status is EnvironmentStatus.DESTROYED
    assert list(workspace.iterdir()) == [], "the temporary checkout must be removed"


# ------------------------------------------------------------------- passing


def test_successful_build_startup_and_tests_is_passed(tmp_path, workspace):
    repo, commit = make_repo(tmp_path / "repo")
    port = free_port()
    result = validate(workspace, repo, port=port, build_command=f"{PY} -c \"print('built ok')\"")

    assert result.status is ValidationStatus.PASSED and result.passed
    assert result.exit_code == 0
    assert result.build_result.status is ExecutionStatus.PASSED
    assert result.startup_result.status is StartupStatus.READY
    assert result.startup_result.readiness_kind is ReadinessKind.HTTP
    assert result.test_result.status is ExecutionStatus.PASSED
    assert (result.test_result.tests_passed, result.test_result.tests_failed) == (2, 0)
    assert result.revision == commit and result.evidence.revision == commit
    assert result.evidence.application_started is True
    assert result.isolation == "none" and any("NOT ISOLATED" in w for w in result.warnings)
    # logs from every part of the run
    assert "built ok" in result.logs.build_logs
    assert "listening" in result.logs.application_logs
    assert "2 passed" in result.logs.test_logs
    # the application was really stopped and everything cleaned up
    assert not port_open(port)
    assert_clean(workspace, result)


def test_tests_only_run_needs_no_application(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    result = validate(workspace, repo)
    assert result.status is ValidationStatus.PASSED
    assert result.startup_result.status is StartupStatus.NOT_REQUIRED
    assert result.evidence.application_started is None


def test_stdout_and_stderr_are_captured_separately(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    cmd = f"{PY} -c \"import sys; print('to-out'); print('to-err', file=sys.stderr)\""
    result = validate(workspace, repo, test_commands=(cmd,))
    run = result.test_result
    assert "to-out" in run.stdout and "to-err" not in run.stdout
    assert "to-err" in run.stderr and "to-out" not in run.stderr
    assert run.duration > 0 and run.exit_code == 0 and run.command == cmd


# ------------------------------------------------------------------- failures


def test_failing_test_is_failed_with_the_failing_test_named(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo", logic="broken")
    port = free_port()
    result = validate(workspace, repo, port=port)

    assert result.status is ValidationStatus.FAILED
    assert result.exit_code == 1
    assert result.startup_result.ready  # "application started successfully, tests failed"
    run = result.test_result
    assert (run.tests_passed, run.tests_failed) == (1, 1)
    assert result.failed_tests == ["tests/test_logic.py::T::test_add"]
    assert result.evidence.failed_tests == result.failed_tests
    assert result.evidence.application_started is True
    assert "1 failed" in result.logs.test_logs
    assert not port_open(port)
    assert_clean(workspace, result)


def test_failed_build_is_build_failed_and_nothing_else_runs(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    port = free_port()
    result = validate(
        workspace,
        repo,
        port=port,
        build_command=f"{PY} -c \"import sys; print('boom', file=sys.stderr); sys.exit(3)\"",
    )
    assert result.status is ValidationStatus.BUILD_FAILED and result.exit_code == 3
    assert "boom" in result.logs.build_logs
    assert result.startup_result is None and result.test_results == []
    assert_clean(workspace, result)


def test_build_timeout_is_timeout_and_returns_promptly(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    started = time.monotonic()
    result = validate(
        workspace, repo, build_command="sleep 30", timeouts=Timeouts(build=1, overall=60)
    )
    assert result.status is ValidationStatus.TIMEOUT
    assert result.build_result.timed_out and result.build_result.status is ExecutionStatus.TIMEOUT
    assert time.monotonic() - started < 15
    assert_clean(workspace, result)


def test_application_that_crashes_on_boot_is_startup_failed(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo", main="crash")
    result = validate(workspace, repo, port=free_port())
    assert result.status is ValidationStatus.STARTUP_FAILED
    assert result.startup_result.status is StartupStatus.FAILED
    assert "cannot load config" in result.logs.application_logs
    assert result.test_results == []  # tests never run against a dead application
    assert result.evidence.application_started is False
    assert_clean(workspace, result)


def test_application_that_never_becomes_ready_is_a_startup_timeout(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo", main="hang")
    started = time.monotonic()
    result = validate(workspace, repo, port=free_port(), timeouts=Timeouts(startup=2, overall=60))
    assert result.status is ValidationStatus.TIMEOUT
    assert result.startup_result.status is StartupStatus.TIMEOUT
    assert "warming up" in result.logs.application_logs
    assert time.monotonic() - started < 15
    assert_clean(workspace, result)


def test_test_timeout_kills_the_command_and_is_timeout(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo", slow_test=True)
    started = time.monotonic()
    result = validate(workspace, repo, timeouts=Timeouts(test=2, overall=60))
    assert result.status is ValidationStatus.TIMEOUT
    assert result.test_result.timed_out and result.test_result.exit_code is None
    assert time.monotonic() - started < 20
    assert_clean(workspace, result)


def test_overall_timeout_bounds_the_whole_run(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    started = time.monotonic()
    result = validate(
        workspace, repo, build_command="sleep 30", timeouts=Timeouts(build=600, overall=2)
    )
    assert result.status is ValidationStatus.TIMEOUT
    assert time.monotonic() - started < 15


def test_test_command_that_cannot_run_is_error_not_failed(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    result = validate(workspace, repo, test_commands=("definitely-not-a-real-command-xyz",))
    assert result.status is ValidationStatus.ERROR
    assert (
        result.test_result.exit_code == 127 and result.test_result.status is ExecutionStatus.ERROR
    )
    assert_clean(workspace, result)


# ------------------------------------------------------------------ readiness


def test_log_and_tcp_readiness_against_a_real_application(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    port = free_port()
    for spec in (
        ReadinessSpec(ReadinessKind.LOG, log_pattern=r"listening on \d+"),
        ReadinessSpec(ReadinessKind.TCP, port=port),
    ):
        result = validate(workspace, repo, port=port, readiness=spec)
        assert result.status is ValidationStatus.PASSED, spec.kind
        assert result.startup_result.readiness_kind is spec.kind


def test_missing_readiness_config_falls_back_to_process_with_a_warning(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    port = free_port()
    result = validate(
        workspace,
        repo,
        test_commands=(PYTEST,),
        start_command=f"{PY} app/main.py",
        environment_variables={"APP_PORT": str(port)},
    )
    assert result.status is ValidationStatus.PASSED
    assert result.startup_result.readiness_kind is ReadinessKind.PROCESS
    assert any("readiness" in w for w in result.warnings)


# ---------------------------------------------------------- exact revisions


def test_validates_the_requested_commit_not_head_and_not_the_working_tree(tmp_path, workspace):
    repo, good = make_repo(tmp_path / "repo")
    bad = commit_files(repo, {"app/logic.py": "def add(a, b):\n    return a - b\n"}, "break add")
    Repo(str(repo)).git.branch("feature", bad)
    Repo(str(repo)).git.checkout(good)  # the developer is sitting on the good commit ...
    (repo / "app/logic.py").write_text("def add(a, b):\n    return 0\n")  # ... with a dirty tree

    at_good = validate(workspace, repo, revision=good)
    assert at_good.status is ValidationStatus.PASSED and at_good.revision == good

    at_bad = validate(workspace, repo, revision=bad)
    assert at_bad.status is ValidationStatus.FAILED and at_bad.revision == bad

    by_branch = validate(workspace, repo, revision="feature")
    assert by_branch.status is ValidationStatus.FAILED and by_branch.revision == bad

    at_head = validate(workspace, repo)  # HEAD == good; the dirty working tree is ignored
    assert at_head.status is ValidationStatus.PASSED and at_head.revision == good
    assert (repo / "app/logic.py").read_text() == "def add(a, b):\n    return 0\n"  # untouched
    assert list(workspace.iterdir()) == []


def test_unknown_revision_is_infrastructure_failed(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    result = validate(workspace, repo, revision="does-not-exist")
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert result.environment_status is EnvironmentStatus.NOT_CREATED
    assert "does-not-exist" in result.errors[0]
    assert list(workspace.iterdir()) == []


def test_unknown_repository_is_infrastructure_failed(tmp_path, workspace):
    result = validate(workspace, tmp_path / "nope")
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert list(workspace.iterdir()) == []


FIX_PATCH = """\
--- a/app/logic.py
+++ b/app/logic.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def test_a_proposed_patch_is_validated_on_top_of_the_revision(tmp_path, workspace):
    repo, commit = make_repo(tmp_path / "repo", logic="broken")
    assert validate(workspace, repo).status is ValidationStatus.FAILED
    fixed = validate(workspace, repo, patch=FIX_PATCH, changed_files=("app/logic.py",))
    assert fixed.status is ValidationStatus.PASSED and fixed.revision == commit
    assert fixed.evidence.changed_files == ["app/logic.py"]
    assert "return a - b" in (repo / "app/logic.py").read_text()  # source repo untouched


def test_a_patch_that_does_not_apply_is_infrastructure_failed(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    result = validate(workspace, repo, patch=FIX_PATCH)  # logic is already correct
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert "patch" in result.errors[0]
    assert list(workspace.iterdir()) == []


# -------------------------------------------------------------------- misc


def test_dependencies_require_the_docker_backend(tmp_path, workspace):
    repo, _ = make_repo(tmp_path / "repo")
    result = validate(
        workspace, repo, dependencies=(DependencySpec("cache", DependencyKind.REDIS),)
    )
    # rejected up front: no container is started and nothing is checked out or run
    assert result.status is ValidationStatus.INFRASTRUCTURE_FAILED
    assert "Docker backend" in result.errors[0]
    assert result.revision is None and result.test_results == []
    assert list(workspace.iterdir()) == []


def test_result_serialises_to_json(tmp_path, workspace):
    repo, commit = make_repo(tmp_path / "repo", logic="broken")
    data = json.loads(json.dumps(validate(workspace, repo).to_dict()))
    assert data["status"] == "FAILED" and data["exit_code"] == 1
    assert data["evidence"]["revision"] == commit
    assert data["evidence"]["failed_tests"] == ["tests/test_logic.py::T::test_add"]
    assert data["logs"]["test_logs"] and data["environment_status"] == "DESTROYED"
