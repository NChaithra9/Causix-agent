"""Tests for `src.graph.ingestion` -- MERGE-based Neo4j ingestion.

All of these need a real, reachable Neo4j (see `neo4j_connection` in
conftest.py -- they skip cleanly when one isn't configured/running via
`docker compose up -d`). This is the layer where Phase 2's core promise
gets proven: running the same ingestion twice does not create duplicates.
"""

from __future__ import annotations

from src.graph import ensure_constraints, ingest_repository
from src.graph.queries import class_methods, commit_modified_files, method_call_relationships, repository_files

from ._sample_repo import build_sample_repository


def _node_count(connection) -> int:
    return connection.execute_read("MATCH (n) RETURN count(n) AS count")[0]["count"]


def _relationship_count(connection) -> int:
    return connection.execute_read("MATCH ()-[r]->() RETURN count(r) AS count")[0]["count"]


def test_ensure_constraints_is_idempotent(neo4j_connection):
    ensure_constraints(neo4j_connection)
    ensure_constraints(neo4j_connection)  # must not raise the second time

    constraints = neo4j_connection.execute_read("SHOW CONSTRAINTS YIELD name RETURN name")
    names = {row["name"] for row in constraints}
    assert "repository_id_unique" in names
    assert "commit_id_unique" in names


def test_node_creation_for_every_populated_label(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)

    ingest_repository(neo4j_connection, repo)

    counts = neo4j_connection.execute_read(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label"
    )
    counts_by_label = {row["label"]: row["count"] for row in counts}

    assert counts_by_label["Repository"] == 1
    assert counts_by_label["File"] == 4
    assert counts_by_label["Class"] == 1
    assert counts_by_label["Method"] == 1
    assert counts_by_label["Function"] == 2
    assert counts_by_label["Commit"] == 2


def test_relationships_are_created(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    files = repository_files(neo4j_connection)
    assert {row["file_path"] for row in files} == {
        "payment/__init__.py",
        "payment/service.py",
        "payment/validator.py",
        "tests/test_service.py",
    }

    methods = class_methods(neo4j_connection)
    assert [(row["class_name"], row["method_name"]) for row in methods] == [("PaymentService", "process")]

    calls = method_call_relationships(neo4j_connection)
    assert any(row["source_name"] == "process" and row["target_name"] == "validate_payment" for row in calls)

    modifies = commit_modified_files(neo4j_connection)
    assert any(row["file_path"] == "payment/validator.py" for row in modifies)


def test_running_ingestion_twice_does_not_create_duplicates(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)

    ingest_repository(neo4j_connection, repo)
    nodes_after_first_run = _node_count(neo4j_connection)
    relationships_after_first_run = _relationship_count(neo4j_connection)

    ingest_repository(neo4j_connection, repo)
    nodes_after_second_run = _node_count(neo4j_connection)
    relationships_after_second_run = _relationship_count(neo4j_connection)

    assert nodes_after_second_run == nodes_after_first_run
    assert relationships_after_second_run == relationships_after_first_run


def test_same_source_entity_maps_to_the_same_node_across_runs(tmp_path, neo4j_connection):
    """Re-parsing and re-ingesting the same repository from scratch (as if this
    were a second, independent ingestion run days later) must resolve to the
    exact same PaymentService node, not a new one."""
    repo_first_pass = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo_first_pass)

    from src.repository import build_repository_facts

    repo_second_pass = build_repository_facts(tmp_path)  # re-run Phase 1 from scratch, no new commits
    ingest_repository(neo4j_connection, repo_second_pass)

    rows = neo4j_connection.execute_read("MATCH (c:Class {name: 'PaymentService'}) RETURN count(c) AS count")
    assert rows[0]["count"] == 1


def test_missing_git_history_does_not_crash_ingestion(tmp_path, neo4j_connection):
    """A repository with an empty relationships/git_history (e.g. not a Git repo
    at all) must still ingest cleanly -- just with fewer nodes/relationships."""
    from src.repository import GitHistory, Repository
    from src.repo_ingestion.models import RepositoryInfo

    bare_repo = Repository(
        info=RepositoryInfo(root_path=str(tmp_path), name="no-git-history", is_git_repository=False),
        source_files=[],
        files=[],
        relationships=[],
        git_history=GitHistory(commits=[], file_changes=[]),
    )

    summary = ingest_repository(neo4j_connection, bare_repo)

    assert summary.nodes_merged == 1  # just the Repository node
    assert summary.relationships_merged == 0
