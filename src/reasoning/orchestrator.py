"""Runs the agents in order. Each phase adds one step here."""
from src.api.models import AnalyzeRequest
from src.reasoning.agents.issue_agent import IssueAgent
from src.reasoning.facts import FactsProvider
from src.reasoning.schemas import AnalysisResult


class Orchestrator:
    def __init__(self, issue_agent: IssueAgent, facts: FactsProvider) -> None:
        self.issue_agent = issue_agent
        self.facts = facts

    def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        # Step 1: understand the issue (LLM)
        issue = self.issue_agent.run(request.issue, request.stack_trace, request.logs)

        # Step 2: collect facts (Person 1's deterministic engine)
        evidence = self.facts.collect(issue, request.repository, request.stack_trace)

        notes = []
        if not evidence:
            notes.append("No evidence yet: deterministic engine (Person 1) not connected.")
        notes.append("Root cause reasoning arrives in Phase 3.")

        return AnalysisResult(issue=issue, evidence=evidence, notes=notes)
