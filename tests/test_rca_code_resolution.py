"""Tests for `src.rca.code_resolution` -- Exception -> Code Location, and
`src.rca.code_context` -- the deterministic context gathered once a location
resolves. Both need a real, reachable Neo4j (see `neo4j_connection` in
conftest.py -- they skip cleanly when one isn't configured/running)."""

from __future__ import annotations

from src.graph import ingest_repository
from src.rca.code_context import build_code_context
from src.rca.code_resolution import resolve_stack_frame
from src.rca.models import ResolutionStatus, StackFrame

from ._sample_repo import build_sample_repository


def test_resolves_a_method_call_to_its_real_graph_node(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    frame = StackFrame(file="payment/service.py", line=7, function="process")
    location = resolve_stack_frame(neo4j_connection, repo.info.name, frame)

    assert location.resolution_status == ResolutionStatus.RESOLVED
    assert location.node_label == "Method"
    assert location.qualified_name == "PaymentService.process"
    assert location.file_path == "payment/service.py"


def test_resolves_a_top_level_function_call(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    frame = StackFrame(file="payment/validator.py", line=1, function="validate_payment")
    location = resolve_stack_frame(neo4j_connection, repo.info.name, frame)

    assert location.resolution_status == ResolutionStatus.RESOLVED
    assert location.node_label == "Function"
    assert location.qualified_name == "validate_payment"


def test_missing_file_is_unresolved_not_guessed(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    frame = StackFrame(file="payment/does_not_exist.py", line=1, function="whatever")
    location = resolve_stack_frame(neo4j_connection, repo.info.name, frame)

    assert location.resolution_status == ResolutionStatus.UNRESOLVED
    assert location.node_id is None
    assert location.reason


def test_missing_repository_is_unresolved(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    frame = StackFrame(file="payment/service.py", line=7, function="process")
    location = resolve_stack_frame(neo4j_connection, "some-other-repository", frame)

    assert location.resolution_status == ResolutionStatus.UNRESOLVED


def test_line_with_no_enclosing_definition_is_partially_resolved(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    # Line 1 of service.py is the top-level import statement -- resolves the
    # file, but there's no Class/Method/Function definition at or before it.
    frame = StackFrame(file="payment/service.py", line=1, function="<module>")
    location = resolve_stack_frame(neo4j_connection, repo.info.name, frame)

    assert location.resolution_status == ResolutionStatus.PARTIALLY_RESOLVED
    assert location.file_path == "payment/service.py"


def test_code_context_reports_containing_function_and_source_window(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    frame = StackFrame(file="payment/service.py", line=7, function="process")
    location = resolve_stack_frame(neo4j_connection, repo.info.name, frame)

    context = build_code_context(tmp_path, location)

    assert context.resolution_status == ResolutionStatus.RESOLVED
    assert context.containing_function == "process"
    assert context.containing_class == "PaymentService"
    assert any("validate_payment" in line for line in context.source_lines)


def test_code_context_unresolved_when_location_unresolved(tmp_path):
    from src.rca.models import CodeLocation

    location = CodeLocation(resolution_status=ResolutionStatus.UNRESOLVED)
    context = build_code_context(tmp_path, location)

    assert context.resolution_status == ResolutionStatus.UNRESOLVED
    assert context.source_lines == []
