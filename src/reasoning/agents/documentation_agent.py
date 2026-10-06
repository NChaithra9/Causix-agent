"""Documentation Agent: produces an incident / RCA report from a finished analysis.

Code renders every fact (src/reasoning/report.py). The LLM supplies only the title,
a short summary and prevention lessons.
"""
from pydantic import BaseModel, Field, ValidationError

from src.reasoning.agents.issue_agent import AgentError, extract_json
from src.reasoning.llm import LLMClient
from src.reasoning.prompts import DOCS_SYSTEM, build_docs_prompt
from src.reasoning.report import render_report
from src.reasoning.schemas import AnalysisResult, DocumentationReport

MAX_LESSONS = 3


class _RawDocs(BaseModel):
    title: str = ""
    summary: str = ""
    lessons: list[str] = Field(default_factory=list)


def skipped(reason: str) -> DocumentationReport:
    return DocumentationReport(status="skipped", reason=reason)


class DocumentationAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(self, result: AnalysisResult) -> DocumentationReport:
        if result.rca is None or result.rca.insufficient_evidence:
            return skipped("No grounded root cause, so there is nothing to document yet.")

        text = self.llm.complete(DOCS_SYSTEM, build_docs_prompt(result))
        try:
            raw = _RawDocs(**extract_json(text))
        except ValidationError as exc:
            raise AgentError(f"Documentation JSON did not match the expected shape: {exc}") from exc

        warnings: list[str] = []
        summary = raw.summary.strip()
        if not summary:
            raise AgentError("Documentation is missing its summary.")
        title = raw.title.strip() or f"Incident: {result.issue.summary[:60]}"
        lessons = [x.strip() for x in raw.lessons if x and x.strip()]
        if len(lessons) > MAX_LESSONS:
            warnings.append(f"Trimmed lessons to {MAX_LESSONS}.")
            lessons = lessons[:MAX_LESSONS]

        md = render_report(result, title, summary, lessons)
        return DocumentationReport(status="generated", title=title, summary=summary,
                                   lessons=lessons, markdown=md, warnings=warnings)
