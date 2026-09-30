"""Tests for `src.rca.log_correlation` -- deterministic, rule-based log matching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.rca.models import CodeLocation, ParsedStackTrace, ResolutionStatus
from src.rca.log_correlation import correlate_logs, parse_log_entry


def test_parses_iso_timestamp_from_log_line():
    entry = parse_log_entry("2026-09-30T12:00:01Z ERROR something failed")
    assert entry.timestamp is not None
    assert entry.timestamp.year == 2026 and entry.timestamp.month == 9 and entry.timestamp.day == 30


def test_parses_correlation_id_from_log_line():
    entry = parse_log_entry("request_id=abc-123 ERROR something failed")
    assert entry.correlation_id == "abc-123"


def test_correlates_by_exception_type():
    stack_trace = ParsedStackTrace(exception_type="ValueError", exception_message="REFUND_NOT_ALLOWED")
    logs = ["some unrelated log line", "2026-01-01 ERROR ValueError raised during checkout"]
    correlations = correlate_logs(logs, stack_trace=stack_trace, location=None, failure_timestamp=None)
    assert len(correlations) == 1
    assert correlations[0].matched_on == "exception_type"


def test_correlates_by_file_path():
    location = CodeLocation(resolution_status=ResolutionStatus.RESOLVED, file_path="payment_service/refund_service.py")
    logs = ["handling request in refund_service.py"]
    correlations = correlate_logs(logs, stack_trace=None, location=location, failure_timestamp=None)
    assert correlations[0].matched_on == "file_path"


def test_correlates_by_timestamp_window():
    failure_time = datetime(2026, 9, 30, 12, 0, 5, tzinfo=timezone.utc)
    logs = ["2026-09-30T12:00:03Z INFO some unrelated event"]
    correlations = correlate_logs(
        logs, stack_trace=None, location=None, failure_timestamp=failure_time, timestamp_window=timedelta(seconds=5)
    )
    assert len(correlations) == 1
    assert correlations[0].matched_on == "timestamp_window"


def test_does_not_correlate_outside_timestamp_window():
    failure_time = datetime(2026, 9, 30, 12, 0, 5, tzinfo=timezone.utc)
    logs = ["2026-09-30T11:00:00Z INFO some unrelated event"]
    correlations = correlate_logs(logs, stack_trace=None, location=None, failure_timestamp=failure_time)
    assert correlations == []


def test_correlates_by_explicit_correlation_id():
    logs = ["correlation_id=xyz-789 ERROR failed"]
    correlations = correlate_logs(
        logs, stack_trace=None, location=None, failure_timestamp=None, correlation_id="xyz-789"
    )
    assert correlations[0].matched_on == "correlation_id"


def test_unrelated_logs_produce_no_correlation():
    stack_trace = ParsedStackTrace(exception_type="ValueError")
    logs = ["totally unrelated log about a different subsystem"]
    correlations = correlate_logs(logs, stack_trace=stack_trace, location=None, failure_timestamp=None)
    assert correlations == []
