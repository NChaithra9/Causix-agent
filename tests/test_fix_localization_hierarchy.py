"""Tests for `src.fix_localization.hierarchy` and `.relevant_code` -- pure
ast/filesystem logic, no Neo4j needed."""

from __future__ import annotations

from pathlib import Path

from src.fix_localization.hierarchy import localize_class_and_method
from src.fix_localization.models import ResolutionStatus
from src.fix_localization.relevant_code import extract_relevant_code
from src.rca.models import CodeLocation


def _write(tmp_path: Path) -> None:
    (tmp_path / "payment").mkdir()
    (tmp_path / "payment" / "validator.py").write_text(
        "class PaymentValidator:\n\n"
        "    def validate_payment(self, payment):\n"
        "        customer = payment.customer\n"
        "        return customer.id\n\n\n"
        "def helper():\n"
        "    pass\n"
    )


def test_resolves_owning_class_and_method_line_range(tmp_path):
    _write(tmp_path)
    location = CodeLocation(
        resolution_status=ResolutionStatus.RESOLVED,
        file_path="payment/validator.py",
        line=4,
        node_id="x",
        node_label="Method",
        qualified_name="PaymentValidator.validate_payment",
    )

    class_loc, method_loc = localize_class_and_method(tmp_path, location)

    assert class_loc.resolution_status == ResolutionStatus.RESOLVED
    assert class_loc.name == "PaymentValidator"
    assert class_loc.start_line == 1

    assert method_loc.resolution_status == ResolutionStatus.RESOLVED
    assert method_loc.name == "validate_payment"
    assert method_loc.class_name == "PaymentValidator"
    assert method_loc.start_line == 3
    assert method_loc.failing_line == 4


def test_top_level_function_has_no_owning_class():
    pass  # covered below with a real filesystem, see test_top_level_function_reports_no_class


def test_top_level_function_reports_no_class(tmp_path):
    _write(tmp_path)
    location = CodeLocation(
        resolution_status=ResolutionStatus.RESOLVED,
        file_path="payment/validator.py",
        line=8,
        node_id="y",
        node_label="Function",
        qualified_name="helper",
    )

    class_loc, method_loc = localize_class_and_method(tmp_path, location)

    assert class_loc.resolution_status == ResolutionStatus.UNRESOLVED
    assert method_loc.resolution_status == ResolutionStatus.RESOLVED
    assert method_loc.kind == "Function"
    assert method_loc.class_name is None
    assert method_loc.name == "helper"


def test_unresolved_location_yields_unresolved_hierarchy(tmp_path):
    location = CodeLocation(resolution_status=ResolutionStatus.UNRESOLVED)
    class_loc, method_loc = localize_class_and_method(tmp_path, location)
    assert class_loc.resolution_status == ResolutionStatus.UNRESOLVED
    assert method_loc.resolution_status == ResolutionStatus.UNRESOLVED


def test_relevant_code_returns_full_method_body_not_whole_file(tmp_path):
    _write(tmp_path)
    location = CodeLocation(
        resolution_status=ResolutionStatus.RESOLVED,
        file_path="payment/validator.py",
        line=4,
        node_label="Method",
        qualified_name="PaymentValidator.validate_payment",
    )
    class_loc, method_loc = localize_class_and_method(tmp_path, location)

    code = extract_relevant_code(tmp_path, class_loc, method_loc)

    assert code.resolution_status == ResolutionStatus.RESOLVED
    assert code.start_line == 3
    assert code.end_line == 5
    assert code.lines == [
        "    def validate_payment(self, payment):",
        "        customer = payment.customer",
        "        return customer.id",
    ]
    # Never the whole file:
    assert len(code.lines) < 8


def test_relevant_code_unresolved_when_hierarchy_unresolved(tmp_path):
    class_loc, method_loc = localize_class_and_method(tmp_path, CodeLocation(resolution_status=ResolutionStatus.UNRESOLVED))
    code = extract_relevant_code(tmp_path, class_loc, method_loc)
    assert code.resolution_status == ResolutionStatus.UNRESOLVED
