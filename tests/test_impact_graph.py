"""Neo4j-backed tests for Phase 5 impact analysis (skip cleanly without a
reachable Neo4j, per conftest.py's `neo4j_connection`). Graphs are built with
`tests/_impact_graph.py`."""

from __future__ import annotations

import pytest

from src.impact_analysis import (
    Directness,
    ImpactCategory,
    KnowledgeStatus,
    PathDirection,
    ResolutionStatus,
    analyze_impact,
)

from ._impact_graph import Fixture, checkout_fixture, names

VALIDATE = {"repository": "payments-service", "file_path": "payment/validator.py", "method": "validate_payment"}


# --- callers ---------------------------------------------------------------------

def test_direct_caller(neo4j_connection):
    fx = Fixture()
    a = fx.function("a", "m.py"), fx.function("b", "m.py")
    fx.calls(a[0], a[1])  # a CALLS b
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="b")

    assert result.primary_change.name == "b" and result.primary_change.category == ImpactCategory.PRIMARY_CHANGE
    assert [(c.name, c.depth, c.category) for c in result.callers] == [("a", 1, ImpactCategory.DIRECT_CALLER)]


def test_multiple_callers(neo4j_connection):
    fx = Fixture()
    a, b, c = fx.function("a", "m.py"), fx.function("b", "m.py"), fx.function("c", "m.py")
    fx.calls(a, b)
    fx.calls(c, b)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="b")

    assert sorted(names(result.callers)) == ["a", "c"]
    assert all(c.category == ImpactCategory.DIRECT_CALLER for c in result.callers)


def test_indirect_callers_have_increasing_depth(neo4j_connection):
    fx = Fixture()
    a, b, c = fx.function("a", "m.py"), fx.function("b", "m.py"), fx.function("c", "m.py")
    fx.calls(a, b)
    fx.calls(b, c)  # a -> b -> c ; change c
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="c")

    assert [(i.name, i.depth, i.category) for i in result.callers] == [
        ("b", 1, ImpactCategory.DIRECT_CALLER),
        ("a", 2, ImpactCategory.INDIRECT_CALLER),
    ]
    assert [i.name for i in result.direct_callers] == ["b"]
    assert [i.name for i in result.indirect_callers] == ["a"]


def test_max_depth_is_respected_and_truncation_is_reported(neo4j_connection):
    fx = Fixture()
    fns = [fx.function(f"f{i}", "m.py") for i in range(5)]
    for caller, callee in zip(fns, fns[1:]):
        fx.calls(caller, callee)  # f0 -> f1 -> f2 -> f3 -> f4 ; change f4
    fx.ingest(neo4j_connection)

    shallow = analyze_impact(neo4j_connection, method="f4", max_depth=2)
    deep = analyze_impact(neo4j_connection, method="f4", max_depth=5)

    assert names(shallow.callers) == ["f3", "f2"]
    assert any("beyond max_depth=2" in u.reason for u in shallow.unresolved_items)
    assert names(deep.callers) == ["f3", "f2", "f1", "f0"]
    assert not any("beyond max_depth" in u.reason for u in deep.unresolved_items)


def test_cycle_terminates(neo4j_connection):
    fx = Fixture()
    a, b, c = fx.function("a", "m.py"), fx.function("b", "m.py"), fx.function("c", "m.py")
    fx.calls(a, b)
    fx.calls(b, c)
    fx.calls(c, a)  # A -> B -> C -> A
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="a", max_depth=10)

    assert [(i.name, i.depth) for i in result.callers] == [("c", 1), ("b", 2)]  # "a" is not its own caller


def test_changed_code_only_when_max_depth_is_zero(neo4j_connection):
    fx = Fixture()
    a, b = fx.function("a", "m.py"), fx.function("b", "m.py")
    fx.calls(a, b)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="b", max_depth=0)

    assert result.callers == []
    assert any("beyond max_depth=0" in u.reason for u in result.unresolved_items)


# --- classes and files -------------------------------------------------------------

def test_classes_and_files_of_callers(neo4j_connection):
    checkout_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert sorted(names(result.classes)) == ["CheckoutController", "PaymentService"]  # not the changed code's own class
    assert sorted(names(result.files)) == ["controllers/checkout_controller.py", "payment/service.py"]


def test_class_is_not_duplicated_when_two_methods_share_it(neo4j_connection):
    fx = Fixture()
    fx.klass("Svc", "svc.py")
    fx.klass("Target", "target.py")
    t = fx.method("Target", "run")
    m1, m2 = fx.method("Svc", "one"), fx.method("Svc", "two")
    fx.calls(m1, t)
    fx.calls(m2, t)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Target.run")

    assert names(result.classes) == ["Svc"]
    assert names(result.files) == ["svc.py"]
    assert len(result.callers) == 2


def test_top_level_function_has_no_class(neo4j_connection):
    fx = Fixture()
    fx.klass("Svc", "svc.py")
    caller = fx.method("Svc", "go")
    target = fx.function("helper", "util.py")
    fx.calls(caller, target)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="helper")

    assert result.primary_change.class_name is None
    assert result.primary_change.node_label == "Function"
    assert result.callers[0].class_name == "Svc" and result.callers[0].qualified_name == "Svc.go"


# --- APIs, events, databases, tables --------------------------------------------------

def test_api_event_database_table_from_the_spec_graph(neo4j_connection):
    checkout_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert [(a.name, a.depth, a.relationship) for a in result.apis] == [("POST /checkout", 3, "EXPOSES")]
    assert [(e.name, e.relationship) for e in result.events] == [("PaymentProcessed", "PUBLISHES")]
    assert names(result.databases) == ["PaymentsDatabase"]
    assert names(result.tables) == ["payments"]
    assert result.tables[0].depth == result.databases[0].depth + 1


def test_event_consumer_relationship_is_included(neo4j_connection):
    fx = Fixture()
    fx.klass("Consumer", "consumer.py")
    fx.klass("Target", "target.py")
    handler = fx.method("Consumer", "handle")
    target = fx.method("Target", "run")
    fx.calls(handler, target)
    fx.link(handler, "CONSUMES", fx.event("PaymentProcessed"))
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Target.run")

    assert [(e.name, e.relationship) for e in result.events] == [("PaymentProcessed", "CONSUMES")]


def test_relationships_in_the_wrong_direction_are_not_followed(neo4j_connection):
    fx = Fixture()
    fx.klass("Target", "target.py")
    target = fx.method("Target", "run")
    api = fx.api("POST /x")
    fx.link(api, "EXPOSES", target)  # API -> code is the wrong way round
    fx.ingest(neo4j_connection)

    assert analyze_impact(neo4j_connection, method="Target.run").apis == []


def test_mismatched_relationship_and_label_is_not_claimed(neo4j_connection):
    fx = Fixture()
    fx.klass("Target", "target.py")
    target = fx.method("Target", "run")
    fx.link(target, "EXPOSES", fx.event("NotAnApi"))  # EXPOSES must point at an API
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Target.run")

    assert result.apis == [] and result.events == []


# --- services and downstream services ----------------------------------------------------

def _service_fixture() -> Fixture:
    fx = checkout_fixture()
    payments, fraud, notify = fx.service("payments-svc"), fx.service("fraud-svc"), fx.service("notification-svc")
    fx.link(payments, "CONTAINS", fx.repo)
    fx.link(payments, "DEPENDS_ON", fraud)
    fx.link(fraud, "CALLS", notify)
    fx.link(notify, "DEPENDS_ON", payments)  # a cycle back to the owning service
    return fx


def test_owning_service_and_downstream_services_with_directness(neo4j_connection):
    _service_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert names(result.services) == ["payments-svc"]
    assert [(s.name, s.directness) for s in result.downstream_services] == [
        ("fraud-svc", Directness.DIRECT),
        ("notification-svc", Directness.INDIRECT),
    ]
    assert result.category_status[ImpactCategory.SERVICE] == KnowledgeStatus.KNOWN
    assert result.category_status[ImpactCategory.DOWNSTREAM_SERVICE] == KnowledgeStatus.KNOWN


def test_service_cycle_terminates_and_owner_is_not_reported_downstream(neo4j_connection):
    _service_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE, max_downstream_depth=10)

    assert "payments-svc" not in names(result.downstream_services)
    assert len(result.downstream_services) == 2


def test_downstream_depth_is_respected(neo4j_connection):
    _service_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE, max_downstream_depth=1)

    assert names(result.downstream_services) == ["fraud-svc"]


# --- tests ----------------------------------------------------------------------------------

def test_graph_validated_test_is_returned(neo4j_connection):
    checkout_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert [(t.name, t.file, t.relationship) for t in result.tests] == [
        ("test_validate_payment", "tests/test_validator.py", "VALIDATES")
    ]


def test_test_function_that_calls_impacted_code_is_a_test_not_a_caller(neo4j_connection):
    fx = Fixture()
    fx.klass("Svc", "svc.py")
    target = fx.method("Svc", "run")
    test_fn = fx.function("test_something", "tests/test_svc.py")
    fx.calls(test_fn, target)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Svc.run")

    assert result.callers == []
    assert [(t.name, t.relationship, t.category) for t in result.tests] == [
        ("test_something", "CALLS", ImpactCategory.TEST)
    ]


def test_naming_convention_test_is_marked_as_such(neo4j_connection):
    fx = Fixture()
    fx.klass("Svc", "svc.py")
    fx.method("Svc", "refund")
    fx.function("test_refund_flow", "tests/test_svc.py")  # no edge to Svc.refund
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Svc.refund")

    assert [(t.name, t.relationship) for t in result.tests] == [("test_refund_flow", "NAMING_CONVENTION")]
    path = result.paths_to(result.tests[0].id)[0]
    assert path.steps[-1].relationship == "NAMING_CONVENTION" and path.steps[-1].direction is None


def test_no_tests_means_empty_never_invented(neo4j_connection):
    fx = Fixture()
    fx.klass("Svc", "svc.py")
    fx.method("Svc", "refund")
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Svc.refund")

    assert result.tests == []
    assert result.category_status[ImpactCategory.TEST] == KnowledgeStatus.UNRESOLVED


# --- impact paths ------------------------------------------------------------------------------

def test_exact_path_to_the_api_is_preserved(neo4j_connection):
    checkout_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    api_path = result.paths_to(result.apis[0].id)[0]
    assert api_path.chain == (
        "PaymentValidator.validate_payment → PaymentService.process_payment → "
        "CheckoutController.checkout → POST /checkout"
    )
    assert [s.relationship for s in api_path.steps] == [None, "CALLS", "CALLS", "EXPOSES"]
    assert [s.direction for s in api_path.steps] == [None, PathDirection.REVERSE, PathDirection.REVERSE, PathDirection.FORWARD]
    assert api_path.detailed == (
        "PaymentValidator.validate_payment <-[CALLS]- PaymentService.process_payment "
        "<-[CALLS]- CheckoutController.checkout -[EXPOSES]-> POST /checkout"
    )


def test_event_path_goes_through_the_publishing_class(neo4j_connection):
    checkout_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    event_path = result.paths_to(result.events[0].id)[0]
    assert event_path.chain == (
        "PaymentValidator.validate_payment → PaymentService.process_payment → PaymentService → PaymentProcessed"
    )


def test_multiple_entry_points_keep_separate_paths(neo4j_connection):
    fx = checkout_fixture()
    fx.klass("RetryController", "controllers/retry_controller.py")
    retry = fx.method("RetryController", "retry")
    fx.calls(retry, "Method:PaymentService.process_payment")
    fx.link(retry, "EXPOSES", fx.api("POST /retry"))
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert sorted(names(result.apis)) == ["POST /checkout", "POST /retry"]
    chains = {a.name: result.paths_to(a.id)[0].chain for a in result.apis}
    assert "CheckoutController.checkout" in chains["POST /checkout"] and "RetryController" not in chains["POST /checkout"]
    assert "RetryController.retry" in chains["POST /retry"] and "CheckoutController" not in chains["POST /retry"]


def test_one_api_reached_through_two_callers_keeps_both_paths(neo4j_connection):
    fx = Fixture()
    fx.klass("Target", "target.py")
    fx.klass("C1", "c1.py")
    fx.klass("C2", "c2.py")
    target, m1, m2 = fx.method("Target", "run"), fx.method("C1", "go"), fx.method("C2", "go")
    fx.calls(m1, target)
    fx.calls(m2, target)
    api = fx.api("GET /shared")
    fx.link(m1, "EXPOSES", api)
    fx.link(m2, "EXPOSES", api)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Target.run")

    assert len(result.apis) == 1
    assert sorted(p.chain for p in result.paths_to(result.apis[0].id)) == [
        "Target.run → C1.go → GET /shared",
        "Target.run → C2.go → GET /shared",
    ]


def test_diamond_keeps_both_routes_to_the_same_caller(neo4j_connection):
    fx = Fixture()
    a, b, c, d = (fx.function(n, "m.py") for n in "abcd")
    fx.calls(b, d)
    fx.calls(c, d)
    fx.calls(a, b)
    fx.calls(a, c)  # a -> b -> d and a -> c -> d ; change d
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="d")

    a_item = next(i for i in result.callers if i.name == "a")
    assert sorted(p.chain for p in result.paths_to(a_item.id)) == ["d → b → a", "d → c → a"]


# --- unresolved -----------------------------------------------------------------------------------

def test_graph_without_architecture_data_reports_unresolved_not_empty(neo4j_connection):
    fx = Fixture()
    a, b = fx.function("a", "m.py"), fx.function("b", "m.py")
    fx.calls(a, b)
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="b")

    assert result.services == result.apis == result.events == result.databases == result.tables == []
    assert result.downstream_services == []
    for category in (ImpactCategory.SERVICE, ImpactCategory.API, ImpactCategory.EVENT, ImpactCategory.DATABASE,
                     ImpactCategory.TABLE, ImpactCategory.DOWNSTREAM_SERVICE):
        assert result.category_status[category] == KnowledgeStatus.UNRESOLVED
        assert any(u.category == category for u in result.unresolved_items)
    assert result.category_status[ImpactCategory.DIRECT_CALLER] == KnowledgeStatus.KNOWN
    assert result.resolution_status == ResolutionStatus.PARTIALLY_RESOLVED


def test_architecture_present_but_unconnected_is_known_and_empty(neo4j_connection):
    fx = checkout_fixture()
    fx.api("GET /unrelated")  # exists in the graph, but nothing links it to the changed code
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert "GET /unrelated" not in names(result.apis)
    assert result.category_status[ImpactCategory.API] == KnowledgeStatus.KNOWN


def test_unresolved_call_sites_are_reported_but_not_counted_as_impact(neo4j_connection):
    fx = Fixture()
    fx.klass("Svc", "svc.py")
    fx.klass("Other", "other.py")
    target = fx.method("Svc", "validate_payment")
    fx.method("Other", "go", unresolved_calls=["self.validator.validate_payment"])
    fx.ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, method="Svc.validate_payment")

    assert result.callers == []
    assert any("Other.go" in u.reason and "unresolved call" in u.reason for u in result.unresolved_items)


def test_unknown_method_is_unresolved(neo4j_connection):
    checkout_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, repository="payments-service", method="does_not_exist")

    assert result.resolution_status == ResolutionStatus.UNRESOLVED
    assert result.primary_change is None and result.callers == []
    assert result.unresolved_items[0].category == ImpactCategory.PRIMARY_CHANGE


def test_ambiguous_method_name_is_unresolved_not_guessed(neo4j_connection):
    fx = Fixture()
    fx.klass("A", "a.py")
    fx.klass("B", "b.py")
    fx.method("A", "run")
    fx.method("B", "run")
    fx.ingest(neo4j_connection)

    ambiguous = analyze_impact(neo4j_connection, method="run")
    pinned = analyze_impact(neo4j_connection, method="run", file_path="a.py")
    qualified = analyze_impact(neo4j_connection, method="B.run")

    assert ambiguous.resolution_status == ResolutionStatus.UNRESOLVED
    assert pinned.primary_change.qualified_name == "A.run"
    assert qualified.primary_change.qualified_name == "B.run"


# --- end to end (the spec's example) ------------------------------------------------------------------

def test_end_to_end_spec_example(neo4j_connection):
    _service_fixture().ingest(neo4j_connection)

    result = analyze_impact(neo4j_connection, **VALIDATE)

    assert result.primary_change.qualified_name == "PaymentValidator.validate_payment"
    assert result.primary_change.repository == "payments-service"
    assert result.primary_change.file == "payment/validator.py"
    assert result.primary_change.class_name == "PaymentValidator" and result.primary_change.depth == 0
    assert names(result.direct_callers) == ["PaymentService.process_payment"]
    assert names(result.indirect_callers) == ["CheckoutController.checkout"]
    assert names(result.apis) == ["POST /checkout"]
    assert names(result.events) == ["PaymentProcessed"]
    assert names(result.databases) == ["PaymentsDatabase"]
    assert names(result.tables) == ["payments"]
    assert names(result.services) == ["payments-svc"]
    assert names(result.tests) == ["test_validate_payment"]
    assert result.paths_to(result.apis[0].id)[0].chain.endswith("CheckoutController.checkout → POST /checkout")
    assert result.resolution_status == ResolutionStatus.RESOLVED  # nothing left unresolved in this graph
    assert result.unresolved_items == []


# --- real Phase 1/2/3/4 pipeline ---------------------------------------------------------------------------

def test_real_ingested_repository_and_phase4_localization_input(tmp_path, neo4j_connection):
    from src.fix_localization import localize_fix
    from src.graph import ingest_repository
    from src.rca.investigator import investigate
    from src.rca.models import RCAInput

    from ._sample_repo import build_sample_repository

    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    # simple input: validate_payment is called by PaymentService.process (a real, resolved CALLS edge)
    by_name = analyze_impact(neo4j_connection, repository=repo.info.name, file_path="payment/validator.py",
                             method="validate_payment")
    assert names(by_name.direct_callers) == ["PaymentService.process"]
    assert names(by_name.classes) == ["PaymentService"]
    assert names(by_name.files) == ["payment/service.py"]
    # the mapper creates no Service/API/Event/Database nodes, so these must be UNRESOLVED, not invented
    assert by_name.services == [] and by_name.apis == []
    assert by_name.category_status[ImpactCategory.SERVICE] == KnowledgeStatus.UNRESOLVED

    # Phase 4 result used directly
    traceback = (
        'Traceback (most recent call last):\n'
        '  File "payment/service.py", line 7, in process\n'
        '    validate_payment()\n'
        'ValueError: boom\n'
    )
    rca = investigate(RCAInput(stack_trace=traceback, repository=repo.info.name),
                      connection=neo4j_connection, repo_root=tmp_path)
    localization = localize_fix(rca, connection=neo4j_connection, repo_root=tmp_path)
    from_localization = analyze_impact(neo4j_connection, localization=localization)

    assert from_localization.primary_change.qualified_name == "PaymentService.process"
    assert [(t.name, t.relationship) for t in from_localization.tests] == [("test_process", "NAMING_CONVENTION")]
