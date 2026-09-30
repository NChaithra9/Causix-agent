"""Adapter satisfying Person 2's ``FactsProvider`` contract (``src.reasoning.facts``)
with this package's real, deterministic RCA engine -- Phase 3's integration
seam. This is the only file in Phase 3 that imports from ``src.reasoning``,
and it only *reads* that module's Protocol/model shapes; nothing in
``src.reasoning`` is modified.

Not wired into ``src.api.main`` here -- that FastAPI wiring is Person 2's
file. See the Phase 3 report for the one-line change that plugs this in.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.graph.connection import Neo4jConnection
from src.reasoning.schemas import Evidence as ReasoningEvidence
from src.reasoning.schemas import IssueUnderstanding

from .investigator import investigate
from .models import RCAInput

__all__ = ["RCAFactsProvider"]


class RCAFactsProvider:
    """Concrete ``FactsProvider``: runs the deterministic RCA engine and
    translates its rich internal evidence into Person 2's ``Evidence``
    contract (``source``/``description``/``location``/``score``).

    ``location`` is rendered as ``"<file>::<qualified_name>"`` when both are
    known -- matching the exact format ``src.reasoning.schemas.Evidence``
    already documents (e.g. ``"refund_service.py::RefundService.process_refund"``).
    ``score`` is always left ``None``: Phase 3 doesn't invent a numeric
    confidence, per the spec's explicit instruction not to (see
    ``src.rca.models.EvidenceStatus`` instead, folded into ``description``).
    """

    def __init__(self, connection: Neo4jConnection, repo_root: str | Path) -> None:
        self._connection = connection
        self._repo_root = repo_root

    def collect(
        self, issue: IssueUnderstanding, repository: str | None, stack_trace: str | None
    ) -> list[ReasoningEvidence]:
        rca_input = RCAInput(
            error_message=issue.summary,
            exception_type=issue.error_type,
            stack_trace=stack_trace,
            repository=repository or issue.suspected_component,
            timestamp=datetime.now().astimezone(),
        )
        result = investigate(rca_input, connection=self._connection, repo_root=self._repo_root)
        return [self._to_reasoning_evidence(e) for e in result.evidence]

    @staticmethod
    def _to_reasoning_evidence(item) -> ReasoningEvidence:
        location = None
        if item.file:
            file_name = item.file.rsplit("/", 1)[-1]
            location = f"{file_name}::{item.method}" if item.method else file_name
        return ReasoningEvidence(
            source=item.type,
            description=f"[{item.status.value}] {item.details}",
            location=location,
            score=None,
        )
