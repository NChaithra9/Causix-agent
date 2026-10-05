from pathlib import Path

from src.reasoning.impact import CodeCallGraph, ImpactNode, StubImpactProvider
from src.reasoning.retrieval.chunker import chunk_repository

REPO = Path(__file__).parent / "fixtures" / "impact_repo"
IS_ELIGIBLE = "billing/refunds.py::is_eligible"


def graph() -> CodeCallGraph:
    g = CodeCallGraph()
    g.load(chunk_repository(REPO))
    return g


def test_finds_direct_and_indirect_callers_with_depth():
    nodes = {n.name: n for n in graph().impact_of(IS_ELIGIBLE).nodes}
    assert nodes["process_refund"].depth == 1
    assert nodes["refund_endpoint"].depth == 2
    assert "unrelated_report" not in nodes


def test_tests_are_marked_and_not_expanded():
    nodes = {n.name: n for n in graph().impact_of(IS_ELIGIBLE).nodes}
    assert nodes["test_is_eligible_positive_amount"].kind == "test"
    assert nodes["test_is_eligible_positive_amount"].relation == "tests"


def test_each_node_appears_once():
    ids = [n.id for n in graph().impact_of(IS_ELIGIBLE).nodes]
    assert len(ids) == len(set(ids))
    assert IS_ELIGIBLE not in ids                      # the changed code is not its own dependent


def test_max_depth_limits_traversal():
    g = CodeCallGraph(max_depth=1)
    g.load(chunk_repository(REPO))
    assert {n.depth for n in g.impact_of(IS_ELIGIBLE).nodes} == {1}


def test_stub_provider_returns_given_nodes():
    node = ImpactNode(id="x", kind="api", name="POST /refund", depth=2, relation="exposes")
    assert StubImpactProvider([node]).impact_of("a.py::f").nodes == [node]
