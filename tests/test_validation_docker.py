"""Phase 6 against a REAL Docker daemon (skipped automatically without one).

Proves the isolated path end to end: the container is created, the source is
copied in, build/start/test run via ``docker exec`` with readiness through a
published port, and the container is gone afterwards. The Redis test also
exercises Testcontainers: a temporary dependency on a private network that
the validation container reaches by alias.

First run pulls ``python:3.12-slim`` (and ``redis:7-alpine`` for the
dependency test); later runs use the local image cache.
"""

from __future__ import annotations

import subprocess

import pytest

from src.validation import (
    Backend,
    DependencyKind,
    DependencySpec,
    EnvironmentStatus,
    ReadinessKind,
    ReadinessSpec,
    Timeouts,
    ValidationConfig,
    ValidationOrchestrator,
    ValidationRequest,
    ValidationStatus,
    docker_available,
)
from tests._validation_fixtures import CONTAINER_UNITTEST, make_repo

pytestmark = pytest.mark.skipif(not docker_available(), reason="no Docker daemon available")

PORT = 8000


def containers_for(validation_id: str) -> str:
    out = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"label=rootfix.validation={validation_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip()


def validate(tmp_path, repo, **fields):
    fields.setdefault("test_commands", (CONTAINER_UNITTEST,))
    fields.setdefault("start_command", "python app/main.py")
    fields.setdefault("readiness", ReadinessSpec(ReadinessKind.HTTP, port=PORT, path="/health"))
    fields.setdefault(
        "environment_variables", {"APP_PORT": str(PORT), "APP_HOST": "0.0.0.0"}
    )  # noqa: S104
    request = ValidationRequest(repository=str(repo), **fields)
    config = ValidationConfig(backend=Backend.DOCKER, workspace_root=tmp_path)
    return request, ValidationOrchestrator(config=config).validate(request)


def test_passing_validation_in_a_real_container(tmp_path):
    repo, commit = make_repo(tmp_path / "repo")
    request, result = validate(tmp_path, repo, build_command="python --version")

    assert result.status is ValidationStatus.PASSED, result.errors
    assert result.isolation == "docker" and result.revision == commit
    assert result.startup_result.ready
    assert "listening" in result.logs.application_logs
    assert "Python 3" in result.logs.build_logs
    assert result.test_result.tests_passed == 2
    assert result.environment_status is EnvironmentStatus.DESTROYED
    assert containers_for(request.validation_id) == ""  # nothing left running or stopped


def test_failing_test_build_failure_and_startup_failure(tmp_path):
    repo, _ = make_repo(tmp_path / "failing", logic="broken")
    request, result = validate(tmp_path, repo)
    assert result.status is ValidationStatus.FAILED and result.exit_code == 1
    assert result.failed_tests == ["tests.test_logic.T.test_add"]
    assert containers_for(request.validation_id) == ""

    repo, _ = make_repo(tmp_path / "build")
    request, result = validate(tmp_path, repo, build_command="exit 4")
    assert result.status is ValidationStatus.BUILD_FAILED and result.exit_code == 4
    assert containers_for(request.validation_id) == ""

    repo, _ = make_repo(tmp_path / "crash", main="crash")
    request, result = validate(tmp_path, repo)
    assert result.status is ValidationStatus.STARTUP_FAILED
    assert "cannot load config" in result.logs.application_logs
    assert containers_for(request.validation_id) == ""


def test_test_timeout_still_destroys_the_container(tmp_path):
    repo, _ = make_repo(tmp_path / "slow", slow_test=True)
    request, result = validate(tmp_path, repo, timeouts=Timeouts(test=3, overall=120))
    assert result.status is ValidationStatus.TIMEOUT
    assert result.environment_status is EnvironmentStatus.DESTROYED
    assert containers_for(request.validation_id) == ""


def test_source_is_copied_in_so_the_container_cannot_modify_the_host(tmp_path):
    repo, _ = make_repo(tmp_path / "repo")
    marker = (
        "echo changed > app/logic_modified_by_test.txt && python -m unittest discover -s tests -t ."
    )
    request, result = validate(tmp_path, repo, test_commands=(marker,))
    assert result.status is ValidationStatus.PASSED
    assert not (repo / "app/logic_modified_by_test.txt").exists()


def test_temporary_redis_dependency_is_reachable_by_alias_and_destroyed(tmp_path):
    pytest.importorskip("testcontainers")
    repo, _ = make_repo(tmp_path / "repo")
    connect = (
        'python -c "import os, socket; '
        "socket.create_connection((os.environ['REDIS_HOST'], int(os.environ['REDIS_PORT'])), 5); "
        "print('connected to', os.environ['REDIS_URL'])\""
    )
    request, result = validate(
        tmp_path,
        repo,
        start_command=None,
        readiness=None,
        test_commands=(connect,),
        dependencies=(DependencySpec("cache", DependencyKind.REDIS),),
        environment_variables={
            "REDIS_HOST": "{cache.host}",
            "REDIS_PORT": "{cache.port}",
            "REDIS_URL": "{cache.url}",
        },
    )
    assert result.status is ValidationStatus.PASSED, (
        result.errors,
        result.logs.infrastructure_logs,
    )
    assert "connected to redis://cache:6379" in result.logs.test_logs
    assert "dependency:cache" in result.logs.infrastructure_logs
    assert containers_for(request.validation_id) == ""
