"""Fix Agent: turns a grounded root cause into a reviewable code change + regression test.

The LLM proposes the new code; Causix itself checks that it is valid Python, keeps the
same function name, computes the diff, and never writes anything to disk.
"""
import ast
import difflib
import re
import textwrap
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

from src.reasoning.agents.issue_agent import AgentError, extract_json
from src.reasoning.llm import LLMClient
from src.reasoning.prompts import FIX_SYSTEM, build_fix_prompt
from src.reasoning.retrieval.chunker import CodeChunk
from src.reasoning.schemas import (
    FixRecommendation,
    IssueUnderstanding,
    RegressionTest,
    RootCauseAnalysis,
)

CodeLookup = Callable[[str], CodeChunk | None]


class _RawFix(BaseModel):
    summary: str
    explanation: str = ""
    code_after: str | None = None
    test_name: str | None = None
    test_code: str | None = None
    risks: list[str] = Field(default_factory=list)


def skipped(reason: str, location: str | None = None) -> FixRecommendation:
    return FixRecommendation(status="skipped", reason=reason, location=location)


def source_of(chunk: CodeChunk) -> str:
    """Chunk text at column 0 (the first line has no indentation in the chunk, the rest does)."""
    return textwrap.dedent(" " * chunk.col_offset + chunk.text)


def parses(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def defined_names(code: str) -> set[str]:
    tree = ast.parse(code)
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def make_diff(chunk: CodeChunk, before: str, after: str) -> str:
    """Unified diff with real file line numbers, re-indented to match the original file."""
    pad = " " * chunk.col_offset
    to_file = lambda code: [(pad + line) if line.strip() else line  # noqa: E731
                            for line in code.rstrip("\n").splitlines(keepends=False)]
    lines = difflib.unified_diff(to_file(before), to_file(after),
                                 fromfile=f"a/{chunk.file}", tofile=f"b/{chunk.file}", lineterm="")
    offset = chunk.start_line - 1

    def shift(match: re.Match) -> str:
        a, b, c, d = match.groups()
        return f"@@ -{int(a) + offset}{b or ''} +{int(c) + offset}{d or ''} @@"

    return "\n".join(re.sub(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@", shift, line) for line in lines)


class FixAgent:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def run(self, issue: IssueUnderstanding, rca: RootCauseAnalysis | None,
            lookup: CodeLookup) -> FixRecommendation:
        # Guardrails first: only fix what the RCA actually pinned down.
        if rca is None:
            return skipped("No root cause analysis available.")
        if rca.insufficient_evidence:
            return skipped("Root cause is not established (insufficient evidence).", rca.location)
        if not rca.location:
            return skipped("Root cause has no exact code location to fix.")
        chunk = lookup(rca.location)
        if chunk is None:
            return skipped("Source code for the location is not indexed.", rca.location)

        before = source_of(chunk)
        raw_text = self.llm.complete(FIX_SYSTEM, build_fix_prompt(issue, rca, rca.location, before))
        try:
            raw = _RawFix(**extract_json(raw_text))
        except ValidationError as exc:
            raise AgentError(f"Fix JSON did not match the expected shape: {exc}") from exc

        warnings: list[str] = []
        after = textwrap.dedent(raw.code_after).strip("\n") + "\n" if raw.code_after else None
        if after and not parses(after):
            warnings.append("Proposed code is not valid Python; dropped it.")
            after = None
        if after and after.strip() == before.strip():
            warnings.append("Proposed code is identical to the current code; no change suggested.")
            after = None
        short_name = chunk.name.split(".")[-1]
        if after and short_name not in defined_names(after):
            warnings.append(f"Proposed code does not define '{short_name}'; dropped it.")
            after = None

        test = None
        if raw.test_code:
            test_code = textwrap.dedent(raw.test_code).strip("\n") + "\n"
            if parses(test_code) and any(n.startswith("test_") for n in defined_names(test_code)):
                test = RegressionTest(name=raw.test_name or next(
                    n for n in defined_names(test_code) if n.startswith("test_")), code=test_code)
            else:
                warnings.append("Regression test is not a valid pytest test function; dropped it.")

        return FixRecommendation(
            status="recommended", location=rca.location, file=chunk.file,
            summary=raw.summary, explanation=raw.explanation,
            code_before=before, code_after=after,
            diff=make_diff(chunk, before, after) if after else None,
            regression_test=test, risks=raw.risks, warnings=warnings,
        )
