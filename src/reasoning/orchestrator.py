"""Runs the agents in order. Each phase adds one step here."""
from src.api.models import AnalyzeRequest
from src.reasoning.agents.issue_agent import IssueAgent
from src.reasoning.facts import FactsProvider
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.schemas import AnalysisResult


class Orchestrator:
    def __init__(self, issue_agent: IssueAgent, facts: FactsProvider,
                 retriever: HybridRetriever | None = None, top_k: int = 5) -> None:
        self.issue_agent = issue_agent
        self.facts = facts
        self.retriever = retriever
        self.top_k = top_k

    def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        # Step 1: understand the issue (LLM)
        issue = self.issue_agent.run(request.issue, request.stack_trace, request.logs)

        # Step 2: collect facts (Person 1's deterministic engine)
        evidence = self.facts.collect(issue, request.repository, request.stack_trace)

        # Step 3 (Phase 2): hybrid retrieval over indexed code + graph
        if self.retriever is not None:
            retrieved = self.retriever.retrieve(issue, self.top_k)
            # deterministic facts keep priority; retrieved items are appended, skipping duplicates
            known = {f.location for f in evidence if f.location}
            evidence = evidence + [e for e in retrieved if e.location not in known]

        notes = []
        if not evidence:
            notes.append("No evidence yet: deterministic engine (Person 1) not connected.")
        notes.append("Root cause reasoning arrives in Phase 3.")

        return AnalysisResult(issue=issue, evidence=evidence, notes=notes)
