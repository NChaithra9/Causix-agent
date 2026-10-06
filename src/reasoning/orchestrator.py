"""Runs the agents in order. Each phase adds one step here."""
from src.api.models import AnalyzeRequest
from src.reasoning.agents.documentation_agent import DocumentationAgent
from src.reasoning.agents.documentation_agent import skipped as skip_docs
from src.reasoning.agents.fix_agent import FixAgent
from src.reasoning.agents.impact_agent import ImpactAgent
from src.reasoning.agents.impact_agent import skipped as skip_impact
from src.reasoning.agents.issue_agent import AgentError, IssueAgent
from src.reasoning.agents.rca_agent import RCAAgent
from src.reasoning.agents.scenario_agent import ScenarioAgent
from src.reasoning.agents.scenario_agent import skipped as skip_scenario
from src.reasoning.execution import ScenarioRunner
from src.reasoning.facts import FactsProvider
from src.reasoning.impact import ImpactProvider
from src.reasoning.retrieval.hybrid import HybridRetriever
from src.reasoning.schemas import AnalysisResult, ExecutionResult


class Orchestrator:
    def __init__(self, issue_agent: IssueAgent, facts: FactsProvider,
                 retriever: HybridRetriever | None = None, top_k: int = 5,
                 rca_agent: RCAAgent | None = None, fix_agent: FixAgent | None = None,
                 impact_agent: ImpactAgent | None = None,
                 impact_provider: ImpactProvider | None = None,
                 scenario_agent: ScenarioAgent | None = None,
                 scenario_runner: ScenarioRunner | None = None,
                 documentation_agent: DocumentationAgent | None = None,
                 architecture_provider=None) -> None:
        self.issue_agent = issue_agent
        self.facts = facts
        self.retriever = retriever
        self.top_k = top_k
        self.rca_agent = rca_agent
        self.fix_agent = fix_agent
        self.impact_agent = impact_agent
        self.impact_provider = impact_provider
        self.scenario_agent = scenario_agent
        self.scenario_runner = scenario_runner
        self.documentation_agent = documentation_agent
        self.architecture_provider = architecture_provider

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

        # Step 4 (Phase 3): root cause reasoning over the evidence
        rca = None
        if self.rca_agent is None:
            notes.append("Root cause reasoning arrives in Phase 3.")
        else:
            try:
                rca = self.rca_agent.run(issue, evidence, request.stack_trace, request.logs)
            except AgentError as exc:
                # keep the evidence even if reasoning fails
                notes.append(f"Root cause analysis failed: {exc}")

        # Step 5 (Phase 4): fix recommendation for the located root cause
        fix = None
        if self.fix_agent is not None:
            lookup = self.retriever.get_chunk if self.retriever is not None else (lambda _loc: None)
            try:
                fix = self.fix_agent.run(issue, rca, lookup)
            except AgentError as exc:
                notes.append(f"Fix recommendation failed: {exc}")

        # Step 6 (Phase 5): explain the impact of the recommended change
        impact = None
        if self.impact_agent is not None and self.impact_provider is not None:
            try:
                if fix is not None and fix.status == "recommended" and fix.location:
                    impact = self.impact_agent.run(fix, self.impact_provider.impact_of(fix.location))
                else:
                    impact = skip_impact("No recommended fix, so there is no planned change to assess.")
            except AgentError as exc:
                notes.append(f"Impact explanation failed: {exc}")

        # Step 7 (Phase 6): business test scenario; PASS/FAIL comes only from the sandbox runner
        scenario = None
        if self.scenario_agent is not None:
            try:
                scenario = self.scenario_agent.run(issue, rca, fix)
                if scenario.status == "generated" and self.scenario_runner is not None:
                    try:
                        scenario.execution = self.scenario_runner.run(scenario, fix)
                    except Exception as exc:  # sandbox failure must not lose the scenario
                        notes.append(f"Scenario execution failed: {exc}")
                        scenario.execution = ExecutionResult(status="error", details=str(exc))
            except AgentError as exc:
                notes.append(f"Scenario generation failed: {exc}")
                scenario = skip_scenario("Scenario generation failed.")

        result = AnalysisResult(issue=issue, evidence=evidence, notes=notes, rca=rca, fix=fix,
                              impact=impact, scenario=scenario,
                              root_cause=None if rca is None or rca.insufficient_evidence
                              else rca.root_cause)

        # Step 8 (Phase 8): architecture changes between two revisions (Person 1's Phase 7 engine)
        if self.architecture_provider is not None and request.previous_revision and request.current_revision:
            try:
                result.architecture = self.architecture_provider.compare(
                    request.repository, request.previous_revision, request.current_revision)
            except Exception as exc:  # engine problems must not lose the analysis
                result.notes.append(f"Architecture comparison failed: {exc}")

        # Step 9 (Phase 7): incident report written from everything above
        if self.documentation_agent is not None:
            try:
                result.documentation = self.documentation_agent.run(result)
            except AgentError as exc:
                result.notes.append(f"Documentation failed: {exc}")
                result.documentation = skip_docs("Documentation generation failed.")
        return result
