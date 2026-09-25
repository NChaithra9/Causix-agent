"""Tests for `src.graph.mapper` -- Phase 1 facts -> plain graph records.

Pure-logic tests: no Neo4j involved anywhere here (the mapper never talks
to a database -- see `src.graph.ingestion` for that). Uses the sample
repository from the Phase 2 spec's own end-to-end example.
"""

from __future__ import annotations

from src.graph.mapper import map_repository_to_graph
from src.graph.schema import NodeLabel
from src.parser.models import RelationshipType

from ._sample_repo import build_sample_repository


def _nodes_by_label(batch, label):
    return [n for n in batch.nodes if n.label == label]


def _find_node(batch, label, name_property="name", name=None):
    for n in batch.nodes:
        if n.label == label and n.properties.get(name_property) == name:
            return n
    return None


def test_repository_and_file_contains_relationships(tmp_path):
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    repository_node = _nodes_by_label(batch, NodeLabel.REPOSITORY)[0]
    file_nodes = {n.properties["path"]: n for n in _nodes_by_label(batch, NodeLabel.FILE)}

    assert set(file_nodes) == {
        "payment/__init__.py",
        "payment/service.py",
        "payment/validator.py",
        "tests/test_service.py",
    }

    contains_repo_to_file = {
        (r.start_id, r.end_id)
        for r in batch.relationships
        if r.rel_type == RelationshipType.CONTAINS.value and r.start_label == NodeLabel.REPOSITORY
    }
    for file_node in file_nodes.values():
        assert (repository_node.id, file_node.id) in contains_repo_to_file


def test_file_contains_class_and_class_contains_method(tmp_path):
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    service_file = _find_node(batch, NodeLabel.FILE, "path", "payment/service.py")
    payment_class = _find_node(batch, NodeLabel.CLASS, "name", "PaymentService")
    process_method = _find_node(batch, NodeLabel.METHOD, "name", "process")

    assert payment_class is not None and payment_class.properties["file_id"] == service_file.id
    assert process_method is not None and process_method.properties["class_id"] == payment_class.id

    assert any(
        r.rel_type == RelationshipType.CONTAINS.value
        and r.start_id == service_file.id
        and r.end_id == payment_class.id
        for r in batch.relationships
    )
    assert any(
        r.rel_type == RelationshipType.CONTAINS.value
        and r.start_id == payment_class.id
        and r.end_id == process_method.id
        for r in batch.relationships
    )


def test_file_contains_function(tmp_path):
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    validator_file = _find_node(batch, NodeLabel.FILE, "path", "payment/validator.py")
    validate_function = _find_node(batch, NodeLabel.FUNCTION, "name", "validate_payment")

    assert validate_function.properties["file_id"] == validator_file.id
    assert any(
        r.rel_type == RelationshipType.CONTAINS.value
        and r.start_id == validator_file.id
        and r.end_id == validate_function.id
        for r in batch.relationships
    )


def test_method_calls_function_across_files(tmp_path):
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    process_method = _find_node(batch, NodeLabel.METHOD, "name", "process")
    validate_function = _find_node(batch, NodeLabel.FUNCTION, "name", "validate_payment")

    assert any(
        r.rel_type == RelationshipType.CALLS.value
        and r.start_id == process_method.id
        and r.end_id == validate_function.id
        for r in batch.relationships
    )


def test_file_imports_file(tmp_path):
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    service_file = _find_node(batch, NodeLabel.FILE, "path", "payment/service.py")
    validator_file = _find_node(batch, NodeLabel.FILE, "path", "payment/validator.py")

    assert any(
        r.rel_type == RelationshipType.IMPORTS.value
        and r.start_id == service_file.id
        and r.end_id == validator_file.id
        for r in batch.relationships
    )


def test_commit_modifies_file(tmp_path):
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    commit_nodes = _nodes_by_label(batch, NodeLabel.COMMIT)
    assert len(commit_nodes) == 2  # "Add payment service" + "Add service test"

    validator_file = _find_node(batch, NodeLabel.FILE, "path", "payment/validator.py")
    modifies_targets = {
        r.end_id for r in batch.relationships if r.rel_type == RelationshipType.MODIFIES.value
    }
    assert validator_file.id in modifies_targets


def test_unresolved_call_creates_no_relationship_but_is_recorded_on_the_node(tmp_path):
    """`tests/test_service.py` calls `PaymentService().process()` -- the `.process()`
    part is an unresolved call through a constructed instance, so it must
    produce no CALLS edge, but the raw call should still show up on the
    caller's `unresolved_calls` property."""
    repo = build_sample_repository(tmp_path)
    batch = map_repository_to_graph(repo)

    test_function = _find_node(batch, NodeLabel.FUNCTION, "name", "test_process")

    calls_from_test_function = [
        r for r in batch.relationships if r.rel_type == RelationshipType.CALLS.value and r.start_id == test_function.id
    ]
    # Only the resolved call (to the PaymentService class, via the import)
    # becomes an edge -- never one for the unresolved `.process()` part.
    assert len(calls_from_test_function) == 1

    assert test_function.properties["unresolved_calls"] == ["PaymentService().process()"]


def test_mapping_the_same_repository_twice_is_idempotent(tmp_path):
    """Running the mapper twice on the same Phase 1 output must produce identical
    node ids and identical relationships -- this is what makes Neo4j `MERGE`
    (in src.graph.ingestion) avoid creating duplicates on a second ingestion run."""
    repo = build_sample_repository(tmp_path)

    batch1 = map_repository_to_graph(repo)
    batch2 = map_repository_to_graph(repo)

    ids1 = sorted((n.label.value, n.id) for n in batch1.nodes)
    ids2 = sorted((n.label.value, n.id) for n in batch2.nodes)
    assert ids1 == ids2

    rels1 = sorted(
        (r.start_label.value, r.start_id, r.rel_type, r.end_label.value, r.end_id) for r in batch1.relationships
    )
    rels2 = sorted(
        (r.start_label.value, r.start_id, r.rel_type, r.end_label.value, r.end_id) for r in batch2.relationships
    )
    assert rels1 == rels2


def test_missing_git_history_does_not_crash_mapping(tmp_path):
    """A repository with no commits yet (or git history unavailable) must still map cleanly."""
    from src.repository import GitHistory, Repository
    from src.repo_ingestion.models import RepositoryInfo

    bare_repo = Repository(
        info=RepositoryInfo(root_path=str(tmp_path), name="empty", is_git_repository=False),
        source_files=[],
        files=[],
        relationships=[],
        git_history=GitHistory(commits=[], file_changes=[]),
    )

    batch = map_repository_to_graph(bare_repo)

    assert len(batch.nodes) == 1  # just the Repository node
    assert batch.relationships == []
