import json

import pytest

from src.reasoning.agents.documentation_agent import DocumentationAgent
from src.reasoning.agents.issue_agent import AgentError
from src.reasoning.llm import FakeLLM
from src.reasoning.schemas import (AnalysisResult, Evidence, ExecutionResult, FixRecommendation,
                                   ImpactExplanation, ImpactItem, IssueUnderstanding,
                                   RootCauseAnalysis, TestScenario)

EV = Evidence(source="graph", description="is_eligible ignores None", location="billing/refunds.py::is_eligible")
RCA = RootCauseAnalysis(root_cause="Eligibility ignores missing orders.", confidence="high",
                        location=EV.location, supporting_evidence=[EV])
FIX = FixRecommendation(status="recommended", location=EV.location, summary="Guard None.",
                        diff="-a\n+b\n", risks=["Behaviour change"])
IMPACT = ImpactExplanation(status="explained", risk_level="medium", summary="Refunds affected.",
                           directly_affected=[ImpactItem(name="process_refund", kind="function", reason="Calls it")])
SC = TestScenario(status="generated", title="Missing order", setup=["No order"], action="Check",
                  expected="Not eligible", execution=ExecutionResult(status="not_run", details="no sandbox"))


def result(**kw) -> AnalysisResult:
    base = dict(issue=IssueUnderstanding(summary="Refund wrong"), rca=RCA, fix=FIX, impact=IMPACT,
                scenario=SC, notes=["a note"])
    return AnalysisResult(**{**base, **kw})


def docs_json(**o) -> str:
    return json.dumps({"title": "Refund eligibility bug", "summary": "Missing orders were eligible.",
                       "lessons": ["Validate inputs"], **o})


def test_report_contains_facts_from_result_not_llm():
    d = DocumentationAgent(FakeLLM(docs_json())).run(result())
    assert d.status == "generated" and d.title == "Refund eligibility bug"
    md = d.markdown
    for expected in ["# Refund eligibility bug", "Eligibility ignores missing orders.", "```diff",
                     "`process_refund`", "Risk level: **medium**", "Execution: **not_run**",
                     "Validate inputs", "a note", "Not applied"]:
        assert expected in md


def test_sections_omitted_when_missing():
    d = DocumentationAgent(FakeLLM(docs_json())).run(result(fix=None, impact=None, scenario=None, notes=[]))
    assert "## Recommended fix" not in d.markdown and "## Impact" not in d.markdown
    assert "## Caveats" not in d.markdown and "## Root cause" in d.markdown


@pytest.mark.parametrize("res", [result(rca=None),
                                 result(rca=RCA.model_copy(update={"insufficient_evidence": True}))])
def test_skipped_without_grounded_rca(res):
    llm = FakeLLM(docs_json())
    d = DocumentationAgent(llm).run(res)
    assert d.status == "skipped" and llm.calls == []


def test_lessons_trimmed_and_title_fallback():
    d = DocumentationAgent(FakeLLM(docs_json(title="", lessons=list("abcde")))).run(result())
    assert len(d.lessons) == 3 and d.warnings and d.title.startswith("Incident:")


def test_bad_output_raises():
    with pytest.raises(AgentError):
        DocumentationAgent(FakeLLM(docs_json(summary=" "))).run(result())
    with pytest.raises(AgentError):
        DocumentationAgent(FakeLLM("nope")).run(result())
