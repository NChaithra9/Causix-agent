"""Contract with Person 1 (deterministic engine).

Person 2 codes against FactsProvider. Until Person 1's engine is ready,
StubFactsProvider keeps the pipeline runnable end to end.
"""
from typing import Protocol

from src.reasoning.schemas import Evidence, IssueUnderstanding


class FactsProvider(Protocol):
    def collect(self, issue: IssueUnderstanding, repository: str | None,
                stack_trace: str | None) -> list[Evidence]: ...


class StubFactsProvider:
    def __init__(self, evidence: list[Evidence] | None = None) -> None:
        self.evidence = evidence or []

    def collect(self, issue, repository, stack_trace) -> list[Evidence]:
        return list(self.evidence)
