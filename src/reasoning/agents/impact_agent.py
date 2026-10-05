"""Impact Agent: explains what a planned change affects.

Person 1's impact data decides WHAT is affected (and how deep). This agent only decides
how to explain it. Grouping is deterministic; the LLM adds a summary, a risk level and
a one-line reason per item, and any reference to an unknown item is dropped.
"""
from pydantic import BaseModel, Field, ValidationError

from src.reasoning.agents.issue_agent import AgentError, extract_json
from src.reasoning.impact import ImpactGraph, ImpactNode
from src.reasoning.llm import LLMClient
from src.reasoning.prompts import IMPACT_SYSTEM, build_impact_prompt
from src.reasoning.schemas import FixRecommendation, ImpactExplanation, ImpactItem

RISK_LEVELS = {"low", "medium", "high"}


class _RawImpact(BaseModel):
    summary: str
    risk_level: str = "low"
    reasons: dict[str, str] = Field(default_factory=dict)


def skipped(reason: str, location: str | None = None) -> ImpactExplanation:
    return ImpactExplanation(status="skipped", reason=reason, changed_location=location)


def to_item(node: ImpactNode, reason: str | None) -> ImpactItem:
    default = f"{node.relation} the changed code" if node.depth == 1 else \
        f"{node.relation} affected code ({node.depth} hops away)"
    return ImpactItem(name=node.name, kind=node.kind, location=node.location,
                      depth=node.depth, reason=reason or default)


def group(nodes: list[ImpactNode], reasons: dict[str, str]):
    """Every node lands in exactly one group: tests, direct (depth 1) or potential (depth 2+)."""
    direct, potential, tests = [], [], []
    for i, node in enumerate(nodes, start=1):
        item = to_item(node, reasons.get(f"N{i}"))
        if node.kind == "test":
            tests.append(item)
        elif node.depth <= 1:
            direct.append(item)
        else:
            potential.append(item)
    return direct, potential, tests


class ImpactAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(self, fix: FixRecommendation | None, graph: ImpactGraph) -> ImpactExplanation:
        if fix is None or fix.status != "recommended":
            return skipped("No recommended fix, so there is no planned change to assess.")

        nodes = graph.nodes
        if not nodes:
            return ImpactExplanation(
                status="explained", changed_location=graph.changed, risk_level="low",
                summary="No callers, dependents or tests were found for the changed code.",
                warnings=["No tests cover this change; add the regression test before merging."])

        raw_text = self.llm.complete(IMPACT_SYSTEM,
                                     build_impact_prompt(graph.changed, fix.summary, nodes))
        try:
            raw = _RawImpact(**extract_json(raw_text))
        except ValidationError as exc:
            raise AgentError(f"Impact JSON did not match the expected shape: {exc}") from exc

        warnings: list[str] = []
        valid_ids = {f"N{i}" for i in range(1, len(nodes) + 1)}
        reasons = {}
        for key, text in raw.reasons.items():
            if key.strip().upper() in valid_ids:
                reasons[key.strip().upper()] = text
            else:
                warnings.append(f"Dropped reason for unknown item {key}.")

        risk = raw.risk_level.strip().lower()
        if risk not in RISK_LEVELS:
            warnings.append(f"Unknown risk level '{raw.risk_level}', using 'medium'.")
            risk = "medium"

        direct, potential, tests = group(nodes, reasons)
        if not tests:
            warnings.append("No tests cover this change; add the regression test before merging.")

        return ImpactExplanation(
            status="explained", changed_location=graph.changed, risk_level=risk,
            summary=raw.summary, directly_affected=direct, potentially_affected=potential,
            tests_to_run=tests, warnings=warnings)
