"""Pure-logic tests for Phase 5 (no Neo4j): path reconstruction, path
collection, rendering, the caller graph's cycle/depth handling (driven by a
tiny stub that answers only the two queries Phase 3's reverse traversal
issues), input handling, and the Person 2 adapter's field mapping."""

from __future__ import annotations

import pytest

from src.impact_analysis import (
    ImpactAnalysisResult,
    ImpactCategory,
    ImpactItem,
    ImpactPath,
    PathDirection,
    PathStep,
    ResolutionStatus,
    analyze_impact,
)
from src.impact_analysis.code_graph import build_caller_graph
from src.impact_analysis.paths import PathCollector, enumerate_chains
from src.impact_analysis.provider import GraphImpactProvider


class StubCallGraph:
    """Answers exactly the two read queries ``traverse_callers`` runs, from an edge list."""

    def __init__(self, edges: list[tuple[str, str]]):
        self.edges = edges  # (caller, callee)
        self.names = {n for e in edges for n in e}

    def execute_read(self, query, parameters=None):
        parameters = parameters or {}
        if "WHERE n.id = $id" in query:
            node = parameters["id"]
            return [{"id": node, "name": node, "label": "Function"}] if node in self.names else []
        if "b.id IN $frontier" in query:
            frontier = set(parameters["frontier"])
            return [
                {"source_id": a, "source_name": a, "source_label": "Function",
                 "target_id": b, "target_name": b, "target_label": "Function"}
                for a, b in self.edges if b in frontier
            ]
        raise AssertionError(f"unexpected query: {query}")


# --- build_caller_graph: depth, direction, cycles ----------------------------

def test_direct_and_indirect_callers_get_depths():
    graph = build_caller_graph(StubCallGraph([("A", "B"), ("B", "C")]), "C", max_depth=3)  # A -> B -> C
    assert graph.depth == {"C": 0, "B": 1, "A": 2}
    assert graph.predecessors == {"B": ["C"], "A": ["B"]}


def test_multiple_callers_are_all_found():
    graph = build_caller_graph(StubCallGraph([("A", "B"), ("C", "B")]), "B", max_depth=2)
    assert {n for n, d in graph.depth.items() if d == 1} == {"A", "C"}


def test_callees_are_not_reported_as_callers():
    graph = build_caller_graph(StubCallGraph([("A", "B"), ("B", "C")]), "B", max_depth=3)
    assert set(graph.depth) == {"B", "A"}  # C is called BY B, so it is not impacted via CALLS


def test_max_depth_is_respected():
    edges = [("A", "B"), ("B", "C"), ("C", "D")]
    graph = build_caller_graph(StubCallGraph(edges), "D", max_depth=2)
    assert set(graph.depth) == {"D", "C", "B"}
    assert "A" not in graph.depth


def test_zero_depth_means_changed_code_only():
    graph = build_caller_graph(StubCallGraph([("A", "B")]), "B", max_depth=0)
    assert graph.depth == {"B": 0}


def test_cycle_terminates_and_keeps_shortest_depths():
    # A -> B -> C -> A
    graph = build_caller_graph(StubCallGraph([("A", "B"), ("B", "C"), ("C", "A")]), "A", max_depth=10)
    assert graph.depth == {"A": 0, "C": 1, "B": 2}


def test_self_recursion_terminates():
    graph = build_caller_graph(StubCallGraph([("A", "A")]), "A", max_depth=5)
    assert graph.depth == {"A": 0}


def test_unknown_start_node_returns_none():
    assert build_caller_graph(StubCallGraph([("A", "B")]), "nope", max_depth=2) is None


def test_diamond_keeps_both_routes():
    # A -> B -> D and A -> C -> D ; change D
    graph = build_caller_graph(StubCallGraph([("B", "D"), ("C", "D"), ("A", "B"), ("A", "C")]), "D", max_depth=3)
    assert graph.depth["A"] == 2
    assert sorted(graph.predecessors["A"]) == ["B", "C"]


# --- path reconstruction -------------------------------------------------------

def test_enumerate_chains_diamond_returns_both_paths():
    preds = {"B": ["D"], "C": ["D"], "A": ["B", "C"]}
    chains, truncated = enumerate_chains("A", "D", preds, limit=10)
    assert sorted(chains) == [["D", "B", "A"], ["D", "C", "A"]]
    assert truncated is False


def test_enumerate_chains_respects_limit_and_reports_truncation():
    preds = {"B": ["D"], "C": ["D"], "A": ["B", "C"]}
    chains, truncated = enumerate_chains("A", "D", preds, limit=1)
    assert len(chains) == 1 and truncated is True


def test_enumerate_chains_start_node_is_a_single_step_chain():
    assert enumerate_chains("D", "D", {}, limit=5) == ([["D"]], False)


# --- path collector / rendering ------------------------------------------------

def _step(i, rel=None, direction=None):
    return PathStep(node_id=i, name=i, category=ImpactCategory.DIRECT_CALLER, relationship=rel, direction=direction)


def test_collector_keeps_separate_paths_per_terminal_and_dedupes():
    c = PathCollector(max_per_terminal=5)
    assert c.add("api", ImpactCategory.API, [_step("x"), _step("api", "EXPOSES", PathDirection.FORWARD)])
    assert c.add("api", ImpactCategory.API, [_step("y"), _step("api", "EXPOSES", PathDirection.FORWARD)])
    assert not c.add("api", ImpactCategory.API, [_step("x"), _step("api", "EXPOSES", PathDirection.FORWARD)])
    assert len(c.paths) == 2


def test_collector_caps_per_terminal_and_records_it():
    c = PathCollector(max_per_terminal=1)
    c.add("t", ImpactCategory.TEST, [_step("a"), _step("t")])
    assert not c.add("t", ImpactCategory.TEST, [_step("b"), _step("t")])
    assert (ImpactCategory.TEST, "t") in c.capped_terminals


def test_path_rendering_keeps_relationship_names_and_directions():
    path = ImpactPath(
        terminal_id="api",
        category=ImpactCategory.API,
        steps=[
            _step("validate_payment"),
            _step("process_payment", "CALLS", PathDirection.REVERSE),
            _step("POST /checkout", "EXPOSES", PathDirection.FORWARD),
        ],
    )
    assert path.chain == "validate_payment → process_payment → POST /checkout"
    assert path.detailed == "validate_payment <-[CALLS]- process_payment -[EXPOSES]-> POST /checkout"


# --- input handling ------------------------------------------------------------

def test_no_input_is_unresolved_without_touching_the_database():
    class Explodes:
        def execute_read(self, *a, **k):
            raise AssertionError("must not query when there is no input")

    result = analyze_impact(Explodes())
    assert result.resolution_status == ResolutionStatus.UNRESOLVED
    assert result.primary_change is None
    assert result.callers == [] and result.apis == []
    assert result.unresolved_items and result.unresolved_items[0].category == ImpactCategory.PRIMARY_CHANGE


def test_unresolved_code_location_is_unresolved():
    from src.rca.models import CodeLocation

    result = analyze_impact(object(), location=CodeLocation(resolution_status=ResolutionStatus.UNRESOLVED, reason="no file"))
    assert result.resolution_status == ResolutionStatus.UNRESOLVED


def test_class_level_location_is_rejected_with_a_reason():
    from src.rca.models import CodeLocation

    loc = CodeLocation(resolution_status=ResolutionStatus.RESOLVED, node_id="c1", node_label="Class", qualified_name="Foo")
    result = analyze_impact(object(), location=loc)
    assert result.resolution_status == ResolutionStatus.UNRESOLVED
    assert "class-level" in result.unresolved_items[0].reason


# --- result helpers and Person 2 adapter mapping ---------------------------------

def _item(id, category, depth, **kw):
    return ImpactItem(id=id, name=kw.pop("name", id), category=category, node_label=kw.pop("node_label", "Method"),
                      depth=depth, **kw)


def test_result_helpers_split_direct_and_indirect_callers():
    r = ImpactAnalysisResult(
        resolution_status=ResolutionStatus.RESOLVED, max_depth=3,
        callers=[_item("a", ImpactCategory.DIRECT_CALLER, 1), _item("b", ImpactCategory.INDIRECT_CALLER, 2)],
        impact_paths=[ImpactPath("a", ImpactCategory.DIRECT_CALLER, [])],
    )
    assert [c.id for c in r.direct_callers] == ["a"]
    assert [c.id for c in r.indirect_callers] == ["b"]
    assert len(r.paths_to("a")) == 1 and r.paths_to("zzz") == []


def test_adapter_maps_items_to_the_impact_provider_contract_fields():
    r = ImpactAnalysisResult(
        resolution_status=ResolutionStatus.RESOLVED, max_depth=3,
        callers=[_item("m1", ImpactCategory.DIRECT_CALLER, 1, name="PaymentService.process_payment",
                       qualified_name="PaymentService.process_payment", file="payment/service.py",
                       relationship="CALLS")],
        tests=[_item("t1", ImpactCategory.TEST, 1, name="test_validate_payment", node_label="Test",
                     relationship="VALIDATES")],
        apis=[_item("api1", ImpactCategory.API, 3, name="POST /checkout", node_label="API", relationship="EXPOSES")],
    )
    fields = {f["id"]: f for f in GraphImpactProvider.to_node_fields(r)}
    assert fields["m1"] == {"id": "m1", "kind": "method", "name": "PaymentService.process_payment",
                            "location": "payment/service.py::PaymentService.process_payment",
                            "depth": 1, "relation": "calls"}
    assert fields["t1"]["kind"] == "test" and fields["t1"]["relation"] == "tests"
    assert fields["api1"]["kind"] == "api" and fields["api1"]["relation"] == "exposes"
    assert fields["api1"]["location"] is None


def test_adapter_builds_the_contract_objects_when_person2_module_is_present():
    pytest.importorskip("src.reasoning.impact")  # lives on Person 2's branch until it is merged
    from src.reasoning.impact import ImpactNode

    node = ImpactNode(**GraphImpactProvider.to_node_fields(
        ImpactAnalysisResult(resolution_status=ResolutionStatus.RESOLVED, max_depth=1,
                             callers=[_item("m1", ImpactCategory.DIRECT_CALLER, 1)]))[0])
    assert node.id == "m1"
