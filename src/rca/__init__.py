"""Phase 3: deterministic Root Cause Analysis (RCA) engine.

    Input -> Parser -> Neo4j -> Git -> Deterministic Traversal -> Evidence

No LLM anywhere in this package. Given a (possibly minimal) description of
a failure, this package deterministically:

  1. parses the stack trace (``stack_trace``),
  2. resolves the failure to a real location in the Phase 1/2 code graph
     (``code_resolution``) and gathers surrounding source context
     (``code_context``),
  3. traverses the CALLS graph forward and in reverse (``call_graph``),
  4. traverses IMPORTS relationships for related components (``dependencies``),
  5. pulls recent commits, changes before the failure, and blame from Git
     (``git_investigation``, reusing ``src.git_history``),
  6. correlates any supplied logs by explicit rule (``log_correlation``),

and assembles everything into a numbered, structured evidence list
(``evidence`` -> :class:`~src.rca.models.RCAResult`). It never asserts a
root cause -- that reasoning step belongs to Person 2's later LLM agent,
which consumes this package's output through :class:`RCAFactsProvider`
(``src.reasoning.facts.FactsProvider``).

Public API:
    RCAInput, RCAResult, ResolutionStatus, EvidenceStatus, Evidence and its
    per-category subclasses (see ``models.py``)
    investigate(rca_input, connection=..., repo_root=...) -> RCAResult
    RCAFactsProvider(connection, repo_root) -- satisfies src.reasoning.facts.FactsProvider
"""

from .facts_provider import RCAFactsProvider
from .investigator import investigate
from .models import (
    BlameEvidence,
    BlameResult,
    CallGraphEdge,
    CallGraphEvidence,
    CallGraphResult,
    CodeContext,
    CodeLocation,
    CodeLocationEvidence,
    DependencyEvidence,
    DependencyResult,
    Evidence,
    EvidenceStatus,
    GitHistoryEvidence,
    GitHistoryResult,
    LogCorrelation,
    LogEntry,
    LogEvidence,
    ParsedStackTrace,
    RCAInput,
    RCAResult,
    RecentChange,
    RecentChangeEvidence,
    ResolutionStatus,
    StackFrame,
    StackTraceEvidence,
)

__all__ = [
    "BlameEvidence",
    "BlameResult",
    "CallGraphEdge",
    "CallGraphEvidence",
    "CallGraphResult",
    "CodeContext",
    "CodeLocation",
    "CodeLocationEvidence",
    "DependencyEvidence",
    "DependencyResult",
    "Evidence",
    "EvidenceStatus",
    "GitHistoryEvidence",
    "GitHistoryResult",
    "LogCorrelation",
    "LogEntry",
    "LogEvidence",
    "ParsedStackTrace",
    "RCAFactsProvider",
    "RCAInput",
    "RCAResult",
    "RecentChange",
    "RecentChangeEvidence",
    "ResolutionStatus",
    "StackFrame",
    "StackTraceEvidence",
    "investigate",
]
