"""Deterministic Python stack-trace parsing -- Phase 3, requirement 2.

Parses a standard CPython traceback string (exactly the text Python itself
prints for an uncaught exception) into structured :class:`StackFrame`
objects plus the exception type/message. Regex-based, on the fixed,
well-known traceback grammar -- no LLM, nothing inferred.

Handles the minimal-input case explicitly: a bare exception name/message
with no traceback body at all still parses cleanly to an empty frame list
plus whatever exception type/message could be read.
"""

from __future__ import annotations

import re

from .models import ParsedStackTrace, StackFrame

__all__ = ["parse_stack_trace"]

# 'File "path/to/file.py", line 42, in function_name'
_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<function>\S+)\s*$'
)

# The last non-blank line of a traceback: 'ExceptionType: message' (message optional).
_EXCEPTION_RE = re.compile(r"^(?P<exc_type>[A-Za-z_][A-Za-z0-9_.]*)\s*:\s*(?P<message>.*)$")

# A bare exception with no message and no colon, e.g. just 'KeyError'.
_BARE_EXCEPTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def parse_stack_trace(raw: str | None) -> ParsedStackTrace:
    """Deterministically parse a Python traceback string.

    Returns a :class:`ParsedStackTrace` with ``frames=[]`` and
    ``exception_type=exception_message=None`` for ``None``/empty/unparseable
    input -- callers (e.g. the RCA investigator) treat that as "nothing to
    resolve from the stack trace", never a crash.
    """
    if not raw or not raw.strip():
        return ParsedStackTrace(frames=[], exception_type=None, exception_message=None, raw=raw)

    lines = raw.splitlines()
    frames: list[StackFrame] = []

    i = 0
    while i < len(lines):
        match = _FRAME_RE.match(lines[i])
        if match:
            code_snippet = None
            # The traceback's own convention: the source line, if Python could
            # read it, is printed indented on the line right after "File ...".
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if nxt.strip() and not _FRAME_RE.match(nxt) and not _looks_like_exception_line(nxt):
                    code_snippet = nxt.strip()
                    i += 1
            frames.append(
                StackFrame(
                    file=match.group("file"),
                    line=int(match.group("line")),
                    function=match.group("function"),
                    code_snippet=code_snippet,
                )
            )
        i += 1

    exception_type, exception_message = _parse_exception_line(lines)

    return ParsedStackTrace(
        frames=frames,
        exception_type=exception_type,
        exception_message=exception_message,
        raw=raw,
    )


def _looks_like_exception_line(line: str) -> bool:
    return bool(_EXCEPTION_RE.match(line.strip())) or bool(_BARE_EXCEPTION_RE.match(line.strip()))


def _parse_exception_line(lines: list[str]) -> tuple[str | None, str | None]:
    """The exception type/message is always the last non-blank line of a
    real traceback -- but the whole input may *also* just be that one line
    (the minimal-input case: someone hands us only "ValueError: bad thing"
    or even just "ValueError")."""
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Traceback frame/context lines are never the exception line.
        if _FRAME_RE.match(line) or stripped.startswith("Traceback (most recent call last)"):
            return None, None
        match = _EXCEPTION_RE.match(stripped)
        if match:
            message = match.group("message").strip()
            return match.group("exc_type"), (message or None)
        if _BARE_EXCEPTION_RE.match(stripped):
            return stripped, None
        return None, None
    return None, None
