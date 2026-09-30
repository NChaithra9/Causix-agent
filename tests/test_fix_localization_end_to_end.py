"""End-to-end Phase 4 test -- Phase 4, requirements 17 & 18's capstone case.

Reuses the exact fixture the spec itself describes: a payment/service.py
calling payment/validator.py, built from the existing Phase 2 sample-repo
fixture (already has this shape and a real test file). Feeds a traceback
through Phase 3's investigate() first, then Phase 4's localize_fix() over
that result, and verifies the whole exception -> exact repository/file/
class/method/lines -> relevant code -> historical evidence -> related
tests chain.

Needs a real, reachable Neo4j (skips cleanly otherwise, per conftest.py's
neo4j_connection fixture).
"""

from __future__ import annotations

from src.fix_localization import localize_fix
from src.fix_localization.models import LocationRole
from src.graph import ingest_repository
from src.rca.investigator import investigate
from src.rca.models import RCAInput, ResolutionStatus

from ._sample_repo import build_sample_repository

TRACEBACK = '''Traceback (most recent call last):
  File "payment/service.py", line 7, in process
    validate_payment()
  File "payment/validator.py", line 1, in validate_payment
ValueError: REFUND_NOT_ALLOWED
'''


def test_full_localization_pipeline(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    rca_input = RCAInput(stack_trace=TRACEBACK, repository=repo.info.name)
    rca_result = investigate(rca_input, connection=neo4j_connection, repo_root=tmp_path)

    result = localize_fix(rca_result, connection=neo4j_connection, repo_root=tmp_path)

    assert result.resolution_status == ResolutionStatus.RESOLVED

    # Primary location: PaymentService.process (the first/failing frame)
    primary = result.primary_location
    assert primary.role == LocationRole.PRIMARY
    assert primary.location.qualified_name == "PaymentService.process"
    assert primary.repository_identity.name == repo.info.name
    assert primary.repository_identity.repository_id is not None

    # Class + method localization with real line ranges
    assert primary.class_localization.name == "PaymentService"
    assert primary.method_localization.name == "process"
    assert primary.method_localization.class_name == "PaymentService"
    assert primary.method_localization.start_line is not None
    assert primary.method_localization.end_line is not None

    # Relevant code is the actual method body, not the whole file
    assert primary.relevant_code.resolution_status == ResolutionStatus.RESOLVED
    assert any("validate_payment" in line for line in primary.relevant_code.lines)

    # Related location: the second frame (validate_payment itself)
    related_names = {c.location.qualified_name for c in result.related_locations}
    assert "validate_payment" in related_names
    for c in result.related_locations:
        assert c.role == LocationRole.RELATED

    # Historical changes reused from Phase 3's git facts
    assert len(result.historical_changes) >= 1
    assert all(c.label in {"related_commit", "previous_change", "historical_change"} for c in result.historical_changes)

    # Related tests: the sample repo's tests/test_service.py calls PaymentService().process()
    assert any(t.test_file == "tests/test_service.py" for t in result.related_tests)

    # No PR/Jira integration exists yet -- must be empty, never invented
    assert result.related_prs == []
    assert result.related_jira == []

    # Evidence includes Phase 3's reused evidence plus Phase 4's own
    evidence_types = {e.type for e in result.evidence}
    assert "candidate_location" in evidence_types
    assert "method_localization" in evidence_types
    assert "relevant_code" in evidence_types
    assert "stack_trace" in evidence_types  # reused from Phase 3

    # Never a recommendation of any kind
    assert not hasattr(result, "recommended_fix")
    for item in result.evidence:
        assert "should" not in item.details.lower()


def test_unresolved_rca_yields_unresolved_localization(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    rca_input = RCAInput(exception_type="KeyError")  # no stack trace at all
    rca_result = investigate(rca_input, connection=neo4j_connection, repo_root=tmp_path)

    result = localize_fix(rca_result, connection=neo4j_connection, repo_root=tmp_path)

    assert result.resolution_status == ResolutionStatus.UNRESOLVED
    assert result.primary_location is None
    assert result.related_locations == []
