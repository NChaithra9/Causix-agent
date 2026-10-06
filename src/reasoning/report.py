"""Deterministic Markdown rendering of an analysis. No LLM: every fact comes from the result."""
from src.reasoning.schemas import AnalysisResult


def _items(heading: str, items) -> list[str]:
    if not items:
        return []
    return [f"**{heading}**"] + [f"- `{i.name}` ({i.kind}): {i.reason}" for i in items] + [""]


def render_report(result: AnalysisResult, title: str, summary: str, lessons: list[str]) -> str:
    L: list[str] = [f"# {title}", "", summary, "", "## Issue", result.issue.summary, ""]
    rca = result.rca
    if rca:
        L += ["## Root cause", rca.root_cause, "",
              f"- Confidence: {rca.confidence}", f"- Location: `{rca.location or 'unknown'}`"]
        if rca.affected_component:
            L.append(f"- Component: {rca.affected_component}")
        L.append("")
        if rca.supporting_evidence:
            L += ["### Evidence"] + [
                f"- [{e.source}] {e.description}" + (f" (`{e.location}`)" if e.location else "")
                for e in rca.supporting_evidence] + [""]
    fix = result.fix
    if fix and fix.status == "recommended":
        L += ["## Recommended fix", fix.summary or "", ""]
        if fix.explanation:
            L += [fix.explanation, ""]
        if fix.diff:
            L += ["```diff", fix.diff.rstrip(), "```", ""]
        if fix.regression_test:
            L += [f"Regression test: `{fix.regression_test.name}`", ""]
        if fix.risks:
            L += ["Risks:"] + [f"- {r}" for r in fix.risks] + [""]
        L += ["_Not applied: a human reviews and applies this change._", ""]
    impact = result.impact
    if impact and impact.status == "explained":
        L += ["## Impact", f"Risk level: **{impact.risk_level}**", "", impact.summary, ""]
        L += _items("Directly affected", impact.directly_affected)
        L += _items("Potentially affected", impact.potentially_affected)
        L += _items("Tests to run", impact.tests_to_run)
    sc = result.scenario
    if sc and sc.status == "generated":
        L += [f"## Test scenario: {sc.title}", ""]
        if sc.setup:
            L += ["Setup:"] + [f"1. {s}" for s in sc.setup]
        L += ["", f"Action: {sc.action}", f"Expected: {sc.expected}"]
        if sc.expected_error_code:
            L.append(f"Expected code: `{sc.expected_error_code}`")
        ex = sc.execution
        L += ["", "Execution: " + (f"**{ex.status}**" + (f" - {ex.details}" if ex.details else "")
                                     if ex else "not run"), ""]
    arch = result.architecture
    if arch and arch.status not in ("SKIPPED",):
        L += ["## Architecture changes", arch.summary, ""]
        L += [f"- {c.change_type} {c.category}: {c.name}" + (f" ({c.detail})" if c.detail else "")
              for c in arch.changes[:15]]
        if len(arch.changes) > 15:
            L.append(f"- ... and {len(arch.changes) - 15} more")
        L += [f"- Could not determine: {u}" for u in arch.unresolved]
        L += ["", "_" + " ".join(arch.caveats) + "_", ""]
    if lessons:
        L += ["## Prevention"] + [f"- {x}" for x in lessons] + [""]
    warnings = (rca.warnings if rca else []) + (fix.warnings if fix else []) \
        + (impact.warnings if impact else []) + (sc.warnings if sc else []) + result.notes
    if warnings:
        L += ["## Caveats"] + [f"- {w}" for w in warnings] + [""]
    return "\n".join(L).rstrip() + "\n"
