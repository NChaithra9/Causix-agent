"""RCA Agent: reasons over evidence to name a root cause.

The LLM proposes; deterministic grounding checks then make sure the answer only
points at evidence that actually exists. Anything ungrounded is removed and
recorded in `warnings`.
"""
from pydantic import BaseModel, Field, ValidationError

from src.reasoning.agents.issue_agent import AgentError, extract_json
from src.reasoning.llm import LLMClient
from src.reasoning.prompts import RCA_SYSTEM, build_rca_prompt
from src.reasoning.schemas import Evidence, IssueUnderstanding, RootCauseAnalysis

CONFIDENCE_LEVELS = {"low", "medium", "high"}


class _RawRCA(BaseModel):
    """Shape we expect back from the LLM, before grounding."""
    root_cause: str
    confidence: str = "low"
    affected_component: str | None = None
    location: str | None = None
    location_id: str | None = None        # evidence id (e.g. "E3") whose location is the fix site
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning: str = ""
    suggested_fix: str | None = None
    insufficient_evidence: bool = False


def insufficient(reason: str) -> RootCauseAnalysis:
    return RootCauseAnalysis(root_cause=reason, confidence="low", insufficient_evidence=True)


def _file_of(location: str) -> str:
    return location.partition("::")[0]


def resolve_location(raw: _RawRCA, evidence: list[Evidence], by_id: dict[str, Evidence],
                     warnings: list[str]) -> str | None:
    """Pick the fix location from what the evidence actually contains, in this order:
    1. the evidence id the model chose (location_id), 2. an exact location match,
    3. a loose match (``file.py:59``, ``file.py``) when exactly one evidence location fits,
    4. the engine's own KNOWN fix_localization location.
    Anything else is dropped, never taken from free text."""
    known = [ev.location for ev in evidence if ev.location]
    if raw.location_id:
        ev = by_id.get(raw.location_id.strip().upper())
        if ev and ev.location:
            return ev.location
        warnings.append(f"Ignored location_id {raw.location_id}: no evidence location there.")

    if raw.location:
        if raw.location in known:
            return raw.location
        # loose forms only: "file.py", "file.py:59" or "file.py:59-70". A named method that is not
        # in the evidence ("file.py::Ghost") is never re-pointed at a different method.
        loc = raw.location.strip()
        head, _, tail = loc.rpartition(":")
        file_part = head if head and tail.replace("-", "").isdigit() else loc
        fits = [] if "::" in file_part else list(dict.fromkeys(
            k for k in known if _file_of(k) == file_part))
        if len(fits) > 1:   # several locations in that file: prefer the engine-localized one
            engine = [ev.location for ev in evidence
                      if ev.source == "fix_localization" and ev.location in fits]
            fits = engine[:1] or fits
        if len(fits) == 1:
            warnings.append(f"Matched location '{raw.location}' to evidence location '{fits[0]}'.")
            return fits[0]
        warnings.append(f"Dropped location '{raw.location}': not found in the evidence.")

    for ev in evidence:     # fall back to the deterministic engine's exact location
        if ev.source == "fix_localization" and ev.location and "[KNOWN]" in ev.description:
            warnings.append(f"No usable location from the model; used the fix-localization location "
                            f"'{ev.location}'.")
            return ev.location
    return None


def ground(raw: _RawRCA, evidence: list[Evidence]) -> RootCauseAnalysis:
    """Keep only claims that are backed by the evidence list."""
    warnings: list[str] = []
    by_id = {f"E{i}": ev for i, ev in enumerate(evidence, start=1)}

    cited = []
    for eid in raw.evidence_ids:
        key = eid.strip().upper()
        if key in by_id and by_id[key] not in cited:
            cited.append(by_id[key])
        elif key not in by_id:
            warnings.append(f"Dropped citation {eid}: no such evidence item.")

    location = resolve_location(raw, evidence, by_id, warnings)

    confidence = raw.confidence.strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        warnings.append(f"Unknown confidence '{raw.confidence}', using 'low'.")
        confidence = "low"

    is_insufficient = raw.insufficient_evidence
    if not cited and not is_insufficient:
        warnings.append("No valid supporting evidence cited; marked as insufficient evidence.")
        is_insufficient = True
    if is_insufficient:
        confidence = "low"

    return RootCauseAnalysis(
        root_cause=raw.root_cause, confidence=confidence,
        affected_component=raw.affected_component, location=location,
        reasoning=raw.reasoning, suggested_fix=raw.suggested_fix,
        insufficient_evidence=is_insufficient, supporting_evidence=cited, warnings=warnings,
    )


class RCAAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(self, issue: IssueUnderstanding, evidence: list[Evidence],
            stack_trace: str | None = None, logs: str | None = None) -> RootCauseAnalysis:
        if not evidence:
            # Nothing to reason over: don't ask the LLM to guess.
            return insufficient("No evidence was collected, so no root cause can be named yet.")

        raw_text = self.llm.complete(RCA_SYSTEM, build_rca_prompt(issue, evidence, stack_trace, logs))
        try:
            raw = _RawRCA(**extract_json(raw_text))
        except ValidationError as exc:
            raise AgentError(f"RCA JSON did not match the expected shape: {exc}") from exc
        return ground(raw, evidence)
