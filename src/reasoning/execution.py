"""Contract with Person 1's sandbox (their Phase 6). The runner, not the LLM, decides PASS/FAIL."""
from typing import Protocol

from src.reasoning.schemas import ExecutionResult, FixRecommendation, TestScenario


class ScenarioRunner(Protocol):
    def run(self, scenario: TestScenario, fix: FixRecommendation) -> ExecutionResult: ...


class StubScenarioRunner:
    """Placeholder until the Docker/Testcontainers sandbox is connected (Phase 8)."""

    def run(self, scenario: TestScenario, fix: FixRecommendation) -> ExecutionResult:
        return ExecutionResult(status="not_run",
                               details="Sandbox not connected yet; scenario was not executed.")
