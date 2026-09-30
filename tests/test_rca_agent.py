import json

import pytest

from src.reasoning.agents.issue_agent import AgentError
from src.reasoning.agents.rca_agent import RCAAgent
from src.reasoning.llm import FakeLLM
from src.reasoning.schemas import Evidence, IssueUnderstanding

ISSUE = IssueUnderstanding(summary="Refund API returns 500", error_type="AttributeError",
                           suspected_component="RefundService", keywords=["refund"])
REFUND = "payment_service/refund_service.py::RefundService.is_eligible_for_refund"
EVIDENCE = [
    Evidence(source="stack_trace", description="AttributeError: 'NoneType' has no attribute "
             "'allows_refunds' raised here", location=REFUND),
    Evidence(source="semantic", description="method RefundService.process_refund",
             location="payment_service/refund_service.py::RefundService.process_refund"),
]


def rca_json(**overrides) -> str:
    base = {"root_cause": "Payment profile can be None and is used without a check.",
            "confidence": "high", "affected_component": "RefundService", "location": REFUND,
            "evidence_ids": ["E1"], "reasoning": "E1 shows the NoneType access.",
            "suggested_fix": "Return False when the payment profile is missing.",
            "insufficient_evidence": False}
    return json.dumps({**base, **overrides})


def test_grounded_answer_is_kept():
    rca = RCAAgent(FakeLLM(rca_json())).run(ISSUE, EVIDENCE)
    assert rca.location == REFUND
    assert rca.confidence == "high"
    assert rca.supporting_evidence[0].source == "stack_trace"
    assert rca.warnings == []


def test_evidence_is_numbered_in_prompt():
    llm = FakeLLM(rca_json())
    RCAAgent(llm).run(ISSUE, EVIDENCE, stack_trace="Traceback ...")
    system, prompt = llm.calls[0]
    assert "Root Cause Agent" in system
    assert f"E1 [stack_trace] @ {REFUND}" in prompt
    assert "E2 [semantic]" in prompt and "Traceback ..." in prompt


def test_no_evidence_skips_llm():
    llm = FakeLLM(rca_json())
    rca = RCAAgent(llm).run(ISSUE, [])
    assert rca.insufficient_evidence and rca.confidence == "low"
    assert llm.calls == []                     # never asked to guess


def test_invented_location_is_dropped():
    rca = RCAAgent(FakeLLM(rca_json(location="made_up.py::Ghost.method"))).run(ISSUE, EVIDENCE)
    assert rca.location is None
    assert any("made_up.py" in w for w in rca.warnings)


def test_unknown_citation_dropped_and_marked_insufficient():
    rca = RCAAgent(FakeLLM(rca_json(evidence_ids=["E9"]))).run(ISSUE, EVIDENCE)
    assert rca.supporting_evidence == []
    assert rca.insufficient_evidence and rca.confidence == "low"
    assert any("E9" in w for w in rca.warnings)


def test_insufficient_forces_low_confidence():
    rca = RCAAgent(FakeLLM(rca_json(insufficient_evidence=True, confidence="high"))).run(ISSUE, EVIDENCE)
    assert rca.confidence == "low"


def test_bad_confidence_value_becomes_low():
    rca = RCAAgent(FakeLLM(rca_json(confidence="very sure"))).run(ISSUE, EVIDENCE)
    assert rca.confidence == "low"


def test_malformed_output_raises():
    with pytest.raises(AgentError):
        RCAAgent(FakeLLM('{"confidence": "high"}')).run(ISSUE, EVIDENCE)
