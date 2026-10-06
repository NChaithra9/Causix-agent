import subprocess
from types import SimpleNamespace

from src.reasoning.agents.fix_agent import make_diff
from src.reasoning.integration.validation_runner import (ValidationScenarioRunner, map_status,
                                                         new_file_patch, test_path_for)
from src.reasoning.retrieval.chunker import CodeChunk
from src.reasoning.schemas import FixRecommendation, RegressionTest, TestScenario

SOURCE = "def is_eligible(order):\n    return order.amount > 0\n"
CHUNK = CodeChunk(id="billing/refunds.py::is_eligible", file="billing/refunds.py", name="is_eligible",
                  kind="function", start_line=1, end_line=2, text=SOURCE)
AFTER = "def is_eligible(order):\n    if order is None:\n        return False\n    return order.amount > 0\n"
SC = TestScenario(status="generated", id="SC-abc123", title="t", action="a", expected="e")


def fix(**kw):
    base = dict(status="recommended", location=CHUNK.location, file=CHUNK.file, summary="s",
                diff=make_diff(CHUNK, SOURCE.rstrip("\n"), AFTER.rstrip("\n")),
                regression_test=RegressionTest(name="test_none", code="def test_none():\n    assert True\n"))
    return FixRecommendation(**{**base, **kw})


class FakeOrch:
    def __init__(self, status="PASSED"):
        self.status, self.request = status, None

    def validate(self, request):
        self.request = request
        ev = SimpleNamespace(summary=lambda: "Validation: " + self.status)
        return SimpleNamespace(status=SimpleNamespace(value=self.status), duration=1.5, evidence=ev)


def runner(orch, repo="/repo", docker=True):
    return ValidationScenarioRunner(lambda: repo, orchestrator=orch, docker_check=lambda: docker)


def test_status_mapping_is_strict():
    assert map_status("PASSED") == "passed" and map_status("FAILED") == "failed"
    for other in ["TIMEOUT", "BUILD_FAILED", "STARTUP_FAILED", "INFRASTRUCTURE_FAILED", "ERROR"]:
        assert map_status(other) == "error"


def test_runs_regression_test_and_maps_result():
    orch = FakeOrch("FAILED")
    res = runner(orch).run(SC, fix())
    assert res.status == "failed" and res.duration_ms == 1500 and "FAILED" in res.details
    req = orch.request
    assert req.repository == "/repo" and req.test_commands == (f"python -m pytest -q {test_path_for(SC, fix())}",)
    assert "tests/causix_regression/test_sc_abc123.py" in req.patch and req.target_location == CHUNK.location


def test_not_run_when_nothing_to_execute():
    for f, repo, docker in [(fix(diff=None), "/r", True), (fix(regression_test=None), "/r", True),
                            (fix(), None, True), (fix(), "/r", False)]:
        orch = FakeOrch()
        assert runner(orch, repo, docker).run(SC, f).status == "not_run" and orch.request is None


def test_patch_really_applies_with_git(tmp_path):
    repo = tmp_path / "r"
    (repo / "billing").mkdir(parents=True)
    (repo / "billing" / "refunds.py").write_text(SOURCE)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    f = fix()
    patch = f.diff.rstrip("\n") + "\n" + new_file_patch(test_path_for(SC, f), f.regression_test.code)
    (tmp_path / "p.patch").write_text(patch)
    subprocess.run(["git", "apply", "--check", str(tmp_path / "p.patch")], cwd=repo, check=True)
    subprocess.run(["git", "apply", str(tmp_path / "p.patch")], cwd=repo, check=True)
    assert "return False" in (repo / "billing" / "refunds.py").read_text()
    assert (repo / test_path_for(SC, f)).read_text() == f.regression_test.code
