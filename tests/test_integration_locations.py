from src.reasoning.facts import FactsProvider
from src.reasoning.integration.locations import LocationIndex, NormalizedFacts
from src.reasoning.retrieval.chunker import CodeChunk
from src.reasoning.schemas import Evidence, IssueUnderstanding


def chunk(file, name):
    return CodeChunk(id=f"{file}::{name}", file=file, name=name, kind="function",
                     start_line=1, end_line=2, text="")


def index(*pairs):
    idx = LocationIndex()
    idx.load([chunk(f, n) for f, n in pairs])
    return idx


def test_exact_location_unchanged():
    assert index(("billing/refunds.py", "is_eligible")).resolve("billing/refunds.py::is_eligible") \
        == "billing/refunds.py::is_eligible"


def test_basename_resolves_to_indexed_id():
    idx = index(("billing/refunds.py", "is_eligible"))
    assert idx.resolve("refunds.py::is_eligible") == "billing/refunds.py::is_eligible"


def test_absolute_path_resolves():
    idx = index(("billing/refunds.py", "Refund.is_eligible"))
    assert idx.resolve("/home/u/repo/billing/refunds.py::Refund.is_eligible") \
        == "billing/refunds.py::Refund.is_eligible"


def test_ambiguous_or_unknown_left_alone():
    idx = index(("a/utils.py", "run"), ("b/utils.py", "run"))
    assert idx.resolve("utils.py::run") == "utils.py::run"
    assert idx.resolve("other.py::run") == "other.py::run"
    assert idx.resolve("refunds.py") == "refunds.py" and idx.resolve(None) is None


def test_normalized_facts_rewrites_only_locations():
    class Inner:
        def collect(self, issue, repository, stack_trace):
            return [Evidence(source="rca", description="d", location="refunds.py::is_eligible"),
                    Evidence(source="git", description="no loc")]

    facts: FactsProvider = NormalizedFacts(Inner(), index(("billing/refunds.py", "is_eligible")))
    out = facts.collect(IssueUnderstanding(summary="x"), None, None)
    assert out[0].location == "billing/refunds.py::is_eligible" and out[0].description == "d"
    assert out[1].location is None
