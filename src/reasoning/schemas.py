"""Domain models shared by the reasoning side (Person 2) and the facts side (Person 1)."""
from pydantic import BaseModel, Field


class IssueUnderstanding(BaseModel):
    """What the Issue Agent understood from the user's report."""
    summary: str
    error_type: str | None = None
    suspected_component: str | None = None
    keywords: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """One fact from the deterministic engine (Person 1). This is the contract between the two tracks."""
    source: str                      # e.g. "stack_trace", "graph", "git"
    description: str
    location: str | None = None      # e.g. "refund_service.py::RefundService.is_eligible_for_refund"
    score: float | None = None       # relevance score, set by retrieval (Phase 2)


class RootCauseAnalysis(BaseModel):
    """Output of the RCA Agent (Phase 3). Always grounded in the evidence it was given."""
    root_cause: str
    confidence: str = "low"                       # "low" | "medium" | "high"
    affected_component: str | None = None
    location: str | None = None                   # must be one of the evidence locations
    reasoning: str = ""
    suggested_fix: str | None = None
    insufficient_evidence: bool = False
    supporting_evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)   # what the grounding checks corrected


class AnalysisResult(BaseModel):
    issue: IssueUnderstanding
    evidence: list[Evidence] = Field(default_factory=list)
    root_cause: str | None = None    # filled in Phase 3 (RCA agent)
    rca: RootCauseAnalysis | None = None
    notes: list[str] = Field(default_factory=list)
