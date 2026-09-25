"""Integration test for `src.repository.build_repository_facts` -- Phase 1, requirement 8.

Confirms the full pipeline (ingestion -> file extraction -> parsing ->
relationships -> git history) assembles correctly into one `Repository`,
without re-testing the individual pieces already covered elsewhere.
"""

from __future__ import annotations

from pathlib import Path

from git import Repo

from src.parser.models import RelationshipType
from src.repository import build_repository_facts


def make_sample_repo(path: Path) -> None:
    repo = Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test Author")
        config.set_value("user", "email", "test@example.com")

    (path / "payment.py").write_text("def get_payment():\n    pass\n")
    (path / "refund.py").write_text(
        "from payment import get_payment\n\n\ndef refund():\n    get_payment()\n"
    )
    repo.index.add(["payment.py", "refund.py"])
    repo.index.commit("Initial implementation")


def test_build_repository_facts_assembles_the_full_pipeline(tmp_path):
    make_sample_repo(tmp_path)

    facts = build_repository_facts(tmp_path)

    # Repository ingestion
    assert facts.info.is_git_repository is True
    assert facts.info.root_path == str(tmp_path.resolve())

    # File extraction
    assert {f.relative_path for f in facts.source_files} == {"payment.py", "refund.py"}

    # Python parsing
    refund_file = next(f for f in facts.files if f.file_path == "refund.py")
    assert [f.name for f in refund_file.functions] == ["refund"]
    assert [imp.qualified_name for imp in refund_file.imports] == ["payment.get_payment"]

    # Relationships, including cross-file call resolution
    calls = [r for r in facts.relationships if r.relationship_type == RelationshipType.CALLS]
    assert any(
        r.source == "refund" and r.target == "payment.get_payment" and r.resolved
        for r in calls
    )

    # Git history
    assert len(facts.git_history.commits) == 1
    assert facts.git_history.commits[0].message == "Initial implementation"
    modifies = facts.git_history.file_changes
    assert {r.target for r in modifies} == {"payment.py", "refund.py"}
    assert all(r.relationship_type == RelationshipType.MODIFIES for r in modifies)
