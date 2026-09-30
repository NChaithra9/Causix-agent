"""End-to-end RCA pipeline test -- Phase 3, requirement 16's capstone case.

Builds a small fixture repo (reusing the existing Phase 2 sample-repo
builder: `payment/service.py` calling `payment/validator.py`), ingests it
into Neo4j, hands the top-level `investigate()` entry point a realistic
traceback, and verifies the full exception -> file -> line -> method ->
caller/callee -> git-history -> blame -> evidence pipeline runs end to end
and never invents anything it can't prove.

Needs a real, reachable Neo4j (see `neo4j_connection` in conftest.py --
skips cleanly otherwise).
"""

from __future__ import annotations

from src.graph import ingest_repository
from src.rca.investigator import investigate
from src.rca.models import EvidenceStatus, RCAInput, ResolutionStatus

from ._sample_repo import build_sample_repository

TRACEBACK = '''Traceback (most recent call last):
  File "payment/service.py", line 7, in process
    validate_payment()
  File "payment/validator.py", line 1, in validate_payment
ValueError: REFUND_NOT_ALLOWED
'''


def test_full_pipeline_from_traceback_to_evidence(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    rca_input = RCAInput(
        issue_id="ISSUE-1",
        stack_trace=TRACEBACK,
        repository=repo.info.name,
    )

    result = investigate(rca_input, connection=neo4j_connection, repo_root=tmp_path)

    # Stack trace parsed
    assert result.stack_trace.exception_type == "ValueError"
    assert len(result.stack_trace.frames) == 2

    # Exception -> code location resolved to the first (failing) frame
    assert result.location.resolution_status == ResolutionStatus.RESOLVED
    assert result.location.qualified_name == "PaymentService.process"
    assert result.location.node_label == "Method"

    # Code context gathered
    assert result.code_context.resolution_status == ResolutionStatus.RESOLVED
    assert result.code_context.containing_function == "process"
    assert result.code_context.containing_class == "PaymentService"

    # Call graph: process() calls validate_payment()
    assert result.call_graph_forward.resolution_status == ResolutionStatus.RESOLVED
    assert any(e.target_name == "validate_payment" for e in result.call_graph_forward.edges)

    # Reverse call graph from validate_payment would show process() as caller
    # (verified indirectly here through the forward edge above -- the reverse
    # direction is exercised directly in test_rca_call_graph.py).

    # Git history: this file has one commit, no changes "before" it (no
    # failure timestamp given) -- just verifies the pipeline pulled real Git facts.
    assert result.git_history.resolution_status == ResolutionStatus.RESOLVED
    assert result.git_history.last_modifying_commit is not None

    # Blame resolved for the failing line
    assert result.blame.resolution_status == ResolutionStatus.RESOLVED
    assert result.blame.commit_hash == result.git_history.last_modifying_commit.commit_hash

    # Evidence: numbered, non-empty, and contains at least one item per category
    assert len(result.evidence) > 0
    evidence_types = {e.type for e in result.evidence}
    assert "stack_trace" in evidence_types
    assert "code_location" in evidence_types
    assert "call_graph" in evidence_types
    assert "git_history" in evidence_types
    assert "blame" in evidence_types

    # Never asserts a root cause -- RCAResult has no such field, and every
    # piece of evidence is explicitly FACT or POTENTIALLY_RELEVANT, never a
    # bare unqualified causal claim.
    assert not hasattr(result, "root_cause")
    for item in result.evidence:
        assert item.status in (EvidenceStatus.FACT, EvidenceStatus.POTENTIALLY_RELEVANT, EvidenceStatus.UNRESOLVED)
        assert "caused" not in item.details.lower()


def test_pipeline_degrades_gracefully_with_only_an_exception_name(tmp_path, neo4j_connection):
    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    rca_input = RCAInput(exception_type="KeyError")

    result = investigate(rca_input, connection=neo4j_connection, repo_root=tmp_path)

    assert result.stack_trace.exception_type == "KeyError"
    assert result.location.resolution_status == ResolutionStatus.UNRESOLVED
    assert result.call_graph_forward is None
    assert result.git_history is None
    # Still produces evidence (the parsed exception itself), never crashes.
    assert any(e.type == "stack_trace" for e in result.evidence)
    assert any(e.type == "code_location" and e.status == EvidenceStatus.UNRESOLVED for e in result.evidence)


def test_facts_provider_adapter_translates_to_reasoning_evidence_contract(tmp_path, neo4j_connection):
    from src.rca.facts_provider import RCAFactsProvider
    from src.reasoning.schemas import IssueUnderstanding

    repo = build_sample_repository(tmp_path)
    ingest_repository(neo4j_connection, repo)

    provider = RCAFactsProvider(neo4j_connection, tmp_path)
    issue = IssueUnderstanding(summary="refund failed", error_type="ValueError")

    evidence = provider.collect(issue, repo.info.name, TRACEBACK)

    assert len(evidence) > 0
    located = [e for e in evidence if e.location]
    assert any("::" in e.location for e in located)
