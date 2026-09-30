import pytest

from src.reasoning.llm import FakeLLM, LLMConfigError, get_llm


def test_default_provider_is_fake(monkeypatch):
    monkeypatch.delenv("CAUSIX_LLM_PROVIDER", raising=False)
    assert isinstance(get_llm(), FakeLLM)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("CAUSIX_LLM_PROVIDER", "mystery")
    with pytest.raises(LLMConfigError, match="Unknown"):
        get_llm()


def test_real_provider_requires_model(monkeypatch):
    monkeypatch.setenv("CAUSIX_LLM_PROVIDER", "openai")
    monkeypatch.delenv("CAUSIX_LLM_MODEL", raising=False)
    with pytest.raises(LLMConfigError, match="CAUSIX_LLM_MODEL"):
        get_llm()


def test_provider_is_built_with_model(monkeypatch):
    built = {}

    class Dummy:
        def __init__(self, model):
            built["model"] = model

    monkeypatch.setitem(__import__("src.reasoning.llm", fromlist=["PROVIDERS"]).PROVIDERS,
                        "openai", Dummy)
    monkeypatch.setenv("CAUSIX_LLM_PROVIDER", "OpenAI")
    monkeypatch.setenv("CAUSIX_LLM_MODEL", "some-model")
    assert isinstance(get_llm(), Dummy) and built["model"] == "some-model"


def test_fake_llm_scripted_responses_in_order():
    llm = FakeLLM(responses=["first", "second"])
    assert llm.complete("s", "p") == "first"
    assert llm.complete("s", "p") == "second"
