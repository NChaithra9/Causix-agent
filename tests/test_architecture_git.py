"""Phase 7 end to end on small REAL Git repositories: revisions are read from
Git's object store, compared, and every change is traced back to evidence."""

from __future__ import annotations

import json

import pytest

from src.architecture import (
    ArchitectureChangeStatus as Status,
)
from src.architecture import (
    ChangeCategory as C,
)
from src.architecture import (
    ChangeType as T,
)
from src.architecture import (
    EntityType,
    detect_architecture_changes,
)
from tests._architecture_fixtures import REQUIREMENTS, ROUTES, GitRepo, base_files

pytest.importorskip("yaml")  # the fixture repos describe their services in docker-compose


@pytest.fixture
def orders(tmp_path):
    repo = GitRepo(tmp_path / "orders")
    return repo, repo.commit(base_files(), "initial architecture")


def detect(repo, previous, current, **kw):
    return detect_architecture_changes(repo.path, previous, current, **kw)


def facts(result):
    return sorted(
        (c.change_type.value, c.category.value, c.source, c.target, c.name) for c in result.changes
    )


def assert_evidenced(result):
    assert result.changes
    for change in result.changes:
        assert change.evidence, change.name
        assert change.repository == result.repository
        assert (change.previous_revision, change.current_revision) == (
            result.previous_revision,
            result.current_revision,
        )
        assert any(e.source != "git_diff" and e.source != "git_commit" for e in change.evidence)


def line_of(text, needle):
    return next(n for n, line in enumerate(text.splitlines(), start=1) if needle in line)


# -------------------------------------------------------------- relationships


def test_added_service_relationship_with_full_evidence(orders):
    repo, first = orders
    routes = ROUTES + '\n\ndef notify():\n    requests.post("http://service-c/notify")\n'
    second = repo.commit({"app/routes.py": routes}, "notify service-c from orders")

    result = detect(repo, first, second)
    assert result.status is Status.CHANGES_DETECTED
    assert facts(result) == [
        (
            "ADDED",
            "SERVICE_RELATIONSHIP_ADDED",
            "orders",
            "service-c",
            "orders -[CALLS]-> service-c",
        )
    ]
    change = result.changes[0]
    primary = change.evidence[0]
    assert (primary.file, primary.line, primary.revision, primary.commit) == (
        "app/routes.py",
        line_of(routes, "service-c"),
        second,
        second,
    )
    assert primary.relationship == "CALLS" and primary.repository == "orders"
    assert any(e.source == "git_diff" and "modified" in e.details for e in change.evidence)
    assert any(e.source == "git_commit" and e.commit == second for e in change.evidence)
    assert result.previous_revision == first and result.current_revision == second
    assert_evidenced(result)


def test_removed_service_relationship(orders):
    repo, first = orders
    second = repo.commit(
        {"app/routes.py": ROUTES.replace('requests.get("http://service-b/stock").json()', "[]")}
    )
    result = detect(repo, first, second)
    assert facts(result) == [
        (
            "REMOVED",
            "SERVICE_RELATIONSHIP_REMOVED",
            "orders",
            "service-b",
            "orders -[CALLS]-> service-b",
        )
    ]
    removed = result.changes[0].evidence[0]
    assert (removed.file, removed.line, removed.revision) == (
        "app/routes.py",
        line_of(ROUTES, "service-b"),
        first,
    )


def test_replacing_a_call_target_reports_both_sides(orders):
    repo, first = orders
    second = repo.commit({"app/routes.py": ROUTES.replace("service-b", "service-c")})
    assert [(c[0], c[1], c[3]) for c in facts(detect(repo, first, second))] == [
        ("ADDED", "SERVICE_RELATIONSHIP_ADDED", "service-c"),
        ("REMOVED", "SERVICE_RELATIONSHIP_REMOVED", "service-b"),
    ]


def test_unchanged_architecture_across_commits_is_no_changes(orders):
    repo, first = orders
    second = repo.commit({"README.md": "docs only\n"})
    result = detect(repo, first, second)
    assert result.status is Status.NO_CHANGES and result.changes == []
    assert [f.path for f in result.changed_files] == ["README.md"]
    assert detect(repo, first, first).status is Status.NO_CHANGES


# ------------------------------------------------------------------ dependencies


def test_dependency_added_removed_and_version_changed(orders):
    repo, first = orders
    added = repo.commit({"requirements.txt": REQUIREMENTS + "httpx>=0.27\n"})
    result = detect(repo, first, added)
    change = result.changes[0]
    assert facts(result) == [
        ("ADDED", "DEPENDENCY_ADDED", "orders", "httpx", "orders -[DEPENDS_ON]-> httpx")
    ]
    assert change.entity_type is EntityType.DEPENDENCY
    assert (change.evidence[0].file, change.evidence[0].line) == ("requirements.txt", 3)

    removed = repo.commit({"requirements.txt": "fastapi>=0.115\n"})
    result = detect(repo, added, removed)
    assert {(c[0], c[1], c[3]) for c in facts(result)} == {
        ("REMOVED", "DEPENDENCY_REMOVED", "httpx"),
        ("REMOVED", "DEPENDENCY_REMOVED", "requests"),
    }

    bumped = repo.commit({"requirements.txt": "fastapi>=0.116\n"})
    result = detect(repo, removed, bumped)
    change = result.changes[0]
    assert (change.change_type, change.category) == (T.MODIFIED, C.DEPENDENCY_VERSION_CHANGED)
    assert (change.previous_state, change.current_state) == (
        {"version": ">=0.115"},
        {"version": ">=0.116"},
    )
    assert_evidenced(result)


def test_pyproject_dependency_change(tmp_path):
    repo = GitRepo(tmp_path / "svc")
    first = repo.commit({"pyproject.toml": '[project]\nname="x"\ndependencies = ["requests>=2"]\n'})
    second = repo.commit(
        {"pyproject.toml": '[project]\nname="x"\ndependencies = ["requests>=2", "neo4j>=5"]\n'}
    )
    result = detect(repo, first, second)
    assert [(c.category, c.target, c.evidence[0].file) for c in result.changes] == [
        (C.DEPENDENCY_ADDED, "neo4j", "pyproject.toml")
    ]


# ------------------------------------------------------------------------- APIs


def test_api_added(orders):
    repo, first = orders
    routes = ROUTES + '\n\n@app.post("/orders")\ndef create_order():\n    return {}\n'
    result = detect(repo, first, repo.commit({"app/routes.py": routes}))
    assert facts(result) == [("ADDED", "API_ADDED", None, None, "POST /orders")]
    assert result.changes[0].evidence[0].line == line_of(routes, '@app.post("/orders")')
    assert_evidenced(result)


def test_api_path_method_and_parameter_changes(tmp_path):
    repo = GitRepo(tmp_path / "users")
    v1 = (
        '@app.get("/users/{id}")\ndef get_user(id): ...\n\n@app.get("/orders")\ndef orders(): ...\n'
    )
    first = repo.commit({"api.py": v1})
    path = repo.commit({"api.py": v1.replace("/users/{id}", "/users/{user_id}")})
    method = repo.commit(
        {
            "api.py": v1.replace("/users/{id}", "/users/{user_id}").replace(
                '@app.get("/orders")', '@app.post("/orders")'
            )
        }
    )
    first_change = detect(repo, first, path).changes[0]
    assert (first_change.change_type, first_change.category) == (T.MODIFIED, C.API_PATH_CHANGED)
    assert first_change.name == "GET /users/{id} -> GET /users/{user_id}"
    second_change = detect(repo, path, method).changes[0]
    assert (second_change.change_type, second_change.category) == (T.MODIFIED, C.API_METHOD_CHANGED)
    assert (second_change.previous_state["method"], second_change.current_state["method"]) == (
        "GET",
        "POST",
    )
    # both sides of the modification are evidenced
    assert {e.revision for e in first_change.evidence if e.source == "route_decorator"} == {
        first,
        path,
    }


def test_api_removed_because_its_file_was_deleted(orders):
    repo, first = orders
    second = repo.commit({"app/routes.py": None}, "delete routes")
    result = detect(repo, first, second)
    got = {(c.category, c.name) for c in result.changes}
    assert (C.API_REMOVED, "GET /orders") in got and (
        C.SERVICE_RELATIONSHIP_REMOVED,
        "orders -[CALLS]-> service-b",
    ) in got
    api = next(c for c in result.changes if c.category is C.API_REMOVED)
    assert any(
        e.source == "git_diff" and "deleted" in e.details and e.file == "app/routes.py"
        for e in api.evidence
    )
    assert [(f.path, f.status) for f in result.changed_files] == [("app/routes.py", "D")]


def test_a_pure_file_rename_is_not_an_architecture_change(orders):
    repo, first = orders
    second = repo.commit(moves=[("app/routes.py", "app/api/routes.py")], message="move routes")
    result = detect(repo, first, second)
    assert result.status is Status.NO_CHANGES
    renamed = result.changed_files[0]
    assert (renamed.status, renamed.old_path, renamed.path) == (
        "R",
        "app/routes.py",
        "app/api/routes.py",
    )


def test_rename_combined_with_a_new_route_reports_only_the_new_route(orders):
    repo, first = orders
    moved = ROUTES + '\n\n@app.get("/health")\ndef health():\n    return "ok"\n'
    second = repo.commit(
        {"app/api/routes.py": moved}, moves=[("app/routes.py", "app/api/routes.py")]
    )
    result = detect(repo, first, second)
    assert facts(result) == [("ADDED", "API_ADDED", None, None, "GET /health")]
    assert result.changes[0].evidence[0].file == "app/api/routes.py"
    assert any(f.status == "R" for f in result.changed_files)


# ----------------------------------------------------------------------- events


def test_event_publisher_and_consumer_declared_then_removed(orders):
    repo, first = orders
    declared = json.dumps(
        {"publishes": ["OrderCreated"], "consumes": ["PaymentCompleted"]}, indent=2
    )
    second = repo.commit({"rootfix-architecture.json": declared})
    result = detect(repo, first, second)
    got = {(c.change_type.value, c.category.value, c.name) for c in result.changes}
    assert got == {
        ("ADDED", "EVENT_ADDED", "OrderCreated"),
        ("ADDED", "EVENT_ADDED", "PaymentCompleted"),
        ("ADDED", "EVENT_PUBLISHER_CHANGED", "orders -[PUBLISHES]-> OrderCreated"),
        ("ADDED", "EVENT_CONSUMER_CHANGED", "orders -[CONSUMES]-> PaymentCompleted"),
    }
    assert all(c.evidence[0].file == "rootfix-architecture.json" for c in result.changes)

    third = repo.commit(
        {"rootfix-architecture.json": json.dumps({"publishes": ["OrderCreated"]}, indent=2)}
    )
    result = detect(repo, second, third)
    assert {(c.change_type.value, c.category.value) for c in result.changes} == {
        ("REMOVED", "EVENT_REMOVED"),
        ("REMOVED", "EVENT_CONSUMER_CHANGED"),
    }
    assert_evidenced(result)


# --------------------------------------------------------------------- databases


def test_orm_table_and_database_access_changes(orders):
    repo, first = orders
    models = 'class Order(Base):\n    __tablename__ = "orders"\n'
    second = repo.commit(
        {
            "app/models.py": models,
            "rootfix-architecture.json": json.dumps({"databases": ["orders-db"]}),
        }
    )
    result = detect(repo, first, second)
    assert {(c.category.value, c.name) for c in result.changes} == {
        ("TABLE_ADDED", "orders"),
        ("DATABASE_ACCESS_ADDED", "orders -[ACCESSES]-> orders"),
        ("DATABASE_ACCESS_ADDED", "orders -[ACCESSES]-> orders-db"),
    }
    third = repo.commit({"app/models.py": None})
    result = detect(repo, second, third)
    assert {c.category.value for c in result.changes} == {
        "TABLE_REMOVED",
        "DATABASE_ACCESS_REMOVED",
    }
    orm = next(c for c in detect(repo, first, second).changes if c.category is C.TABLE_ADDED)
    assert (orm.evidence[0].file, orm.evidence[0].line, orm.evidence[0].source) == (
        "app/models.py",
        2,
        "orm_model",
    )


def test_compose_database_added_and_depends_on_changes(orders):
    repo, first = orders
    compose = (
        base_files()["docker-compose.yml"]
        + "  orders:\n    image: example/orders\n    depends_on: [orders-db]\n"
    )
    second = repo.commit({"docker-compose.yml": compose + "  analytics-db:\n    image: mongo:7\n"})
    result = detect(repo, first, second)
    got = {(c.category.value, c.name) for c in result.changes}
    assert ("DATABASE_ADDED", "analytics-db") in got
    assert (
        "SERVICE_RELATIONSHIP_ADDED",
        "orders -[DEPENDS_ON]-> orders-db",
    ) not in got  # a database is not a service
    assert ("RELATIONSHIP_ADDED", "orders -[DEPENDS_ON]-> orders-db") in got
    assert all(c.evidence[0].file == "docker-compose.yml" for c in result.changes)


def test_new_compose_service_and_service_removal(orders):
    repo, first = orders
    plus = base_files()["docker-compose.yml"] + "  service-d:\n    image: example/d\n"
    second = repo.commit({"docker-compose.yml": plus})
    assert facts(detect(repo, first, second)) == [
        ("ADDED", "SERVICE_ADDED", None, None, "service-d")
    ]
    third = repo.commit({"docker-compose.yml": base_files()["docker-compose.yml"]})
    assert facts(detect(repo, second, third)) == [
        ("REMOVED", "SERVICE_REMOVED", None, None, "service-d")
    ]


# ----------------------------------------------- meaningful change filtering


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s.replace("def list_orders():", "# the orders endpoint\ndef list_orders():"),
        lambda s: s.replace("return requests", 'print("listing orders")\n    return requests'),
        lambda s: s.replace("def list_orders():", "def   list_orders( ):"),
        lambda s: s.replace("    return requests", "    stock = requests").replace(
            ".json()", ".json()\n    return stock"
        ),
        lambda s: s.replace(
            "app = FastAPI()", "application = FastAPI()  # renamed variable"
        ).replace("@app.", "@application."),
    ],
    ids=["comment", "log-statement", "formatting", "local-variable", "module-variable-rename"],
)
def test_cosmetic_source_changes_are_not_architecture_changes(orders, edit):
    repo, first = orders
    edited = edit(ROUTES)
    assert edited != ROUTES
    second = repo.commit({"app/routes.py": edited})
    result = detect(repo, first, second)
    assert result.status is Status.NO_CHANGES and result.changes == []
    assert [(f.path, f.status) for f in result.changed_files] == [
        ("app/routes.py", "M")
    ]  # the code did change


def test_moving_code_to_another_line_is_not_a_change(orders):
    repo, first = orders
    second = repo.commit({"app/routes.py": "\n\n\n" + ROUTES})
    assert detect(repo, first, second).changes == []


# ------------------------------------------------------- unresolved information


def test_a_file_that_stops_parsing_yields_unresolved_not_false_removals(orders):
    repo, first = orders
    second = repo.commit({"app/routes.py": ROUTES + "\ndef broken(:\n"})
    result = detect(repo, first, second)
    assert result.changes == [] and result.status is Status.NO_CHANGES
    reasons = [(u.file, u.revision) for u in result.unresolved_items]
    assert ("app/routes.py", second) in reasons
    assert any(
        "GET /orders" in u.reason and "could not be analysed" in u.reason
        for u in result.unresolved_items
    )


def test_unknown_revision_and_non_git_directory_are_unresolved_not_exceptions(orders, tmp_path):
    repo, first = orders
    result = detect(repo, first, "no-such-revision")
    assert (
        result.status is Status.UNRESOLVED
        and "no-such-revision" in result.unresolved_items[0].reason
    )
    plain = tmp_path / "plain"
    plain.mkdir()
    assert detect_architecture_changes(plain, "a", "b").status is Status.UNRESOLVED


def test_a_call_to_an_undeclared_host_is_ignored_unless_the_service_is_known(orders):
    repo, first = orders
    routes = ROUTES + '\n\ndef sync():\n    requests.post("http://ledger/entries")\n'
    second = repo.commit({"app/routes.py": routes})
    assert detect(repo, first, second).changes == []
    known = detect(repo, first, second, known_services=["ledger"])
    assert {(c.category.value, c.target) for c in known.changes} == {
        ("SERVICE_ADDED", None),
        ("SERVICE_RELATIONSHIP_ADDED", "ledger"),
    }


# ----------------------------------------------------- isolation and history


def test_revisions_are_never_mixed(tmp_path):
    repo = GitRepo(tmp_path / "svc")

    def compose(*names):
        return "services:\n" + "".join(f"  {n}:\n    image: x/{n}\n" for n in names)

    def code(host):
        return f'import requests\nrequests.get("http://{host}/x")\n'

    first = repo.commit({"docker-compose.yml": compose("b", "c"), "client.py": code("b")}, "A -> B")
    second = repo.commit({"client.py": code("c")}, "A -> C")
    result = detect(repo, first, second)
    assert facts(result) == [
        ("ADDED", "SERVICE_RELATIONSHIP_ADDED", "svc", "c", "svc -[CALLS]-> c"),
        ("REMOVED", "SERVICE_RELATIONSHIP_REMOVED", "svc", "b", "svc -[CALLS]-> b"),
    ]
    reverse = detect(repo, second, first)
    assert [(c[0], c[3]) for c in facts(reverse)] == [("ADDED", "b"), ("REMOVED", "c")]
    # each revision's snapshot holds only its own call
    from src.architecture import build_snapshot

    def calls(sha):
        rels = build_snapshot(repo.path, sha).relationships
        return sorted(k[2] for k in rels if k[0] == "CALLS")

    assert calls(first) == ["service:b"] and calls(second) == ["service:c"]


def test_repositories_are_isolated_from_each_other(tmp_path):
    results = {}
    for name, host in (("alpha", "b"), ("beta", "c")):
        repo = GitRepo(tmp_path / name)
        files = {
            "docker-compose.yml": "services:\n  b:\n    image: x\n  c:\n    image: x\n",
            "k.py": f'import requests\nrequests.get("http://{host}/")\n',
        }
        first = repo.commit(files)
        second = repo.commit({"requirements.txt": "requests\n"})
        results[name] = detect(repo, first, second)
    assert {r.repository for r in results.values()} == {"alpha", "beta"}
    assert (
        [c.target for c in results["alpha"].changes]
        == ["requests"]
        == [c.target for c in results["beta"].changes]
    )
    assert results["alpha"].current_revision != results["beta"].current_revision


def test_uncommitted_work_is_ignored_and_the_working_tree_is_untouched(orders):
    repo, first = orders
    second = repo.commit({"README.md": "x\n"})
    dirty = repo.path / "app/routes.py"
    dirty.write_text(ROUTES + '\n@app.post("/uncommitted")\ndef x(): ...\n')
    (repo.path / "untracked.py").write_text("print()\n")
    before = repo.repo.git.status("--porcelain")
    result = detect(repo, first, second)
    assert result.changes == []  # the uncommitted route is not part of either revision
    assert repo.repo.git.status("--porcelain") == before
    assert "/uncommitted" in dirty.read_text()


def test_branch_and_tag_names_resolve_to_exact_commits(orders):
    repo, first = orders
    second = repo.commit({"requirements.txt": REQUIREMENTS + "httpx\n"})
    repo.repo.create_tag("v1", ref=first)
    repo.repo.git.branch("feature", second)
    result = detect(repo, "v1", "feature")
    assert (result.previous_revision, result.current_revision) == (first, second)
    assert [c.category for c in result.changes] == [C.DEPENDENCY_ADDED]


def test_cyclic_architecture_compares_correctly(tmp_path):
    repo = GitRepo(tmp_path / "ring")
    ring = (
        "services:\n  a:\n    depends_on: [b]\n"
        "  b:\n    depends_on: [c]\n  c:\n    depends_on: [a]\n"
    )
    first = repo.commit({"docker-compose.yml": ring})
    second = repo.commit({"docker-compose.yml": ring + "  d:\n    depends_on: [a]\n"})
    third = repo.commit(
        {"docker-compose.yml": ring.replace("depends_on: [a]", "depends_on: []", 1)}
    )
    assert {(c.category.value, c.name) for c in detect(repo, first, second).changes} == {
        ("SERVICE_ADDED", "d"),
        ("SERVICE_RELATIONSHIP_ADDED", "d -[DEPENDS_ON]-> a"),
    }
    assert [(c.change_type.value, c.name) for c in detect(repo, first, third).changes] == [
        ("REMOVED", "c -[DEPENDS_ON]-> a")
    ]
    assert detect(repo, first, first).changes == []


def test_detection_is_deterministic_and_serialisable(orders):
    repo, first = orders
    second = repo.commit(
        {
            "requirements.txt": REQUIREMENTS + "httpx\n",
            "app/routes.py": ROUTES + '\n@app.get("/h")\ndef h(): ...\n',
        }
    )
    one, two = detect(repo, first, second), detect(repo, first, second)
    assert json.dumps(one.to_dict(), sort_keys=True) == json.dumps(two.to_dict(), sort_keys=True)
    data = json.loads(json.dumps(one.to_dict()))
    assert data["repository"] == "orders" and data["summary"]["total"] == len(one.changes)
    assert {"change_type", "entity_type", "category", "evidence"} <= set(data["changes"][0])
    assert one.summary.by_category == {"API_ADDED": 1, "DEPENDENCY_ADDED": 1}
