import json

import pytest

from src.reasoning.agents.issue_agent import AgentError
from src.reasoning.agents.scenario_agent import ScenarioAgent
from src.reasoning.execution import StubScenarioRunner
from src.reasoning.llm import FakeLLM
from src.reasoning.schemas import FixRecommendation, IssueUnderstanding, RegressionTest

ISSUE = IssueUnderstanding(summary="Refund fails for customers without a payment profile")
FIX = FixRecommendation(
    status="recommended", location="billing/refunds.py::is_eligible",
    summary="Return PAYMENT_PROFILE_MISSING when no profile exists.",
    code_after="def f(c):\n    if not c.profile:\n        return 'PAYMENT_PROFILE_MISSING'\n",
    regression_test=RegressionTest(name="test_missing_profile", code="def test_missing_profile(): ..."))


def scenario_json(**overrides) -> str:
    base = {"title": "Refund without payment profile",
            "setup": ["Customer exists", "Customer has no saved payment profile"],
            "action": "Attempt a refund for the customer's order.",
            "expected": "The refund is rejected with a clear error.",
            "expected_error_code": "PAYMENT_PROFILE_MISSING"}
    return json.dumps({**base, **overrides})


def run(text, fix=FIX):
    return ScenarioAgent(FakeLLM(text)).run(ISSUE, None, fix)


def test_generates_scenario_from_fix():
    s = run(scenario_json())
    assert s.status == "generated" and s.id.startswith("SC-")
    assert s.setup[1] == "Customer has no saved payment profile"
    assert s.expected_error_code == "PAYMENT_PROFILE_MISSING"
    assert s.related_location == FIX.location and s.regression_test == "test_missing_profile"
    assert s.execution is None and s.warnings == []


def test_id_is_stable():
    assert run(scenario_json()).id == run(scenario_json()).id


@pytest.mark.parametrize("fix", [None, FixRecommendation(status="skipped", reason="x")])
def test_skipped_without_recommended_fix(fix):
    llm = FakeLLM(scenario_json())
    s = ScenarioAgent(llm).run(ISSUE, None, fix)
    assert s.status == "skipped" and llm.calls == []


def test_unknown_error_code_warns():
    s = run(scenario_json(expected_error_code="MADE_UP"))
    assert any("MADE_UP" in w for w in s.warnings)


def test_missing_setup_warns_and_long_setup_trimmed():
    assert any("no setup" in w for w in run(scenario_json(setup=[])).warnings)
    s = run(scenario_json(setup=[f"step {i}" for i in range(12)]))
    assert len(s.setup) == 8 and any("Trimmed" in w for w in s.warnings)


def test_missing_action_or_bad_json_raises():
    with pytest.raises(AgentError):
        run(scenario_json(action=" "))
    with pytest.raises(AgentError):
        run("not json")
    with pytest.raises(AgentError):
        run(json.dumps({"setup": "not a list", "action": "a", "expected": "b"}))


def test_stub_runner_never_claims_pass():
    s = run(scenario_json())
    result = StubScenarioRunner().run(s, FIX)
    assert result.status == "not_run"


def test_orchestrator_records_runner_crash():
    from src.api.models import AnalyzeRequest
    from src.reasoning.agents.issue_agent import IssueAgent
    from src.reasoning.facts import StubFactsProvider
    from src.reasoning.orchestrator import Orchestrator

    class Boom:
        def run(self, scenario, fix):
            raise RuntimeError("docker down")

    class FixedFix:
        def run(self, issue, rca, lookup):
            return FIX

    llm = FakeLLM(responses=[json.dumps({"summary": "s"}), scenario_json()])
    orch = Orchestrator(IssueAgent(llm), StubFactsProvider(), fix_agent=FixedFix(),
                        scenario_agent=ScenarioAgent(llm), scenario_runner=Boom())
    result = orch.analyze(AnalyzeRequest(issue="x"))
    assert result.scenario.status == "generated" and result.scenario.execution.status == "error"
    assert any("docker down" in n for n in result.notes)
