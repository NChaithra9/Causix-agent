"""LLM abstraction. Agents depend on LLMClient, never on a vendor SDK directly."""
import json
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


class FakeLLM:
    """Deterministic stand-in used for local runs and tests (no API key needed)."""

    DEFAULT_RESPONSE = json.dumps({
        "summary": "Placeholder understanding from FakeLLM",
        "error_type": None,
        "suspected_component": None,
        "keywords": [],
    })

    def __init__(self, response: str | None = None) -> None:
        self.response = response or self.DEFAULT_RESPONSE
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.response


def get_llm() -> LLMClient:
    """Single place to choose the real LLM later (Phase 1 wrap-up / Phase 3)."""
    return FakeLLM()
