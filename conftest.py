"""Repo-wide test setup: never talk to a real Neo4j / Docker unless a test asks for it."""
import pytest


@pytest.fixture(autouse=True)
def _stub_engines(monkeypatch):
    monkeypatch.setenv("CAUSIX_ENGINES", "stub")
    try:
        import src.api.main as main
        monkeypatch.setattr(main, "_engines", None)
    except Exception:  # API not importable in some narrow runs
        pass
