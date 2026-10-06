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


class RegressionTest(BaseModel):
    name: str
    code: str


class FixRecommendation(BaseModel):
    """Output of the Fix Agent (Phase 4). A recommendation only: Causix never edits code itself."""
    status: str                                   # "recommended" | "skipped"
    reason: str | None = None                     # why it was skipped
    location: str | None = None
    file: str | None = None
    summary: str | None = None
    explanation: str | None = None
    code_before: str | None = None
    code_after: str | None = None
    diff: str | None = None                       # unified diff, computed by Causix (not the LLM)
    regression_test: RegressionTest | None = None
    risks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    applied: bool = False                         # always False: human review first


class ImpactItem(BaseModel):
    name: str
    kind: str
    location: str | None = None
    depth: int = 1
    reason: str = ""


class ImpactExplanation(BaseModel):
    """Output of the Impact Agent (Phase 5). Groups come from the impact graph, never the LLM."""
    status: str                                   # "explained" | "skipped"
    reason: str | None = None
    changed_location: str | None = None
    risk_level: str = "low"                       # "low" | "medium" | "high"
    summary: str = ""
    directly_affected: list[ImpactItem] = Field(default_factory=list)
    potentially_affected: list[ImpactItem] = Field(default_factory=list)
    tests_to_run: list[ImpactItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    """Outcome of running a scenario in Person 1's sandbox. Only the runner sets this, never the LLM."""
    status: str                                   # "passed" | "failed" | "error" | "not_run"
    details: str = ""
    duration_ms: int | None = None


class TestScenario(BaseModel):
    """Output of the Scenario Agent (Phase 6): setup -> action -> expected, in business terms."""
    __test__ = False                              # not a pytest class
    status: str                                   # "generated" | "skipped"
    reason: str | None = None
    id: str | None = None
    title: str = ""
    setup: list[str] = Field(default_factory=list)
    action: str = ""
    expected: str = ""
    expected_error_code: str | None = None
    related_location: str | None = None
    regression_test: str | None = None            # name of the fix's regression test, if any
    execution: ExecutionResult | None = None      # filled by the sandbox runner (PASS/FAIL source)
    warnings: list[str] = Field(default_factory=list)


class DocumentationReport(BaseModel):
    """Output of the Documentation Agent (Phase 7): a readable RCA report in Markdown.
    Facts are rendered by code from the analysis; the LLM only adds the title, summary, lessons."""
    status: str                                   # "generated" | "skipped"
    reason: str | None = None
    title: str = ""
    summary: str = ""
    lessons: list[str] = Field(default_factory=list)
    markdown: str = ""
    warnings: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    issue: IssueUnderstanding
    evidence: list[Evidence] = Field(default_factory=list)
    root_cause: str | None = None    # filled in Phase 3 (RCA agent)
    rca: RootCauseAnalysis | None = None
    fix: FixRecommendation | None = None
    impact: ImpactExplanation | None = None
    scenario: TestScenario | None = None
    documentation: DocumentationReport | None = None
    notes: list[str] = Field(default_factory=list)
