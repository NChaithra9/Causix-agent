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
