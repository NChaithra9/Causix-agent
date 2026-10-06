"""Phase 7 unit tests: the diff engine on hand-built snapshots, and each
extractor on in-memory source trees. No Git, Neo4j or Docker needed."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.architecture import (
    ArchitectureDiffEngine,
    EntityType,
    MemorySourceTree,
    build_snapshot_from_tree,
    snapshot_content_hash,
    snapshot_from_dict,
    snapshot_to_dict,
)
from src.architecture import (
    ChangeCategory as C,
)
from src.architecture import (
    ChangeType as T,
)
from src.architecture.builder import SnapshotBuilder
from src.architecture.extractors import compose as compose_module
from src.architecture.extractors import (
    extract_code,
    extract_compose,
    extract_declared,
    extract_manifests,
)
from src.architecture.extractors.manifests import normalise_name, parse_requirement
from src.git_history.models import CommitInfo, FileChange
from tests._architecture_fixtures import Snap


def diff(previous, current, **kw):
    return ArchitectureDiffEngine(previous.build(), current.build(), **kw).diff()


def kinds(outcome):
    return sorted((c.change_type.value, c.category.value, c.name) for c in outcome.changes)


def only(outcome):
    assert len(outcome.changes) == 1, kinds(outcome)
    return outcome.changes[0]


# ------------------------------------------------------------------ services


def test_service_added_removed_and_unchanged():
    a = Snap("r1").service("worker")
    b = Snap("r2")
    removed = only(diff(a, b))
    assert (removed.change_type, removed.category, removed.name) == (
        T.REMOVED,
        C.SERVICE_REMOVED,
        "worker",
    )
    added = only(diff(b, a))
    assert (added.change_type, added.category) == (T.ADDED, C.SERVICE_ADDED)
    assert diff(a, Snap("r3").service("worker")).changes == []


def test_service_relationship_added_removed_unchanged():
    base = Snap("r1").service("b").calls("svc", "b")
    plus_c = Snap("r2").service("b").calls("svc", "b").calls("svc", "c")
    result = diff(base, plus_c)
    assert [
        (c.change_type, c.category, c.source, c.target)
        for c in result.changes
        if c.entity_type is EntityType.RELATIONSHIP
    ] == [(T.ADDED, C.SERVICE_RELATIONSHIP_ADDED, "svc", "c")]
    assert (T.ADDED, C.SERVICE_ADDED) in {
        (c.change_type, c.category) for c in result.changes
    }  # c is new too
    removed = diff(plus_c, base)
    assert (T.REMOVED, C.SERVICE_RELATIONSHIP_REMOVED, "svc", "c") in [
        (c.change_type, c.category, c.source, c.target) for c in removed.changes
    ]
    assert diff(base, Snap("r3").service("b").calls("svc", "b")).changes == []


def test_replacing_a_target_is_one_removal_and_one_addition_not_both_present():
    previous = Snap("100").service("b").calls("svc", "b")
    current = Snap("200").service("c").calls("svc", "c")
    rels = [c for c in diff(previous, current).changes if c.entity_type is EntityType.RELATIONSHIP]
    assert sorted((c.change_type.value, c.target) for c in rels) == [
        ("ADDED", "c"),
        ("REMOVED", "b"),
    ]


# ----------------------------------------------------------------------- APIs


def test_api_added_and_removed_have_no_separate_exposes_noise():
    before = Snap("r1").api("GET", "/orders", "list_orders")
    after = Snap("r2").api("GET", "/orders", "list_orders").api("POST", "/orders", "create_order")
    change = only(diff(before, after))
    assert (change.change_type, change.category, change.name) == (
        T.ADDED,
        C.API_ADDED,
        "POST /orders",
    )
    removed = only(diff(after, before))
    assert (removed.change_type, removed.category) == (T.REMOVED, C.API_REMOVED)


def test_api_path_change_is_modified_when_the_same_handler_serves_a_new_path():
    change = only(
        diff(
            Snap("r1").api("GET", "/users/{id}", "get_user"),
            Snap("r2").api("GET", "/users/{user_id}", "get_user"),
        )
    )
    assert (change.change_type, change.category) == (T.MODIFIED, C.API_PATH_CHANGED)
    assert (
        change.previous_state["path"] == "/users/{id}"
        and change.current_state["path"] == "/users/{user_id}"
    )
    assert change.name == "GET /users/{id} -> GET /users/{user_id}"


def test_api_method_change_is_modified_when_the_same_handler_changes_method():
    change = only(
        diff(Snap("r1").api("GET", "/users", "users"), Snap("r2").api("POST", "/users", "users"))
    )
    assert (change.change_type, change.category) == (T.MODIFIED, C.API_METHOD_CHANGED)
    assert (change.previous_state["method"], change.current_state["method"]) == ("GET", "POST")


def test_api_without_a_handler_is_never_paired():
    result = diff(Snap("r1").api("GET", "/users"), Snap("r2").api("POST", "/users"))
    assert kinds(result) == [
        ("ADDED", "API_ADDED", "POST /users"),
        ("REMOVED", "API_REMOVED", "GET /users"),
    ]


def test_ambiguous_api_pairing_falls_back_to_add_and_remove():
    previous = Snap("r1").api("GET", "/a", "h").api("GET", "/b", "h")
    current = Snap("r2").api("GET", "/c", "h")
    assert {c.category for c in diff(previous, current).changes} == {C.API_ADDED, C.API_REMOVED}


def test_moving_an_exposed_api_to_another_service_is_an_api_relationship_change():
    previous = Snap("r1").service("other").api("GET", "/x", "h", service="svc")
    current = Snap("r2").service("other").api("GET", "/x", "h", service="other")
    cats = [(c.change_type, c.category, c.source) for c in diff(previous, current).changes]
    assert (T.ADDED, C.API_RELATIONSHIP_CHANGED, "other") in cats
    assert (T.REMOVED, C.API_RELATIONSHIP_CHANGED, "svc") in cats


# --------------------------------------------------------------------- events


def test_event_publisher_and_consumer_changes():
    previous = (
        Snap("r1").service("b").event("svc", "OrderCreated").event("b", "OrderCreated", "consumes")
    )
    current = Snap("r2").service("b").event("svc", "OrderCreated").event("svc", "PaymentRequested")
    result = diff(previous, current)
    got = {(c.change_type, c.category, c.name) for c in result.changes}
    assert (T.ADDED, C.EVENT_ADDED, "PaymentRequested") in got
    assert (T.ADDED, C.EVENT_PUBLISHER_CHANGED, "svc -[PUBLISHES]-> PaymentRequested") in got
    assert (T.REMOVED, C.EVENT_CONSUMER_CHANGED, "b -[CONSUMES]-> OrderCreated") in got
    assert not any(c.name == "OrderCreated" for c in result.changes)  # still exists, unchanged


def test_event_removed_when_nothing_refers_to_it_any_more():
    result = diff(Snap("r1").event("svc", "OrderCreated"), Snap("r2"))
    assert {(c.change_type, c.category) for c in result.changes} == {
        (T.REMOVED, C.EVENT_REMOVED),
        (T.REMOVED, C.EVENT_PUBLISHER_CHANGED),
    }


# ------------------------------------------------------------------- database


def test_database_access_added_and_removed():
    base = Snap("r1").database_only("users-db")
    current = Snap("r2").database("svc", "users-db")
    added = only(diff(base, current))
    assert (added.change_type, added.category) == (T.ADDED, C.DATABASE_ACCESS_ADDED)
    removed = only(diff(current, base))
    assert (removed.change_type, removed.category) == (T.REMOVED, C.DATABASE_ACCESS_REMOVED)


def test_database_and_table_added_and_removed():
    result = diff(Snap("r1"), Snap("r2").database_only("orders-db").table_only("orders"))
    assert {(c.change_type, c.category) for c in result.changes} == {
        (T.ADDED, C.DATABASE_ADDED),
        (T.ADDED, C.TABLE_ADDED),
    }
    gone = diff(Snap("r2").database_only("orders-db").table_only("orders"), Snap("r3"))
    assert {c.category for c in gone.changes} == {C.DATABASE_REMOVED, C.TABLE_REMOVED}


def test_table_access_change():
    result = diff(
        Snap("r1").table_only("users"), Snap("r2").table("svc", "users").table("svc", "payments")
    )
    got = {(c.change_type, c.category, c.name) for c in result.changes}
    assert (T.ADDED, C.DATABASE_ACCESS_ADDED, "svc -[ACCESSES]-> users") in got
    assert (T.ADDED, C.TABLE_ADDED, "payments") in got


def test_database_engine_change_is_an_entity_modification():
    before, after = Snap("r1"), Snap("r2")
    for snap, engine in ((before, "postgres"), (after, "mysql")):
        snap.database_only("db")
        snap.builder.snapshot.entities["database:db"].properties["engine"] = engine
    change = only(diff(before, after))
    assert (change.change_type, change.category) == (T.MODIFIED, C.ENTITY_MODIFIED)
    assert (change.previous_state, change.current_state) == (
        {"engine": "postgres"},
        {"engine": "mysql"},
    )


# ---------------------------------------------------------------- dependencies


def test_dependency_added_removed_unchanged_and_version_changed():
    base = Snap("r1").depends("svc", "requests", "==2.31").depends("svc", "fastapi", ">=0.1")
    plus = (
        Snap("r2")
        .depends("svc", "requests", "==2.31")
        .depends("svc", "fastapi", ">=0.1")
        .depends("svc", "httpx", ">=0.27")
    )
    added = only(diff(base, plus))
    assert (added.change_type, added.entity_type, added.category) == (
        T.ADDED,
        EntityType.DEPENDENCY,
        C.DEPENDENCY_ADDED,
    )
    assert (added.source, added.target) == ("svc", "httpx")
    removed = only(diff(plus, base))
    assert (removed.change_type, removed.category) == (T.REMOVED, C.DEPENDENCY_REMOVED)
    assert (
        diff(
            base, Snap("r3").depends("svc", "requests", "==2.31").depends("svc", "fastapi", ">=0.1")
        ).changes
        == []
    )
    bumped = only(
        diff(
            base, Snap("r4").depends("svc", "requests", "==2.32").depends("svc", "fastapi", ">=0.1")
        )
    )
    assert (bumped.change_type, bumped.category) == (T.MODIFIED, C.DEPENDENCY_VERSION_CHANGED)
    assert (bumped.previous_state, bumped.current_state) == (
        {"version": "==2.31"},
        {"version": "==2.32"},
    )


# -------------------------------------------------------------------- snapshots


def test_identical_snapshots_produce_no_changes():
    def make(revision):
        return (
            Snap(revision)
            .calls("svc", "b")
            .api("GET", "/x", "h")
            .event("svc", "E")
            .database("svc", "db")
            .table("svc", "t")
            .depends("svc", "requests", "==1")
        )

    assert diff(make("r1"), make("r2")).changes == []


def test_empty_previous_and_empty_current():
    rich = Snap("r2").calls("svc", "b").api("GET", "/x", "h").depends("svc", "requests")
    empty = ArchitectureDiffEngine(Snap("r1", service="svc").build(), rich.build()).diff()
    assert {c.change_type for c in empty.changes} == {T.ADDED}
    gone = ArchitectureDiffEngine(rich.build(), Snap("r3").build()).diff()
    assert {c.change_type for c in gone.changes} == {T.REMOVED}


def test_snapshots_of_other_repositories_are_never_compared():
    with pytest.raises(ValueError, match="different repositories"):
        ArchitectureDiffEngine(
            Snap("r1", repository="a").build(), Snap("r2", repository="b").build()
        )


def test_diff_does_not_mutate_either_snapshot():
    previous, current = Snap("r1").calls("svc", "b").build(), Snap("r2").calls("svc", "c").build()
    before = (snapshot_content_hash(previous), snapshot_content_hash(current))
    ArchitectureDiffEngine(previous, current).diff()
    assert (snapshot_content_hash(previous), snapshot_content_hash(current)) == before


def test_cycles_are_handled_by_plain_set_comparison():
    ring = lambda rev: Snap(rev).calls("a", "b").calls("b", "c").calls("c", "a")  # noqa: E731
    assert diff(ring("r1"), ring("r2")).changes == []
    broken = diff(ring("r1"), Snap("r2").calls("a", "b").calls("b", "c"))
    assert [(c.change_type, c.source, c.target) for c in broken.changes] == [(T.REMOVED, "c", "a")]
    self_call = diff(Snap("r1").service("a"), Snap("r2").calls("a", "a"))
    assert any(c.category is C.SERVICE_RELATIONSHIP_ADDED for c in self_call.changes)


def test_duplicate_facts_collapse_into_one_with_merged_evidence():
    snap = Snap("r1").depends("svc", "requests", "==1", file="requirements.txt", line=2)
    snap.depends("svc", "requests", "==1", file="pyproject.toml", line=9)
    snap.depends("svc", "requests", "==1", file="pyproject.toml", line=9)  # exact duplicate
    built = snap.build()
    assert len([k for k in built.relationships if k[0] == "DEPENDS_ON"]) == 1
    rel = built.relationships[("DEPENDS_ON", "service:svc", "dependency:requests")]
    assert sorted(e.file for e in rel.evidence) == ["pyproject.toml", "requirements.txt"]


def test_conflicting_declared_versions_are_kept_deterministically():
    snap = (
        Snap("r1")
        .depends("svc", "requests", "==1", file="a.txt")
        .depends("svc", "requests", "==2", file="b.txt")
    )
    rel = snap.build().relationships[("DEPENDS_ON", "service:svc", "dependency:requests")]
    assert rel.properties["version"] == "==1, ==2"


def test_relationship_to_an_unknown_entity_is_rejected():
    builder = SnapshotBuilder("repo", "r1")
    from src.architecture.models import ArchRelationship, GraphRelationship

    with pytest.raises(KeyError):
        builder.add_relationship(
            ArchRelationship(GraphRelationship.CALLS, "service:x", "service:y")
        )


# ---------------------------------------------------- unresolved information


def test_a_fact_in_an_unparseable_file_is_unknown_not_removed():
    previous = Snap("r1").api("GET", "/x", "h", file="app/api.py")
    current = Snap("r2").unresolved(
        "app/api.py", "could not parse"
    )  # the API vanished because the file broke
    result = diff(previous, current)
    assert result.changes == []
    assert [u.file for u in result.unresolved] == ["app/api.py"]
    assert (
        "GET /x" in result.unresolved[0].reason
        and "could not be analysed" in result.unresolved[0].reason
    )


def test_a_fact_appearing_in_a_previously_unparseable_file_is_unknown_not_added():
    previous = Snap("r1").unresolved("app/api.py")
    current = Snap("r2").api("GET", "/x", "h", file="app/api.py")
    assert diff(previous, current).changes == []


def test_unresolved_files_do_not_hide_changes_elsewhere():
    previous = Snap("r1").unresolved("broken.py").api("GET", "/a", "h", file="ok.py")
    current = Snap("r2").unresolved("broken.py")
    assert only(diff(previous, current)).category is C.API_REMOVED


# --------------------------------------------------------------------- evidence


def test_every_change_carries_evidence_with_revision_and_source():
    previous = (
        Snap("aaa")
        .calls("svc", "b", file="client.py", line=7)
        .api("GET", "/old", "h", file="api.py", line=3)
    )
    current = (
        Snap("bbb")
        .calls("svc", "c", file="client.py", line=8)
        .depends("svc", "httpx", ">=1", file="requirements.txt", line=2)
    )
    result = diff(previous, current)
    assert result.changes
    for change in result.changes:
        assert change.evidence, change.name
        assert all(
            e.revision in ("aaa", "bbb") and e.source and e.repository == "repo"
            for e in change.evidence
        )
        assert (change.previous_revision, change.current_revision) == ("aaa", "bbb")
    removed = next(c for c in result.changes if c.category is C.SERVICE_RELATIONSHIP_REMOVED)
    assert (removed.evidence[0].file, removed.evidence[0].line, removed.evidence[0].revision) == (
        "client.py",
        7,
        "aaa",
    )
    added = next(c for c in result.changes if c.category is C.SERVICE_RELATIONSHIP_ADDED)
    assert (added.evidence[0].file, added.evidence[0].line, added.evidence[0].relationship) == (
        "client.py",
        8,
        "CALLS",
    )


def test_git_facts_are_attached_as_evidence():
    previous = Snap("a" * 40).api("GET", "/gone", "h", file="app/old.py")
    current = Snap("b" * 40)
    commit = CommitInfo(
        "c" * 40,
        "n",
        "e",
        datetime(2026, 1, 1, tzinfo=UTC),
        "remove old api\n\nbody",
        ["app/old.py"],
    )
    result = diff(previous, current, file_changes=[FileChange("app/old.py", "D")], commits=[commit])
    api = next(c for c in result.changes if c.category is C.API_REMOVED)
    details = [(e.source, e.details) for e in api.evidence]
    assert any(s == "git_diff" and "deleted" in d for s, d in details)
    assert any(s == "git_commit" and "remove old api" in d for s, d in details)
    renamed = diff(previous, current, file_changes=[FileChange("app/new.py", "R", "app/old.py")])
    assert any(
        "renamed app/old.py -> app/new.py" in e.details
        for e in next(c for c in renamed.changes if c.category is C.API_REMOVED).evidence
    )


def test_output_order_is_deterministic():
    previous = Snap("r1")
    current = (
        Snap("r2").api("GET", "/b", "b").api("GET", "/a", "a").calls("svc", "z").calls("svc", "m")
    )
    first, second = diff(previous, current), diff(previous, current)
    assert [c.entity_id for c in first.changes] == [c.entity_id for c in second.changes]


# ---------------------------------------------------------------- serialisation


def test_snapshot_round_trips_through_plain_data_exactly():
    snapshot = (
        Snap("r1")
        .calls("svc", "b")
        .api("GET", "/x", "h")
        .depends("svc", "requests", "==1")
        .unresolved("x.py")
        .build()
    )
    again = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snapshot))))
    assert snapshot_content_hash(again) == snapshot_content_hash(snapshot)
    assert sorted(again.relationships) == sorted(snapshot.relationships)


def test_content_hash_ignores_insertion_order_but_not_content():
    one = Snap("r1").calls("svc", "a").calls("svc", "b").build()
    two = Snap("r1").calls("svc", "b").calls("svc", "a").build()
    assert snapshot_content_hash(one) == snapshot_content_hash(two)
    assert snapshot_content_hash(one) != snapshot_content_hash(Snap("r1").calls("svc", "a").build())


# ------------------------------------------------------------------- extractors


def snapshot_of(files, *, known=(), service="orders", extractors=None):
    kwargs = {"extractors": extractors} if extractors else {}
    return build_snapshot_from_tree(
        MemorySourceTree(files),
        repository="orders",
        revision="rev1",
        service_name=service,
        known_services=known,
        **kwargs,
    )


def rels(snapshot):
    return sorted(snapshot.relationships)


ROUTE_FILE = """\
from fastapi import APIRouter, FastAPI
from flask import Blueprint
router = APIRouter(prefix="/v1")
app = FastAPI()
bp = Blueprint("bp", __name__, url_prefix="/bp")

@router.get("/items/{item_id}")
async def get_item(item_id): ...

@app.post("/orders")
def create_order(): ...

@bp.route("/multi", methods=["GET", "POST"])
def multi(): ...

@app.route("/default")
def default(): ...

class Controller:
    @app.delete("/orders/{id}")
    def remove(self, id): ...
"""


def test_routes_are_extracted_with_prefixes_methods_handlers_and_lines():
    snapshot = snapshot_of({"app/api.py": ROUTE_FILE})
    names = [e.name for e in snapshot.apis]
    assert names == [
        "DELETE /orders/{id}",
        "GET /bp/multi",
        "GET /default",
        "GET /v1/items/{item_id}",
        "POST /bp/multi",
        "POST /orders",
    ]
    item = snapshot.entities["api:GET /v1/items/{item_id}"]
    assert item.meta["handler"] == "get_item" and item.evidence[0].line == 7
    assert item.evidence[0].file == "app/api.py" and item.evidence[0].relationship == "EXPOSES"
    assert snapshot.entities["api:DELETE /orders/{id}"].meta["handler"] == "Controller.remove"
    assert ("EXPOSES", "service:orders", "api:POST /orders") in snapshot.relationships


def test_non_routes_and_test_files_are_ignored_and_dynamic_paths_are_unresolved():
    source = """\
from fastapi import FastAPI
app = FastAPI()
PATH = "/dyn"

@cache.get("key")
def cached(): ...

@app.get(PATH)
def dynamic(): ...
"""
    snapshot = snapshot_of(
        {"app/x.py": source, "tests/test_routes.py": '@app.get("/in-tests")\ndef t(): ...\n'}
    )
    assert snapshot.apis == []
    assert len(snapshot.unresolved) == 1 and "not a string literal" in snapshot.unresolved[0].reason
    assert snapshot.unresolved[0].file == "app/x.py"


def test_syntax_errors_are_reported_not_ignored():
    snapshot = snapshot_of({"bad.py": "def broken(:\n"})
    assert [(u.file, "could not parse" in u.reason) for u in snapshot.unresolved] == [
        ("bad.py", True)
    ]


def test_outbound_calls_only_for_known_services():
    source = """\
import requests, httpx
def go(client, session):
    requests.get("http://payment-service/pay")
    httpx.post(f"https://inventory.local/{1}")
    client.put("http://payment-service/x")
    requests.get("http://example.com/other")
    requests.get("http://orders/self")
    session.request("DELETE", "http://payment-service/y")
"""
    snapshot = snapshot_of({"app/c.py": source}, known={"payment-service", "inventory.local"})
    assert [r for r in rels(snapshot) if r[0] == "CALLS"] == [
        ("CALLS", "service:orders", "service:inventory.local"),
        ("CALLS", "service:orders", "service:payment-service"),
    ]
    call = snapshot.relationships[("CALLS", "service:orders", "service:payment-service")]
    assert sorted(e.line for e in call.evidence) == [3, 5, 8]
    assert not snapshot_of({"app/c.py": source}).relationships.keys() - {
        ("DEPENDS_ON",)
    }  # nothing known -> no calls


def test_orm_tables():
    source = 'class User(Base):\n    __tablename__ = "users"\n\nclass Plain:\n    name = "x"\n'
    snapshot = snapshot_of({"models.py": source})
    assert [e.name for e in snapshot.tables] == ["users"]
    assert ("ACCESSES", "service:orders", "table:users") in snapshot.relationships
    assert snapshot.entities["table:users"].evidence[0].line == 2


def test_requirement_parsing():
    assert parse_requirement("Fastapi[standard]>=0.115 ; python_version>'3.9'") == (
        "fastapi",
        ">=0.115",
    )
    assert parse_requirement("my_pkg == 1.2 # pinned") == ("my-pkg", "==1.2")
    assert parse_requirement("aetherion-sdk @ https://x/y.whl") == (
        "aetherion-sdk",
        "@https://x/y.whl",
    )
    assert parse_requirement("requests") == ("requests", "")
    for skipped in (
        "",
        "# comment",
        "-r other.txt",
        "--index-url https://x",
        "-e .",
        "https://x/y.whl",
        "./local",
    ):
        assert parse_requirement(skipped) is None
    assert normalise_name("Foo_Bar.baz") == "foo-bar-baz"


def test_manifest_dependencies_from_requirements_and_pyproject():
    files = {
        "requirements.txt": "fastapi>=0.115\n# comment\nrequests==2.31\n-r base.txt\n",
        "requirements-dev.txt": "pytest\n",
        "pyproject.toml": (
            '[project]\nname = "x"\ndependencies = [\n  "neo4j>=5.28,<7",\n  "fastapi>=0.115",\n]\n'
        ),
    }
    snapshot = snapshot_of(files)
    assert [e.name for e in snapshot.dependencies] == [
        "fastapi",
        "neo4j",
        "requests",
    ]  # dev requirements excluded
    fastapi = snapshot.relationships[("DEPENDS_ON", "service:orders", "dependency:fastapi")]
    assert sorted((e.file, e.line) for e in fastapi.evidence) == [
        ("pyproject.toml", 5),
        ("requirements.txt", 1),
    ]
    assert fastapi.properties["version"] == ">=0.115"
    neo = snapshot.relationships[("DEPENDS_ON", "service:orders", "dependency:neo4j")]
    assert neo.properties["version"] == ">=5.28,<7"


def test_invalid_pyproject_is_unresolved():
    snapshot = snapshot_of({"pyproject.toml": "[project\n"})
    assert (
        snapshot.unresolved[0].file == "pyproject.toml"
        and "not valid TOML" in snapshot.unresolved[0].reason
    )


COMPOSE_TEXT = """\
services:
  web:
    image: example/web
    depends_on:
      - db
      - cache
  db:
    image: postgres:16-alpine
  cache:
    image: redis:7
  orders:
    image: example/orders
    depends_on:
      web:
        condition: service_started
"""


def test_compose_services_databases_and_depends_on():
    pytest.importorskip("yaml")
    snapshot = snapshot_of({"docker-compose.yml": COMPOSE_TEXT})
    assert [e.name for e in snapshot.services] == [
        "cache",
        "orders",
        "web",
    ]  # orders merged with the repo service
    assert [(e.name, e.properties) for e in snapshot.databases] == [("db", {"engine": "postgres"})]
    assert [r for r in rels(snapshot) if r[0] == "DEPENDS_ON"] == [
        ("DEPENDS_ON", "service:orders", "service:web"),
        ("DEPENDS_ON", "service:web", "database:db"),
        ("DEPENDS_ON", "service:web", "service:cache"),
    ]
    web = snapshot.entities["service:web"]
    assert any(e.file == "docker-compose.yml" and e.line == 2 for e in web.evidence)


def test_compose_dependencies_make_calls_attributable_to_known_services():
    pytest.importorskip("yaml")
    files = {
        "docker-compose.yml": COMPOSE_TEXT,
        "app/c.py": 'import requests\nrequests.get("http://web/x")\n',
    }
    assert ("CALLS", "service:orders", "service:web") in snapshot_of(files).relationships


def test_invalid_compose_yaml_and_unknown_depends_on_are_unresolved():
    pytest.importorskip("yaml")
    bad = snapshot_of({"docker-compose.yml": "services: [unclosed\n"})
    assert "not valid YAML" in bad.unresolved[0].reason
    dangling = snapshot_of({"docker-compose.yml": "services:\n  a:\n    depends_on: [ghost]\n"})
    assert "unknown service 'ghost'" in dangling.unresolved[0].reason


def test_missing_pyyaml_makes_compose_unresolved_instead_of_empty(monkeypatch):
    monkeypatch.setattr(compose_module, "_load_yaml", lambda: None)
    snapshot = snapshot_of({"docker-compose.yml": COMPOSE_TEXT})
    assert [u.file for u in snapshot.unresolved] == ["docker-compose.yml"]
    assert "PyYAML is not installed" in snapshot.unresolved[0].reason
    assert [e.name for e in snapshot.services] == ["orders"]


def test_declared_architecture_file():
    declared = json.dumps(
        {
            "publishes": ["OrderCreated"],
            "consumes": ["PaymentCompleted"],
            "calls": ["payment-service"],
            "databases": ["orders-db"],
            "tables": ["orders"],
            "apis": [{"method": "get", "path": "/health"}],
        },
        indent=2,
    )
    snapshot = snapshot_of({"rootfix-architecture.json": declared})
    assert [r[0:1] + r[2:] for r in rels(snapshot) if r[0] != "DEPENDS_ON"] == [
        ("ACCESSES", "database:orders-db"),
        ("ACCESSES", "table:orders"),
        ("CALLS", "service:payment-service"),
        ("CONSUMES", "event:PaymentCompleted"),
        ("EXPOSES", "api:GET /health"),
        ("PUBLISHES", "event:OrderCreated"),
    ]
    published = snapshot.relationships[("PUBLISHES", "service:orders", "event:OrderCreated")]
    assert (
        published.evidence[0].file == "rootfix-architecture.json"
        and published.evidence[0].line == 3
    )


def test_declared_file_problems_are_unresolved_never_guessed():
    for text, fragment in (
        ("{not json", "not valid JSON"),
        ("[1]", "must contain a JSON object"),
        ('{"colours": ["x"]}', "unknown declaration key 'colours'"),
        ('{"publishes": "OrderCreated"}', "list of non-empty strings"),
        ('{"apis": [{"method": "GET"}]}', "needs a 'method' and a '/path'"),
    ):
        snapshot = snapshot_of({"rootfix-architecture.json": text})
        assert any(fragment in u.reason for u in snapshot.unresolved), (text, snapshot.unresolved)
        assert snapshot.events == [] and snapshot.apis == []


def test_extractors_can_be_selected_individually():
    files = {"requirements.txt": "requests\n", "app/api.py": '@app.get("/x")\ndef f(): ...\n'}
    only_manifests = snapshot_of(files, extractors=(extract_manifests,))
    assert [e.name for e in only_manifests.dependencies] == [
        "requests"
    ] and only_manifests.apis == []
    assert [e.name for e in snapshot_of(files, extractors=(extract_code,)).apis] == ["GET /x"]
    assert extract_compose and extract_declared  # public


def test_results_serialise_to_json():
    from src.architecture import ArchitectureChangeResult, ArchitectureChangeStatus

    outcome = diff(Snap("r1"), Snap("r2").calls("svc", "b"))
    result = ArchitectureChangeResult(
        status=ArchitectureChangeStatus.CHANGES_DETECTED,
        repository="repo",
        previous_revision="r1",
        current_revision="r2",
        changes=outcome.changes,
    )
    data = json.loads(json.dumps(result.to_dict()))
    assert data["status"] == "CHANGES_DETECTED" and data["changes"][0]["change_type"] in {
        "ADDED",
        "REMOVED",
    }
