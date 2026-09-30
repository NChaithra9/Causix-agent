import ast
import json
from pathlib import Path

import pytest

from src.reasoning.agents.fix_agent import FixAgent, make_diff, source_of
from src.reasoning.agents.issue_agent import AgentError
from src.reasoning.llm import FakeLLM
from src.reasoning.retrieval.chunker import chunk_repository
from src.reasoning.schemas import IssueUnderstanding, RootCauseAnalysis

SAMPLE = Path(__file__).parent / "fixtures" / "sample_repo"
REFUND = "payment_service/refund_service.py::RefundService.is_eligible_for_refund"
CHUNKS = {c.id: c for c in chunk_repository(SAMPLE)}
lookup = CHUNKS.get

ISSUE = IssueUnderstanding(summary="Refund crashes when payment profile is missing")
RCA = RootCauseAnalysis(root_cause="Payment profile can be None.", confidence="high",
                        location=REFUND, suggested_fix="Return False when profile is missing.")

FIXED = '''def is_eligible_for_refund(self, order):
    profile = self.repository.get_payment_profile(order.customer_id)
    if profile is None:
        return False
    return profile.allows_refunds and order.amount > 0
'''
TEST = '''def test_refund_rejected_without_payment_profile():
    repo = FakeRepo(profile=None)
    assert RefundService(repo).is_eligible_for_refund(Order(amount=10)) is False
'''


def fix_json(**overrides) -> str:
    base = {"summary": "Guard against a missing payment profile.",
            "explanation": "profile can be None, so check it before use.",
            "code_after": FIXED, "test_name": "test_refund_rejected_without_payment_profile",
            "test_code": TEST, "risks": ["Callers relying on AttributeError"]}
    return json.dumps({**base, **overrides})


def test_source_of_is_valid_python_at_column_zero():
    code = source_of(CHUNKS[REFUND])
    assert code.startswith("def is_eligible_for_refund")
    ast.parse(code)


def test_recommends_fix_with_diff_and_test():
    fix = FixAgent(FakeLLM(fix_json())).run(ISSUE, RCA, lookup)
    assert fix.status == "recommended" and fix.applied is False
    assert fix.file == "payment_service/refund_service.py"
    assert "+        if profile is None:" in fix.diff          # re-indented to file level
    assert "@@ -5," in fix.diff                               # real line number of the method
    assert fix.regression_test.name == "test_refund_rejected_without_payment_profile"
    assert fix.warnings == []


def test_prompt_contains_current_code_and_root_cause():
    llm = FakeLLM(fix_json())
    FixAgent(llm).run(ISSUE, RCA, lookup)
    system, prompt = llm.calls[0]
    assert "Fix Agent" in system
    assert "Payment profile can be None." in prompt
    assert "get_payment_profile" in prompt


@pytest.mark.parametrize("rca, reason_part", [
    (None, "No root cause"),
    (RCA.model_copy(update={"insufficient_evidence": True}), "insufficient evidence"),
    (RCA.model_copy(update={"location": None}), "no exact code location"),
    (RCA.model_copy(update={"location": "ghost.py::nope"}), "not indexed"),
])
def test_skips_without_calling_llm(rca, reason_part):
    llm = FakeLLM(fix_json())
    fix = FixAgent(llm).run(ISSUE, rca, lookup)
    assert fix.status == "skipped" and reason_part in fix.reason
    assert llm.calls == []


def test_invalid_python_is_dropped():
    fix = FixAgent(FakeLLM(fix_json(code_after="def broken(:"))).run(ISSUE, RCA, lookup)
    assert fix.code_after is None and fix.diff is None
    assert any("not valid Python" in w for w in fix.warnings)


def test_renamed_function_is_dropped():
    renamed = FIXED.replace("is_eligible_for_refund", "check_refund")
    fix = FixAgent(FakeLLM(fix_json(code_after=renamed))).run(ISSUE, RCA, lookup)
    assert fix.code_after is None
    assert any("does not define" in w for w in fix.warnings)


def test_unchanged_code_gives_no_diff():
    same = source_of(CHUNKS[REFUND])
    fix = FixAgent(FakeLLM(fix_json(code_after=same))).run(ISSUE, RCA, lookup)
    assert fix.diff is None
    assert any("identical" in w for w in fix.warnings)


def test_bad_regression_test_is_dropped():
    fix = FixAgent(FakeLLM(fix_json(test_code="print('hi')"))).run(ISSUE, RCA, lookup)
    assert fix.regression_test is None
    assert fix.code_after is not None                       # the fix itself is kept


def test_malformed_output_raises():
    with pytest.raises(AgentError):
        FixAgent(FakeLLM('{"risks": []}')).run(ISSUE, RCA, lookup)


def test_make_diff_shifts_hunk_to_file_lines():
    chunk = CHUNKS[REFUND]
    diff = make_diff(chunk, source_of(chunk), FIXED)
    assert diff.splitlines()[0] == "--- a/payment_service/refund_service.py"
    assert diff.splitlines()[2].startswith("@@ -5,")
