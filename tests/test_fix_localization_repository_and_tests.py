"""Tests for `src.fix_localization.repository_identity` and `.related_tests`.
Needs a real, reachable Neo4j (skips cleanly otherwise)."""

from __future__ import annotations

from src.fix_localization.related_tests import find_related_tests
from src.fix_localization.repository_identity import get_repository_identity
from src.fix_localization.models import ResolutionStatus
from src.graph import ingest_repository

from ._sample_repo import build_sample_repository


def test_resolves_repository_identity_by_name(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    identity = get_repository_identity(neo4j_connection, repo.info.name)

    assert identity.resolution_status == ResolutionStatus.RESOLVED
    assert identity.name == repo.info.name
    assert identity.repository_id is not None
    assert identity.root_path == repo.info.root_path


def test_repository_identity_unresolved_for_unknown_name(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    identity = get_repository_identity(neo4j_connection, "no-such-repository")

    assert identity.resolution_status == ResolutionStatus.UNRESOLVED


def test_repository_identity_unresolved_when_nothing_given(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    identity = get_repository_identity(neo4j_connection, None)

    assert identity.resolution_status == ResolutionStatus.UNRESOLVED


def test_finds_the_existing_test_that_exercises_the_target_method(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    repo_id = neo4j_connection.execute_read("MATCH (r:Repository) RETURN r.id AS id")[0]["id"]

    # tests/test_service.py defines test_process(), which the sample repo's
    # fixture builder wrote specifically to exercise PaymentService.process().
    tests = find_related_tests(neo4j_connection, repo_id, "process")

    assert any(t.test_file == "tests/test_service.py" and t.test_function == "test_process" for t in tests)


def test_no_related_tests_for_a_name_nothing_references(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)
    repo_id = neo4j_connection.execute_read("MATCH (r:Repository) RETURN r.id AS id")[0]["id"]

    tests = find_related_tests(neo4j_connection, repo_id, "totally_unrelated_name")

    assert tests == []


def test_related_tests_empty_without_repository_or_target(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)
    repo_id = neo4j_connection.execute_read("MATCH (r:Repository) RETURN r.id AS id")[0]["id"]

    assert find_related_tests(neo4j_connection, None, "process") == []
    assert find_related_tests(neo4j_connection, repo_id, None) == []
