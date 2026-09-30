"""Tests for `src.rca.call_graph` -- forward (callees) and reverse (callers)
CALLS traversal, depth control, and cycle safety. Needs a real, reachable
Neo4j (see `neo4j_connection` in conftest.py -- skips cleanly otherwise)."""

from __future__ import annotations

from pathlib import Path

from git import Repo

from src.graph import ingest_repository
from src.rca.call_graph import traverse_callees, traverse_callers
from src.rca.models import ResolutionStatus
from src.repository import build_repository_facts

from ._sample_repo import build_sample_repository


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_direct_callee_found_at_depth_one(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    process_id = neo4j_connection.execute_read(
        "MATCH (m:Method {name: 'process'}) RETURN m.id AS id"
    )[0]["id"]

    result = traverse_callees(neo4j_connection, process_id, max_depth=1)

    assert result.resolution_status == ResolutionStatus.RESOLVED
    assert result.direction == "forward"
    assert any(e.target_name == "validate_payment" for e in result.edges)
    assert all(e.depth == 1 for e in result.edges)


def test_reverse_traversal_finds_the_caller(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    validate_id = neo4j_connection.execute_read(
        "MATCH (f:Function {name: 'validate_payment'}) RETURN f.id AS id"
    )[0]["id"]

    result = traverse_callers(neo4j_connection, validate_id, max_depth=1)

    assert result.resolution_status == ResolutionStatus.RESOLVED
    assert result.direction == "reverse"
    assert any(e.source_name == "process" for e in result.edges)


def test_unresolved_for_a_node_id_that_does_not_exist(neo4j_connection):
    result = traverse_callees(neo4j_connection, "not-a-real-node-id", max_depth=2)
    assert result.resolution_status == ResolutionStatus.UNRESOLVED


def test_multiple_callers_of_the_same_function(tmp_path, neo4j_connection):
    root = tmp_path
    repo = Repo.init(root)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "test@example.com")

    _write(root / "shared.py", "def helper():\n    pass\n")
    _write(root / "a.py", "from shared import helper\n\n\ndef caller_a():\n    helper()\n")
    _write(root / "b.py", "from shared import helper\n\n\ndef caller_b():\n    helper()\n")
    repo.index.add(["shared.py", "a.py", "b.py"])
    repo.index.commit("Add shared helper and two callers")

    facts = build_repository_facts(root)
    ingest_repository(neo4j_connection, facts)

    helper_id = neo4j_connection.execute_read(
        "MATCH (f:Function {name: 'helper'}) RETURN f.id AS id"
    )[0]["id"]

    result = traverse_callers(neo4j_connection, helper_id, max_depth=1)

    caller_names = {e.source_name for e in result.edges}
    assert caller_names == {"caller_a", "caller_b"}


def test_depth_two_reaches_transitive_callees_but_not_further(tmp_path, neo4j_connection):
    root = tmp_path
    repo = Repo.init(root)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "test@example.com")

    _write(
        root / "chain.py",
        "def level_one():\n    level_two()\n\n\n"
        "def level_two():\n    level_three()\n\n\n"
        "def level_three():\n    pass\n",
    )
    repo.index.add(["chain.py"])
    repo.index.commit("Add a three-level call chain")

    facts = build_repository_facts(root)
    ingest_repository(neo4j_connection, facts)

    level_one_id = neo4j_connection.execute_read(
        "MATCH (f:Function {name: 'level_one'}) RETURN f.id AS id"
    )[0]["id"]

    depth_one = traverse_callees(neo4j_connection, level_one_id, max_depth=1)
    depth_two = traverse_callees(neo4j_connection, level_one_id, max_depth=2)

    names_depth_one = {e.target_name for e in depth_one.edges}
    names_depth_two = {e.target_name for e in depth_two.edges}

    assert names_depth_one == {"level_two"}
    assert names_depth_two == {"level_two", "level_three"}


def test_recursive_call_does_not_infinite_loop(tmp_path, neo4j_connection):
    root = tmp_path
    repo = Repo.init(root)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "test@example.com")

    _write(root / "recursive.py", "def countdown(n):\n    countdown(n - 1)\n")
    repo.index.add(["recursive.py"])
    repo.index.commit("Add a self-recursive function")

    facts = build_repository_facts(root)
    ingest_repository(neo4j_connection, facts)

    countdown_id = neo4j_connection.execute_read(
        "MATCH (f:Function {name: 'countdown'}) RETURN f.id AS id"
    )[0]["id"]

    result = traverse_callees(neo4j_connection, countdown_id, max_depth=5)

    # Must terminate (pytest itself proves that), and must never revisit the
    # already-visited `countdown` node as a *new* frontier entry.
    assert result.visited_node_ids == {countdown_id}
    assert all(e.target_id == countdown_id for e in result.edges)
