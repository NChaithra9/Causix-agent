from pathlib import Path

from src.reasoning.retrieval.chunker import chunk_python_source, chunk_repository

SAMPLE = Path(__file__).parent / "fixtures" / "sample_repo"


def test_extracts_classes_methods_and_functions():
    names = {c.name: c.kind for c in chunk_repository(SAMPLE)}
    assert names["RefundService"] == "class"
    assert names["RefundService.is_eligible_for_refund"] == "method"
    assert names["generate_invoice"] == "function"


def test_chunk_has_location_lines_and_source():
    chunk = next(c for c in chunk_repository(SAMPLE) if c.name == "RefundService.is_eligible_for_refund")
    assert chunk.location == "payment_service/refund_service.py::RefundService.is_eligible_for_refund"
    assert (chunk.start_line, chunk.end_line) == (5, 7)
    assert "get_payment_profile" in chunk.text


def test_skips_venv_and_syntax_errors():
    names = {c.name for c in chunk_repository(SAMPLE)}
    assert "hidden_dependency" not in names
    assert "broken" not in names
    assert chunk_python_source("def broken(:", "x.py") == []
