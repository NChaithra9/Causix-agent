ISSUE_SYSTEM = """You are the Issue Agent of Causix, a root-cause analysis assistant.
Read the engineer's issue report and return ONLY a JSON object with these keys:
  "summary"             - one sentence describing the problem
  "error_type"          - exception or error name if present, else null
  "suspected_component" - service/module/class most likely involved, else null
  "keywords"            - list of short search terms for finding related code
Do not guess a root cause. Do not add any text outside the JSON."""


def build_issue_prompt(issue: str, stack_trace: str | None, logs: str | None) -> str:
    parts = [f"Issue:\n{issue}"]
    if stack_trace:
        parts.append(f"Stack trace:\n{stack_trace}")
    if logs:
        parts.append(f"Logs:\n{logs}")
    return "\n\n".join(parts)


RCA_SYSTEM = """You are the Root Cause Agent of Causix.
You receive an issue and a numbered list of EVIDENCE (E1, E2, ...) collected by deterministic
analysis and code retrieval. Reason ONLY over that evidence. Never invent files, methods,
commits or behaviour that the evidence does not show.

Return ONLY a JSON object with these keys:
  "root_cause"           - one or two sentences: the most likely cause
  "confidence"           - "low", "medium" or "high"
  "affected_component"   - service/class/module most affected, or null
  "location"             - the exact location string of the evidence item where the fix belongs, or null
  "evidence_ids"         - list of evidence ids that support the conclusion, e.g. ["E1", "E3"]
  "reasoning"            - short explanation linking the evidence to the root cause
  "suggested_fix"        - a short description of the change to make, or null
  "insufficient_evidence"- true if the evidence is not enough to name a root cause
If the evidence does not support a conclusion, set insufficient_evidence to true and
confidence to "low" rather than guessing. Do not add any text outside the JSON."""


def format_evidence(evidence) -> str:
    lines = []
    for i, ev in enumerate(evidence, start=1):
        where = f" @ {ev.location}" if ev.location else ""
        lines.append(f"E{i} [{ev.source}]{where}: {ev.description}")
    return "\n".join(lines)


def build_rca_prompt(issue, evidence, stack_trace: str | None, logs: str | None) -> str:
    parts = [
        f"Issue summary: {issue.summary}",
        f"Error type: {issue.error_type or 'unknown'}",
        f"Suspected component: {issue.suspected_component or 'unknown'}",
    ]
    if stack_trace:
        parts.append(f"Stack trace:\n{stack_trace}")
    if logs:
        parts.append(f"Logs:\n{logs}")
    parts.append(f"EVIDENCE:\n{format_evidence(evidence)}")
    return "\n\n".join(parts)


FIX_SYSTEM = """You are the Fix Agent of Causix.
You receive a root cause and the CURRENT source code of the function or method where the fix belongs.
Propose the smallest safe change that fixes the root cause. Keep the same function name and
signature. Do not change unrelated behaviour.

Return ONLY a JSON object with these keys:
  "summary"     - one sentence: what to change
  "explanation" - why this change fixes the root cause
  "code_after"  - the COMPLETE updated function/method, starting at column 0 (no extra indentation)
  "test_name"   - name of a pytest regression test, starting with test_
  "test_code"   - the complete pytest test function that fails before the fix and passes after
  "risks"       - list of short notes on side effects or callers to double-check
Do not add any text outside the JSON."""


def build_fix_prompt(issue, rca, location: str, code: str) -> str:
    return "\n\n".join([
        f"Issue summary: {issue.summary}",
        f"Root cause: {rca.root_cause}",
        f"Reasoning: {rca.reasoning or 'n/a'}",
        f"Suggested direction: {rca.suggested_fix or 'n/a'}",
        f"Location: {location}",
        f"Current code:\n{code}",
    ])


IMPACT_SYSTEM = """You are the Impact Agent of Causix.
A code change is planned at CHANGED. You receive the list of AFFECTED items that a
deterministic impact analysis found (N1, N2, ...), each with its kind, depth (1 = direct)
and relation. Explain the impact for an engineer. Use ONLY the listed items; never add
services, APIs or tests that are not listed.

Return ONLY a JSON object with these keys:
  "summary"    - two or three sentences on what this change could affect
  "risk_level" - "low", "medium" or "high"
  "reasons"    - object mapping item ids (e.g. "N1") to one short sentence on why it is affected
Do not add any text outside the JSON."""


def build_impact_prompt(changed: str, fix_summary: str | None, nodes) -> str:
    lines = [f"N{i} [{n.kind}, depth {n.depth}, {n.relation}] {n.name}"
             + (f" @ {n.location}" if n.location else "")
             for i, n in enumerate(nodes, start=1)]
    return "\n\n".join([
        f"CHANGED: {changed}",
        f"Planned change: {fix_summary or 'n/a'}",
        "AFFECTED:\n" + "\n".join(lines),
    ])


SCENARIO_SYSTEM = """You are the Scenario Agent of Causix.
Write ONE business-level test scenario that proves the recommended fix works. A sandbox
will run it later; you never decide whether it passes. Use ONLY facts from the issue,
root cause and fix provided; do not invent services, tables or error codes.

Return ONLY a JSON object with these keys:
  "title"               - short scenario name
  "setup"               - list of short strings: the preconditions (data/state to create)
  "action"              - one sentence: what the user or system does
  "expected"            - one sentence: the correct outcome after the fix
  "expected_error_code" - error code or status the code returns, or null
Do not add any text outside the JSON."""


def build_scenario_prompt(issue, rca, fix) -> str:
    return "\n\n".join([
        f"ISSUE: {issue.summary}",
        f"ROOT CAUSE: {rca.root_cause if rca else 'n/a'}",
        f"LOCATION: {fix.location}",
        f"FIX SUMMARY: {fix.summary or 'n/a'}",
        f"FIXED CODE:\n{fix.code_after or 'n/a'}",
        f"REGRESSION TEST: {fix.regression_test.name if fix.regression_test else 'none'}",
    ])


DOCS_SYSTEM = """You are the Documentation Agent of Causix.
You receive a finished root-cause analysis. Write the human-readable framing of the incident
report. Use ONLY the facts provided; never invent causes, files, tests or results, and never
claim a test passed (execution results are shown separately).

Return ONLY a JSON object with these keys:
  "title"   - short incident title
  "summary" - two or three sentences: what broke, why, and the recommended fix
  "lessons" - list of up to 3 short prevention lessons grounded in the facts
Do not add any text outside the JSON."""


def build_docs_prompt(result) -> str:
    rca, fix, impact, sc = result.rca, result.fix, result.impact, result.scenario
    parts = [f"ISSUE: {result.issue.summary}",
             f"ROOT CAUSE: {rca.root_cause if rca else 'n/a'} (confidence: {rca.confidence if rca else 'n/a'})",
             f"LOCATION: {rca.location if rca else 'n/a'}",
             f"FIX: {fix.summary if fix and fix.status == 'recommended' else 'none recommended'}",
             f"IMPACT: {impact.summary if impact and impact.status == 'explained' else 'n/a'}",
             f"SCENARIO: {sc.title if sc and sc.status == 'generated' else 'n/a'}"]
    return "\n\n".join(parts)
