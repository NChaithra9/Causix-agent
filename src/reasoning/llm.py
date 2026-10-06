"""LLM abstraction. Agents depend on LLMClient, never on a vendor SDK directly.

Pick the provider with environment variables (e.g. in .env):
    CAUSIX_LLM_PROVIDER = fake | openai | anthropic      (default: fake)
    CAUSIX_LLM_MODEL    = model name for that provider     (required for openai/anthropic)
plus the provider's own key: OPENAI_API_KEY or ANTHROPIC_API_KEY.
"""
import json
import os
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


class LLMConfigError(Exception):
    """Raised when the LLM provider settings are missing or invalid."""


class FakeLLM:
    """Deterministic stand-in used for local runs and tests (no API key needed).

    - FakeLLM(response="...")          -> always returns that text
    - FakeLLM(responses=["a", "b"])    -> returns them in order, one per call
    - FakeLLM()                        -> a safe default per agent
    """

    DEFAULT_RESPONSE = json.dumps({
        "summary": "Placeholder understanding from FakeLLM",
        "error_type": None,
        "suspected_component": None,
        "keywords": [],
    })

    DEFAULT_RCA_RESPONSE = json.dumps({
        "root_cause": "FakeLLM cannot reason; configure CAUSIX_LLM_PROVIDER for a real analysis.",
        "confidence": "low",
        "affected_component": None,
        "location": None,
        "evidence_ids": [],
        "reasoning": "",
        "suggested_fix": None,
        "insufficient_evidence": True,
    })

    DEFAULT_FIX_RESPONSE = json.dumps({
        "summary": "FakeLLM cannot propose a fix; configure CAUSIX_LLM_PROVIDER.",
        "explanation": "", "code_after": None, "test_name": None, "test_code": None, "risks": [],
    })

    DEFAULT_IMPACT_RESPONSE = json.dumps({
        "summary": "FakeLLM cannot explain impact; configure CAUSIX_LLM_PROVIDER.",
        "risk_level": "low", "reasons": {},
    })

    DEFAULT_SCENARIO_RESPONSE = json.dumps({
        "title": "FakeLLM cannot write a scenario; configure CAUSIX_LLM_PROVIDER.",
        "setup": [], "action": "n/a", "expected": "n/a", "expected_error_code": None,
    })

    def __init__(self, response: str | None = None, responses: list[str] | None = None) -> None:
        self.response = response
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self.responses:
            return self.responses.pop(0)
        if self.response is not None:
            return self.response
        if "Scenario Agent" in system:
            return self.DEFAULT_SCENARIO_RESPONSE
        if "Impact Agent" in system:
            return self.DEFAULT_IMPACT_RESPONSE
        if "Fix Agent" in system:
            return self.DEFAULT_FIX_RESPONSE
        return self.DEFAULT_RCA_RESPONSE if "Root Cause Agent" in system else self.DEFAULT_RESPONSE


class OpenAILLM:
    def __init__(self, model: str) -> None:
        from openai import OpenAI  # reads OPENAI_API_KEY
        self.client, self.model = OpenAI(), model

    def complete(self, system: str, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


class AnthropicLLM:
    def __init__(self, model: str) -> None:
        from anthropic import Anthropic  # reads ANTHROPIC_API_KEY
        self.client, self.model = Anthropic(), model

    def complete(self, system: str, prompt: str) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=2000, temperature=0, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")


PROVIDERS = {"openai": OpenAILLM, "anthropic": AnthropicLLM}


def get_llm() -> LLMClient:
    """Single place that chooses the LLM, based on CAUSIX_LLM_PROVIDER / CAUSIX_LLM_MODEL."""
    provider = os.getenv("CAUSIX_LLM_PROVIDER", "fake").strip().lower()
    if provider == "fake":
        return FakeLLM()
    if provider not in PROVIDERS:
        raise LLMConfigError(
            f"Unknown CAUSIX_LLM_PROVIDER '{provider}'. Use one of: fake, {', '.join(PROVIDERS)}")
    model = os.getenv("CAUSIX_LLM_MODEL", "").strip()
    if not model:
        raise LLMConfigError(f"Set CAUSIX_LLM_MODEL to the {provider} model name you want to use.")
    return PROVIDERS[provider](model)
