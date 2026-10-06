"""Facts from Person 1's RCA engine (Phase 3) and fix localization engine (Phase 4)."""
from collections.abc import Callable
from datetime import datetime

from src.reasoning.schemas import Evidence, IssueUnderstanding


def to_evidence(item) -> Evidence:
    """Engine evidence -> the shared Evidence contract (full file path kept; see NormalizedFacts)."""
    location = None
    if item.file:
        location = f"{item.file}::{item.method}" if item.method else item.file
    return Evidence(source=item.type, description=f"[{item.status.value}] {item.details}",
                    location=location, score=None)


class EngineFacts:
    """Runs ``investigate`` then ``localize_fix`` and returns the combined evidence.

    ``last_localization`` keeps the newest localization result (its related tests are used when
    validating a fix). Imports are lazy so the package works without Neo4j installed.
    """

    def __init__(self, connection, repo_root: Callable[[], str | None]) -> None:
        self.connection = connection
        self.repo_root = repo_root
        self.last_localization = None

    def collect(self, issue: IssueUnderstanding, repository: str | None,
                stack_trace: str | None) -> list[Evidence]:
        from src.fix_localization import ResolutionStatus, localize_fix
        from src.rca import RCAInput, investigate

        root = self.repo_root()
        if not root:
            return []   # nothing indexed yet: the orchestrator records "no evidence"
        rca_input = RCAInput(error_message=issue.summary, exception_type=issue.error_type,
                             stack_trace=stack_trace, repository=repository or issue.suspected_component,
                             timestamp=datetime.now().astimezone())
        rca = investigate(rca_input, connection=self.connection, repo_root=root)
        loc = localize_fix(rca, connection=self.connection, repo_root=root)
        self.last_localization = loc
        items = rca.evidence if loc.resolution_status == ResolutionStatus.UNRESOLVED else loc.evidence
        evidence = [to_evidence(i) for i in items]

        primary = loc.primary_location.method_localization if loc.primary_location else None
        if primary and primary.file_path and primary.qualified_name:
            span = f"lines {primary.start_line}-{primary.end_line}" if primary.start_line else "lines unknown"
            evidence.insert(0, Evidence(
                source="fix_localization", location=f"{primary.file_path}::{primary.qualified_name}",
                description=f"[KNOWN] exact method to change ({span}"
                            + (f", failing line {primary.failing_line}" if primary.failing_line else "") + ")"))
        return evidence
