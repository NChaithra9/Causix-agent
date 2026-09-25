"""Issue Agent: turns a raw report into a structured IssueUnderstanding."""
import json

from pydantic import ValidationError

from src.reasoning.llm import LLMClient
from src.reasoning.prompts import ISSUE_SYSTEM, build_issue_prompt
from src.reasoning.schemas import IssueUnderstanding


class AgentError(Exception):
    """Raised when an agent cannot produce a valid result."""


def extract_json(text: str) -> dict:
    """Take the outermost {...} block, so ```json fences or stray text don't break parsing."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise AgentError("LLM response did not contain a JSON object")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AgentError(f"LLM returned invalid JSON: {exc}") from exc


class IssueAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(self, issue: str, stack_trace: str | None = None, logs: str | None = None) -> IssueUnderstanding:
        raw = self.llm.complete(ISSUE_SYSTEM, build_issue_prompt(issue, stack_trace, logs))
        try:
            return IssueUnderstanding(**extract_json(raw))
        except ValidationError as exc:
            raise AgentError(f"LLM JSON did not match IssueUnderstanding: {exc}") from exc
