import json

import pytest

from src.reasoning.agents.impact_agent import ImpactAgent
from src.reasoning.agents.issue_agent import AgentError
from src.reasoning.impact import ImpactGraph, ImpactNode
from src.reasoning.llm import FakeLLM
from src.reasoning.schemas import FixRecommendation

CHANGED = "billing/refunds.py::is_eligible"
FIX = FixRecommendation(status="recommended", location=CHANGED, summary="Guard missing profile.")
NODES = [
    ImpactNode(id="a", kind="function", name="process_refund", location="billing/refunds.py::process_refund", depth=1),
    ImpactNode(id="b", kind="api", name="POST /refunds", depth=2, relation="exposes"),
    ImpactNode(id="c", kind="test", name="test_refund_flow", depth=1, relation="tests"),
]
GRAPH = ImpactGraph(changed=CHANGED, nodes=NODES)


def impact_json(**overrides) -> str:
    base = {"summary": "Refund processing and its API are affected.", "risk_level": "medium",
            "reasons": {"N1": "Calls is_eligible directly.", "N2": "Exposes process_refund."}}
    return json.dumps({**base, **overrides})


def test_groups_come_from_graph_not_llm():
    result = ImpactAgent(FakeLLM(impact_json())).run(FIX, GRAPH)
    assert result.status == "explained" and result.risk_level == "medium"
    assert [i.name for i in result.directly_affected] == ["process_refund"]
    assert [i.name for i in result.potentially_affected] == ["POST /refunds"]
    assert [i.name for i in result.tests_to_run] == ["test_refund_flow"]
    assert result.directly_affected[0].reason == "Calls is_eligible directly."


def test_every_node_lands_in_exactly_one_group():
    r = ImpactAgent(FakeLLM(impact_json())).run(FIX, GRAPH)
    names = [i.name for i in r.directly_affected + r.potentially_affected + r.tests_to_run]
    assert sorted(names) == sorted(n.name for n in NODES)


def test_items_without_llm_reason_get_a_default():
    r = ImpactAgent(FakeLLM(impact_json())).run(FIX, GRAPH)
    assert r.tests_to_run[0].reason == "tests the changed code"


def test_unknown_item_reason_is_dropped():
    r = ImpactAgent(FakeLLM(impact_json(reasons={"N9": "made up"}))).run(FIX, GRAPH)
    assert any("N9" in w for w in r.warnings)


def test_bad_risk_level_becomes_medium():
    r = ImpactAgent(FakeLLM(impact_json(risk_level="catastrophic"))).run(FIX, GRAPH)
    assert r.risk_level == "medium"


def test_warns_when_no_tests_cover_change():
    no_tests = ImpactGraph(changed=CHANGED, nodes=NODES[:2])
    r = ImpactAgent(FakeLLM(impact_json())).run(FIX, no_tests)
    assert r.tests_to_run == []
    assert any("No tests cover" in w for w in r.warnings)


def test_no_dependents_skips_llm():
    llm = FakeLLM(impact_json())
    r = ImpactAgent(llm).run(FIX, ImpactGraph(changed=CHANGED))
    assert r.status == "explained" and r.risk_level == "low"
    assert llm.calls == []


@pytest.mark.parametrize("fix", [None, FixRecommendation(status="skipped", reason="x")])
def test_skipped_without_recommended_fix(fix):
    llm = FakeLLM(impact_json())
    assert ImpactAgent(llm).run(fix, GRAPH).status == "skipped"
    assert llm.calls == []


def test_prompt_lists_numbered_items():
    llm = FakeLLM(impact_json())
    ImpactAgent(llm).run(FIX, GRAPH)
    system, prompt = llm.calls[0]
    assert "Impact Agent" in system
    assert "N2 [api, depth 2, exposes] POST /refunds" in prompt


def test_malformed_output_raises():
    with pytest.raises(AgentError):
        ImpactAgent(FakeLLM('{"risk_level": "low"}')).run(FIX, GRAPH)
