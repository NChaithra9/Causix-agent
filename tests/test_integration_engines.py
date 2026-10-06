from types import SimpleNamespace as NS

import pytest

from src.reasoning.integration.engine_facts import EngineFacts
from src.reasoning.integration.engines import EngineConfigError, RepoContext, build_engines
from src.reasoning.integration.locations import LocationIndex
from src.reasoning.facts import StubFactsProvider
from src.reasoning.execution import StubScenarioRunner
from src.reasoning.schemas import IssueUnderstanding

ISSUE = IssueUnderstanding(summary="refund fails", error_type="KeyError")


def build(mode, **kw):
    return build_engines(RepoContext(), LocationIndex(), StubScenarioRunner(), mode=mode, **kw)


def test_stub_mode_and_auto_without_password(monkeypatch):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    for mode in ("stub", "auto"):
        e = build(mode)
        assert e.mode == "stub" and isinstance(e.facts, StubFactsProvider) and e.impact_provider is None
        assert e.ingest("/x") is False


def test_auto_falls_back_when_neo4j_down_but_real_fails_loudly(monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    def down():
        raise RuntimeError("refused")
    e = build("auto", connect=down)
    assert e.mode == "stub" and "refused" in e.notes[0]
    with pytest.raises(EngineConfigError):
        build("real", connect=down)
    with pytest.raises(EngineConfigError):
        build("bogus")


def test_real_mode_wires_engines(monkeypatch):
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    monkeypatch.setenv("CAUSIX_EXECUTION", "docker")
    conn = object()
    e = build("real", connect=lambda: conn)
    from src.impact_analysis import GraphImpactProvider
    from src.reasoning.integration.validation_runner import ValidationScenarioRunner
    assert e.mode == "real" and isinstance(e.impact_provider, GraphImpactProvider)
    assert isinstance(e.scenario_runner, ValidationScenarioRunner) and e.connection is conn
    monkeypatch.setenv("CAUSIX_EXECUTION", "stub")
    assert isinstance(build("real", connect=lambda: conn).scenario_runner, StubScenarioRunner)


def item(file, method, details="d", status="KNOWN", type_="stack_trace"):
    return NS(file=file, method=method, details=details, status=NS(value=status), type=type_)


def test_engine_facts_combines_rca_and_localization(monkeypatch):
    import src.fix_localization as fl
    import src.rca as rca

    rca_result = NS(evidence=[item("billing/refunds.py", "is_eligible")])
    loc = NS(resolution_status="RESOLVED", evidence=[item("billing/refunds.py", "is_eligible"),
                                                     item("tests/t.py", None, type_="tests")],
             primary_location=NS(method_localization=NS(file_path="billing/refunds.py",
                                                        qualified_name="is_eligible", start_line=3,
                                                        end_line=9, failing_line=5)))
    monkeypatch.setattr(rca, "RCAInput", lambda **kw: NS(**kw))
    monkeypatch.setattr(rca, "investigate", lambda inp, connection, repo_root: rca_result)
    monkeypatch.setattr(fl, "localize_fix", lambda r, connection, repo_root: loc)
    facts = EngineFacts(object(), lambda: "/repo")
    out = facts.collect(ISSUE, None, "trace")
    assert out[0].source == "fix_localization" and out[0].location == "billing/refunds.py::is_eligible"
    assert "failing line 5" in out[0].description
    assert [e.location for e in out[1:]] == ["billing/refunds.py::is_eligible", "tests/t.py"]
    assert facts.last_localization is loc


def test_engine_facts_empty_without_indexed_repo():
    assert EngineFacts(object(), lambda: None).collect(ISSUE, None, None) == []
