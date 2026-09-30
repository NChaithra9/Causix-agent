"""Evidence construction for Phase 4 -- "why this location is relevant"
(requirement 8), built entirely from facts already established elsewhere in
this package (and reused directly from the Phase 3 RCAResult this
localization was built from). No new facts are produced here.
"""

from __future__ import annotations

from src.rca.models import Evidence, EvidenceStatus, ResolutionStatus

from .models import CandidateLocation, HistoricalChange, LocationRole, RelatedTest

__all__ = [
    "candidate_location_evidence",
    "relevant_code_evidence",
    "historical_change_evidence",
    "related_test_evidence",
]


def candidate_location_evidence(candidate: CandidateLocation) -> list[Evidence]:
    items: list[Evidence] = []
    loc = candidate.location
    role_word = "Primary" if candidate.role == LocationRole.PRIMARY else "Related"
    items.append(
        Evidence(
            type="candidate_location",
            source="graph",
            status=EvidenceStatus.FACT,
            details=f"{role_word} location: {candidate.reason} -> {loc.qualified_name} ({loc.file_path}:{loc.line})",
            repository=loc.repository,
            file=loc.file_path,
            line=loc.line,
            method=loc.qualified_name,
        )
    )

    method_loc = candidate.method_localization
    if method_loc and method_loc.resolution_status == ResolutionStatus.RESOLVED:
        owner = f"{method_loc.class_name}." if method_loc.class_name else ""
        items.append(
            Evidence(
                type="method_localization",
                source="parser",
                status=EvidenceStatus.FACT,
                details=f"Line {loc.line} belongs to {owner}{method_loc.name}() "
                f"(lines {method_loc.start_line}-{method_loc.end_line})",
                file=method_loc.file_path,
                line=loc.line,
                method=method_loc.qualified_name,
            )
        )

    class_loc = candidate.class_localization
    if class_loc and class_loc.resolution_status == ResolutionStatus.RESOLVED:
        items.append(
            Evidence(
                type="class_localization",
                source="parser",
                status=EvidenceStatus.FACT,
                details=f"Owning class: {class_loc.name} (lines {class_loc.start_line}-{class_loc.end_line})",
                file=class_loc.file_path,
                method=class_loc.name,
            )
        )
    return items


def relevant_code_evidence(candidate: CandidateLocation) -> list[Evidence]:
    code = candidate.relevant_code
    if code is None or code.resolution_status != ResolutionStatus.RESOLVED:
        return []
    return [
        Evidence(
            type="relevant_code",
            source="repository",
            status=EvidenceStatus.FACT,
            details=f"Retrieved lines {code.start_line}-{code.end_line} of {code.file_path} "
            f"({len(code.lines)} lines) from the actual repository source",
            file=code.file_path,
            line=code.start_line,
        )
    ]


def historical_change_evidence(changes: list[HistoricalChange]) -> list[Evidence]:
    items: list[Evidence] = []
    for change in changes:
        status = EvidenceStatus.FACT if change.label == "related_commit" else EvidenceStatus.POTENTIALLY_RELEVANT
        items.append(
            Evidence(
                type=change.label,
                source="git",
                status=status,
                details=f"[{change.label}] {change.commit_hash[:12]} by {change.author_name}: {change.message}"
                + (f" (scoped to {change.method})" if change.method else ""),
                file=change.file_path,
                commit=change.commit_hash,
                timestamp=change.committed_at,
                method=change.method,
            )
        )
    return items


def related_test_evidence(tests: list[RelatedTest]) -> list[Evidence]:
    return [
        Evidence(
            type="related_test",
            source="graph",
            status=EvidenceStatus.FACT,
            details=f"{test.test_file}::{test.test_function} references {test.target}",
            file=test.test_file,
            method=test.test_function,
        )
        for test in tests
    ]
