"""Log Correlation -- Phase 3, requirement 11.

Deterministic, rule-based correlation only: timestamp proximity, a shared
correlation/request id, or the log text literally containing the exception
type, the resolved file name, the resolved method name, or the error
message. Never semantic/LLM matching -- each match records exactly which
rule fired, in ``LogCorrelation.matched_on``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import CodeLocation, LogCorrelation, LogEntry, ParsedStackTrace

__all__ = ["parse_log_entry", "correlate_logs"]

DEFAULT_TIMESTAMP_WINDOW = timedelta(seconds=5)

# A reasonably permissive ISO-8601-ish timestamp, e.g. "2026-09-30T12:00:01Z" or "2026-09-30 12:00:01".
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_CORRELATION_ID_RE = re.compile(
    r"(?:correlation[_-]?id|request[_-]?id|trace[_-]?id)[=:]\s*([A-Za-z0-9_-]+)", re.IGNORECASE
)


def parse_log_entry(raw: str) -> LogEntry:
    """Deterministically extract a timestamp and/or correlation id from one raw log line."""
    timestamp = None
    ts_match = _TIMESTAMP_RE.search(raw)
    if ts_match:
        timestamp = _parse_timestamp(ts_match.group(0))

    correlation_id = None
    id_match = _CORRELATION_ID_RE.search(raw)
    if id_match:
        correlation_id = id_match.group(1)

    return LogEntry(raw=raw, timestamp=timestamp, correlation_id=correlation_id)


def _parse_timestamp(text: str) -> datetime | None:
    normalized = text.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    # No offset present -- try naive formats.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def correlate_logs(
    logs: list[str],
    *,
    stack_trace: ParsedStackTrace | None,
    location: CodeLocation | None,
    failure_timestamp: datetime | None,
    correlation_id: str | None = None,
    timestamp_window: timedelta = DEFAULT_TIMESTAMP_WINDOW,
) -> list[LogCorrelation]:
    """Correlate raw log lines against the rest of the investigation by
    explicit, named rules -- never free-text similarity."""
    correlations: list[LogCorrelation] = []

    needles: list[tuple[str, str]] = []
    if stack_trace and stack_trace.exception_type:
        needles.append((stack_trace.exception_type, "exception_type"))
    if stack_trace and stack_trace.exception_message:
        needles.append((stack_trace.exception_message, "exception_message"))
    if location and location.file_path:
        needles.append((location.file_path.rsplit("/", 1)[-1], "file_path"))
    if location and location.qualified_name:
        needles.append((location.qualified_name.rsplit(".", 1)[-1], "method"))

    for raw in logs:
        entry = parse_log_entry(raw)
        matched_on: str | None = None

        if correlation_id and entry.correlation_id and entry.correlation_id == correlation_id:
            matched_on = "correlation_id"
        elif failure_timestamp is not None and entry.timestamp is not None:
            if abs(entry.timestamp - failure_timestamp) <= timestamp_window:
                matched_on = "timestamp_window"

        if matched_on is None:
            for needle, rule in needles:
                if needle and needle in raw:
                    matched_on = rule
                    break

        if matched_on is not None:
            correlations.append(LogCorrelation(entry=entry, matched_on=matched_on))

    return correlations
