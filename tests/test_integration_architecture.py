from types import SimpleNamespace as NS

from src.reasoning.integration.architecture import GitArchitectureProvider, summarize


def change(category="API_ADDED", name="GET /refunds"):
    return NS(change_type=NS(value="ADDED"), category=NS(value=category), name=name,
              previous_state=None, current_state=None, source="svc", target="GET /refunds")


def result(status="CHANGES_DETECTED", changes=(), unresolved=()):
    by = {}
    for c in changes:
        by[c.category.value] = by.get(c.category.value, 0) + 1
    return NS(status=NS(value=status), previous_revision="a", current_revision="b", changes=list(changes),
              summary=NS(total=len(changes), by_category=by),
              unresolved_items=[NS(reason=r) for r in unresolved])


def test_summarize_changes():
    s = summarize(result(changes=[change(), change("DEPENDENCY_ADDED", "requests")]))
    assert s.status == "CHANGES_DETECTED" and "2 change(s)" in s.summary
    assert [c.name for c in s.changes] == ["GET /refunds", "requests"] and s.caveats


def test_summarize_no_changes_and_unresolved():
    assert "No architecture changes" in summarize(result("NO_CHANGES")).summary
    s = summarize(result("UNRESOLVED", unresolved=["revision x not found"]))
    assert s.unresolved == ["revision x not found"]


def test_provider_uses_indexed_repo_and_skips_without_one():
    seen = {}
    def detect(path, a, b):
        seen["args"] = (path, a, b)
        return result("NO_CHANGES")
    assert GitArchitectureProvider(lambda: "/repo", detect).compare(None, "a", "b").status == "NO_CHANGES"
    assert seen["args"] == ("/repo", "a", "b")
    assert GitArchitectureProvider(lambda: None, detect).compare(None, "a", "b").status == "SKIPPED"


def test_real_engine_reports_unresolved_for_non_git_dir(tmp_path):
    s = GitArchitectureProvider(lambda: str(tmp_path)).compare(None, "a", "b")
    assert s.status == "UNRESOLVED" and s.unresolved
