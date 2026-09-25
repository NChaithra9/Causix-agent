import json

import pytest

from src.reasoning.agents.issue_agent import AgentError, IssueAgent
from src.reasoning.llm import FakeLLM

VALID = json.dumps({
    "summary": "Refund API returns 500",
    "error_type": "AttributeError",
    "suspected_component": "RefundService",
    "keywords": ["refund", "payment_profile"],
})


def test_parses_valid_json():
    result = IssueAgent(FakeLLM(VALID)).run("Refund API fails")
    assert result.error_type == "AttributeError"
    assert result.suspected_component == "RefundService"
    assert "refund" in result.keywords


def test_parses_json_inside_code_fence():
    result = IssueAgent(FakeLLM(f"```json\n{VALID}\n```")).run("Refund API fails")
    assert result.summary == "Refund API returns 500"


def test_sends_stack_trace_and_logs_to_llm():
    llm = FakeLLM(VALID)
    IssueAgent(llm).run("Refund fails", stack_trace="Traceback ...", logs="ERROR refund")
    _, prompt = llm.calls[0]
    assert "Traceback ..." in prompt and "ERROR refund" in prompt


def test_raises_on_non_json():
    with pytest.raises(AgentError):
        IssueAgent(FakeLLM("I think it's the database")).run("x")


def test_raises_on_wrong_shape():
    with pytest.raises(AgentError):
        IssueAgent(FakeLLM('{"keywords": "not-a-list"}')).run("x")
