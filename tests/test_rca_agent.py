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


# --- location resolution (Phase 8 live-test bug: "file.py:59" was dropped, so fix/impact skipped) ---
CFG = "src/graph/config.py::Neo4jConfig.from_env"
LIVE_EVIDENCE = [
    Evidence(source="fix_localization", description="[KNOWN] exact method to change (lines 52-76)",
             location=CFG),
    Evidence(source="stack_trace", description="[FACT] Frame: src/graph/config.py:59 in from_env",
             location=CFG),
    Evidence(source="stack_trace", description="[FACT] Frame: src/other.py:3 in run",
             location="src/other.py::run"),
]


def live(**o):
    return RCAAgent(FakeLLM(rca_json(evidence_ids=["E1"], **o))).run(ISSUE, LIVE_EVIDENCE)


def test_file_and_line_location_resolves_to_the_evidence_location():
    for loose in ("src/graph/config.py:59", "src/graph/config.py", "src/graph/config.py:52-76"):
        rca = live(location=loose)
        assert rca.location == CFG and any("Matched location" in w for w in rca.warnings)


def test_location_id_wins_over_free_text():
    rca = live(location="nonsense", location_id="e3")
    assert rca.location == "src/other.py::run"


def test_bad_location_id_falls_back_to_exact_text():
    rca = live(location=CFG, location_id="E9")
    assert rca.location == CFG and any("location_id" in w for w in rca.warnings)


def test_falls_back_to_known_fix_localization_when_model_gives_nothing():
    for kw in ({"location": None}, {"location": "made_up.py::Ghost.method"}):
        rca = live(**kw)
        assert rca.location == CFG and any("fix-localization" in w for w in rca.warnings)


def test_named_method_not_in_evidence_is_never_repointed_to_another_method():
    # only semantic evidence, two methods in the same file, model names a third
    evidence = [Evidence(source="semantic", description="a", location="m.py::A.one"),
                Evidence(source="semantic", description="b", location="m.py::A.two")]
    rca = RCAAgent(FakeLLM(rca_json(location="m.py::A.ghost"))).run(ISSUE, evidence)
    assert rca.location is None


def test_ambiguous_file_without_engine_location_is_dropped():
    evidence = [Evidence(source="semantic", description="a", location="m.py::A.one"),
                Evidence(source="semantic", description="b", location="m.py::A.two")]
    rca = RCAAgent(FakeLLM(rca_json(location="m.py:10"))).run(ISSUE, evidence)
    assert rca.location is None and any("Dropped location" in w for w in rca.warnings)


def test_fallback_not_used_when_evidence_is_insufficient_anyway():
    rca = live(location=None, insufficient_evidence=True)
    assert rca.insufficient_evidence   # location may be set, but downstream skips on insufficiency
