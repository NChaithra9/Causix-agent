"""Scenario Agent: turns a recommended fix into a business test scenario.

The LLM writes setup / action / expected. It never reports a result: execution is attached
later from the sandbox runner. Output is validated in code (fields, sizes, error code).
"""
import hashlib

from pydantic import BaseModel, Field, ValidationError

from src.reasoning.agents.issue_agent import AgentError, extract_json
from src.reasoning.llm import LLMClient
from src.reasoning.prompts import SCENARIO_SYSTEM, build_scenario_prompt
from src.reasoning.schemas import FixRecommendation, IssueUnderstanding, RootCauseAnalysis, TestScenario

MAX_SETUP_STEPS = 8


class _RawScenario(BaseModel):
    title: str = ""
    setup: list[str] = Field(default_factory=list)
    action: str = ""
    expected: str = ""
    expected_error_code: str | None = None


def skipped(reason: str) -> TestScenario:
    return TestScenario(status="skipped", reason=reason)


class ScenarioAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(self, issue: IssueUnderstanding, rca: RootCauseAnalysis | None,
            fix: FixRecommendation | None) -> TestScenario:
        if fix is None or fix.status != "recommended":
            return skipped("No recommended fix, so there is nothing to build a scenario for.")

        text = self.llm.complete(SCENARIO_SYSTEM, build_scenario_prompt(issue, rca, fix))
        try:
            raw = _RawScenario(**extract_json(text))
        except ValidationError as exc:
            raise AgentError(f"Scenario JSON did not match the expected shape: {exc}") from exc

        action, expected = raw.action.strip(), raw.expected.strip()
        if not action or not expected:
            raise AgentError("Scenario is missing its action or expected result.")

        warnings: list[str] = []
        setup = [s.strip() for s in raw.setup if s and s.strip()]
        if len(setup) > MAX_SETUP_STEPS:
            warnings.append(f"Trimmed setup to {MAX_SETUP_STEPS} steps.")
            setup = setup[:MAX_SETUP_STEPS]
        if not setup:
            warnings.append("Scenario has no setup steps; check that the starting state is clear.")

        code = (raw.expected_error_code or "").strip() or None
        if code and code not in (fix.code_after or "") and code not in (fix.summary or ""):
            warnings.append(f"Error code '{code}' does not appear in the proposed fix; verify it.")

        title = raw.title.strip() or f"Scenario for {fix.location}"
        sid = "SC-" + hashlib.sha1(f"{fix.location}|{action}|{expected}".encode()).hexdigest()[:8]
        return TestScenario(
            status="generated", id=sid, title=title, setup=setup, action=action,
            expected=expected, expected_error_code=code, related_location=fix.location,
            regression_test=fix.regression_test.name if fix.regression_test else None,
            warnings=warnings)
