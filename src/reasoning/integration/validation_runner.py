"""ScenarioRunner backed by Person 1's Docker validation engine (Phase 6).

What actually runs is the fix's regression test: the fix diff and a new test file are applied as
one patch to a fresh checkout and pytest is executed in a container. PASS/FAIL comes only from
the real exit code. No regression test, no diff, no repository or no Docker -> "not_run".
"""
import re
from collections.abc import Callable

from src.reasoning.schemas import ExecutionResult, FixRecommendation, TestScenario

DEFAULT_BUILD = "pip install -q pytest"

_STATUS = {"PASSED": "passed", "FAILED": "failed"}   # everything else is "error"


def map_status(validation_status: str) -> str:
    return _STATUS.get(validation_status, "error")


def new_file_patch(path: str, content: str) -> str:
    lines = content.rstrip("\n").split("\n")
    body = "\n".join("+" + line for line in lines)
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}\n")


def test_path_for(scenario: TestScenario, fix: FixRecommendation) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (scenario.id or "scenario").lower()).strip("_")
    return f"tests/causix_regression/test_{key}.py"


test_path_for.__test__ = False  # type: ignore[attr-defined]


def not_run(details: str) -> ExecutionResult:
    return ExecutionResult(status="not_run", details=details)


class ValidationScenarioRunner:
    def __init__(self, repo_provider: Callable[[], str | None], orchestrator=None,
                 build_command: str | None = DEFAULT_BUILD,
                 docker_check: Callable[[], bool] | None = None) -> None:
        self.repo_provider = repo_provider
        self._orchestrator = orchestrator
        self.build_command = build_command
        self._docker_check = docker_check

    def _orch(self):
        if self._orchestrator is None:
            from src.validation import ValidationConfig, ValidationOrchestrator
            self._orchestrator = ValidationOrchestrator(config=ValidationConfig())
        return self._orchestrator

    def _docker_ok(self) -> bool:
        if self._docker_check is not None:
            return self._docker_check()
        from src.validation import docker_available
        return docker_available()

    def run(self, scenario: TestScenario, fix: FixRecommendation) -> ExecutionResult:
        if not fix.diff or not fix.regression_test or not fix.regression_test.code.strip():
            return not_run("The fix has no diff or regression test, so there is nothing to execute.")
        repo = self.repo_provider()
        if not repo:
            return not_run("No indexed repository path is known.")
        if not self._docker_ok():
            return not_run("Docker is not available on this machine.")

        from src.validation import ValidationRequest
        test_path = test_path_for(scenario, fix)
        patch = fix.diff.rstrip("\n") + "\n" + new_file_patch(test_path, fix.regression_test.code)
        request = ValidationRequest(
            repository=repo, patch=patch, test_commands=(f"python -m pytest -q {test_path}",),
            build_command=self.build_command, changed_files=tuple(f for f in (fix.file, test_path) if f),
            target_location=fix.location)
        result = self._orch().validate(request)
        return ExecutionResult(status=map_status(result.status.value),
                               details=result.evidence.summary(),
                               duration_ms=int(result.duration * 1000))
