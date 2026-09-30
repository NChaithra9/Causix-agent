"""Tests for `src.rca.stack_trace` -- deterministic traceback parsing."""

from __future__ import annotations

from src.rca.stack_trace import parse_stack_trace

SAMPLE_TRACEBACK = '''Traceback (most recent call last):
  File "payment_service/service.py", line 12, in handle_request
    result = process_refund(order_id)
  File "payment_service/refund_service.py", line 34, in process_refund
    raise ValueError("REFUND_NOT_ALLOWED")
ValueError: REFUND_NOT_ALLOWED
'''


def test_parses_all_frames_with_file_line_function():
    parsed = parse_stack_trace(SAMPLE_TRACEBACK)
    assert len(parsed.frames) == 2
    assert parsed.frames[0].file == "payment_service/service.py"
    assert parsed.frames[0].line == 12
    assert parsed.frames[0].function == "handle_request"
    assert parsed.frames[1].file == "payment_service/refund_service.py"
    assert parsed.frames[1].line == 34
    assert parsed.frames[1].function == "process_refund"


def test_captures_code_snippet_when_present():
    parsed = parse_stack_trace(SAMPLE_TRACEBACK)
    assert parsed.frames[0].code_snippet == "result = process_refund(order_id)"
    assert parsed.frames[1].code_snippet == 'raise ValueError("REFUND_NOT_ALLOWED")'


def test_extracts_exception_type_and_message():
    parsed = parse_stack_trace(SAMPLE_TRACEBACK)
    assert parsed.exception_type == "ValueError"
    assert parsed.exception_message == "REFUND_NOT_ALLOWED"


def test_handles_bare_exception_name_only():
    parsed = parse_stack_trace("KeyError")
    assert parsed.frames == []
    assert parsed.exception_type == "KeyError"
    assert parsed.exception_message is None


def test_handles_exception_type_and_message_with_no_traceback_body():
    parsed = parse_stack_trace("ValueError: something went wrong")
    assert parsed.frames == []
    assert parsed.exception_type == "ValueError"
    assert parsed.exception_message == "something went wrong"


def test_handles_none_and_empty_input_gracefully():
    for value in (None, "", "   "):
        parsed = parse_stack_trace(value)
        assert parsed.frames == []
        assert parsed.exception_type is None
        assert parsed.exception_message is None


def test_multi_frame_traceback_without_code_snippets():
    raw = (
        'Traceback (most recent call last):\n'
        '  File "a.py", line 1, in f\n'
        '  File "b.py", line 2, in g\n'
        'RuntimeError: boom\n'
    )
    parsed = parse_stack_trace(raw)
    assert [f.function for f in parsed.frames] == ["f", "g"]
    assert parsed.frames[0].code_snippet is None
    assert parsed.exception_type == "RuntimeError"
    assert parsed.exception_message == "boom"
