"""The revision-isolated Neo4j snapshot store (needs the ``neo4j_connection`` fixture;
skipped automatically when Neo4j is not running)."""

from __future__ import annotations

import pytest

from src.architecture import (
    ArchitectureSnapshotStore,
    SnapshotConflictError,
    build_snapshot,
    detect_architecture_changes,
    snapshot_content_hash,
)
from tests._architecture_fixtures import REQUIREMENTS, ROUTES, GitRepo, Snap, base_files

pytest.importorskip("yaml")


@pytest.fixture
def store(neo4j_connection):
    return ArchitectureSnapshotStore(neo4j_connection)


def rich(revision, repository="repo"):
    return (
        Snap(revision, repository=repository)
        .calls("svc", "b", file="client.py", line=4)
        .api("GET", "/x", "h", file="api.py", line=2)
        .event("svc", "OrderCreated")
        .database("svc", "db")
        .table("svc", "t")
        .depends("svc", "requests", "==2.31")
        .unresolved("broken.py", "could not parse")
        .build()
    )


def test_a_stored_snapshot_loads_back_identically(store):
    snapshot = rich("a" * 40)
    assert store.save(snapshot) is True
    loaded = store.load("repo", "a" * 40)
    assert loaded is not None
    assert snapshot_content_hash(loaded) == snapshot_content_hash(snapshot)
    assert sorted(loaded.relationships) == sorted(snapshot.relationships)
    call = loaded.relationships[("CALLS", "service:svc", "service:b")]
    assert (call.evidence[0].file, call.evidence[0].line, call.evidence[0].revision) == (
        "client.py",
        4,
        "a" * 40,
    )
    assert [(u.file, u.reason) for u in loaded.unresolved] == [("broken.py", "could not parse")]


def test_missing_snapshot_is_none(store):
    assert store.load("repo", "f" * 40) is None
    assert store.revisions("repo") == []


def test_saving_the_same_snapshot_twice_is_a_no_op(store, neo4j_connection):
    snapshot = rich("a" * 40)
    assert store.save(snapshot) is True
    count = neo4j_connection.execute_read("MATCH (n) RETURN count(n) AS n")[0]["n"]
    assert store.save(rich("a" * 40)) is False
    assert neo4j_connection.execute_read("MATCH (n) RETURN count(n) AS n")[0]["n"] == count


def test_a_stored_snapshot_is_never_overwritten(store):
    store.save(rich("a" * 40))
    different = Snap("a" * 40).calls("svc", "other").build()
    with pytest.raises(SnapshotConflictError):
        store.save(different)
    assert ("CALLS", "service:svc", "service:b") in store.load("repo", "a" * 40).relationships


def test_revisions_of_one_repository_stay_separate(store):
    store.save(Snap("1" * 40).calls("svc", "b").build())
    store.save(Snap("2" * 40).calls("svc", "c").build())
    one, two = store.load("repo", "1" * 40), store.load("repo", "2" * 40)
    assert [k[2] for k in one.relationships if k[0] == "CALLS"] == ["service:b"]
    assert [k[2] for k in two.relationships if k[0] == "CALLS"] == ["service:c"]
    assert {r for r, _ in store.revisions("repo")} == {"1" * 40, "2" * 40}


def test_repositories_are_isolated_even_with_the_same_revision_text(store):
    store.save(Snap("a" * 40, repository="alpha").calls("svc", "b").build())
    store.save(Snap("a" * 40, repository="beta").calls("svc", "c").build())
    assert [k[2] for k in store.load("alpha", "a" * 40).relationships if k[0] == "CALLS"] == [
        "service:b"
    ]
    assert [k[2] for k in store.load("beta", "a" * 40).relationships if k[0] == "CALLS"] == [
        "service:c"
    ]
    assert [r for r, _ in store.revisions("alpha")] == ["a" * 40]


def test_the_store_does_not_touch_the_code_graph(store, neo4j_connection):
    neo4j_connection.execute_write("CREATE (:Repository {id: 'keep-me', name: 'x'})")
    store.save(rich("a" * 40))
    rows = neo4j_connection.execute_read(
        "MATCH (r:Repository {id: 'keep-me'}) RETURN count(r) AS n"
    )
    assert rows[0]["n"] == 1
    labels = {
        r["l"]
        for r in neo4j_connection.execute_read("MATCH (n) UNWIND labels(n) AS l RETURN DISTINCT l")
    }
    assert labels == {"Repository", "ArchitectureSnapshot", "ArchitectureEntity"}


def test_detection_persists_snapshots_and_reuses_them(store, tmp_path):
    repo = GitRepo(tmp_path / "orders")
    first = repo.commit(base_files())
    second = repo.commit({"requirements.txt": REQUIREMENTS + "httpx\n", "app/routes.py": ROUTES})
    fresh = detect_architecture_changes(repo.path, first, second, store=store)
    assert {r for r, _ in store.revisions("orders")} == {first, second}
    again = detect_architecture_changes(
        repo.path, first, second, store=store
    )  # served from the store
    assert fresh.to_dict() == again.to_dict()
    assert [c.category.value for c in fresh.changes] == ["DEPENDENCY_ADDED"]
    stored = store.load("orders", first)
    assert snapshot_content_hash(stored) == snapshot_content_hash(build_snapshot(repo.path, first))
