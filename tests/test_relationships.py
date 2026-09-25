"""Tests for `src.parser.relationships` -- deterministic relationship extraction (Step 5).

Each test writes a small, throwaway Python file (or small set of files) into
pytest's `tmp_path`, runs it through Step 4's parser, then checks what Step 5
derives from that. No LLM, network access, or Neo4j involved anywhere here.
"""

from __future__ import annotations

from pathlib import Path

from src.parser import (
    RelationshipType,
    extract_relationships_for_file,
    extract_repository_relationships,
    parse_python_file,
    parse_repository,
)


def write(tmp_path: Path, name: str, content: str) -> Path:
    """Write `content` to `tmp_path/name` and return the resulting path."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


def relationships_of_type(relationships, relationship_type):
    return [r for r in relationships if r.relationship_type == relationship_type]


# ---------------------------------------------------------------------------
# Test 1 -- File contains class
# ---------------------------------------------------------------------------


def test_file_contains_class(tmp_path):
    path = write(tmp_path, "payment_service.py", "class PaymentService:\n    pass\n")
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    contains = relationships_of_type(relationships, RelationshipType.CONTAINS)

    assert any(
        r.source == str(path) and r.target == "PaymentService" and r.resolved for r in contains
    )


# ---------------------------------------------------------------------------
# File contains function (relationship #2 from the spec)
# ---------------------------------------------------------------------------


def test_file_contains_function(tmp_path):
    path = write(tmp_path, "refund_service.py", "def calculate_refund():\n    pass\n")
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    contains = relationships_of_type(relationships, RelationshipType.CONTAINS)

    assert any(
        r.source == str(path) and r.target == "calculate_refund" and r.resolved for r in contains
    )


# ---------------------------------------------------------------------------
# Test 2 -- Class contains method
# ---------------------------------------------------------------------------


def test_class_contains_method(tmp_path):
    source = "class PaymentService:\n\n    def process(self):\n        pass\n"
    path = write(tmp_path, "payment_service.py", source)
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    contains = relationships_of_type(relationships, RelationshipType.CONTAINS)

    assert any(
        r.source == "PaymentService" and r.target == "process" and r.resolved for r in contains
    )


# ---------------------------------------------------------------------------
# Test 3 -- Function calls function
# ---------------------------------------------------------------------------


def test_function_calls_function(tmp_path):
    source = "def process():\n    validate()\n\ndef validate():\n    pass\n"
    path = write(tmp_path, "workflow.py", source)
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    call = calls[0]
    assert call.source == "process"
    assert call.target == "validate"
    assert call.resolved is True
    assert call.raw_call == "validate()"


# ---------------------------------------------------------------------------
# Test 4 -- Method calls another method (via self)
# ---------------------------------------------------------------------------


def test_method_calls_another_method(tmp_path):
    source = (
        "class PaymentService:\n"
        "\n"
        "    def process(self):\n"
        "        self.validate()\n"
        "\n"
        "    def validate(self):\n"
        "        pass\n"
    )
    path = write(tmp_path, "payment_service.py", source)
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    call = calls[0]
    assert call.source == "PaymentService.process"
    assert call.target == "PaymentService.validate"
    assert call.resolved is True


def test_method_calls_top_level_function_too(tmp_path):
    """A method can also call a plain top-level function alongside self.<method>()."""
    source = (
        "def calculate_refund():\n"
        "    pass\n"
        "\n"
        "class RefundService:\n"
        "\n"
        "    def process(self):\n"
        "        self.validate()\n"
        "        calculate_refund()\n"
        "\n"
        "    def validate(self):\n"
        "        pass\n"
    )
    path = write(tmp_path, "refund_service.py", source)
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    calls = {(r.source, r.target): r.resolved for r in relationships_of_type(relationships, RelationshipType.CALLS)}

    assert calls[("RefundService.process", "RefundService.validate")] is True
    assert calls[("RefundService.process", "calculate_refund")] is True


# ---------------------------------------------------------------------------
# Test 5 -- Imported function call, resolved across files
# ---------------------------------------------------------------------------


def test_imported_function_call_is_resolved_across_files(tmp_path):
    write(tmp_path, "payment.py", "def get_payment():\n    pass\n")
    write(
        tmp_path,
        "refund.py",
        "from payment import get_payment\n\ndef refund():\n    get_payment()\n",
    )

    parsed_files = parse_repository(tmp_path)
    relationships = extract_repository_relationships(tmp_path, parsed_files)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    call = calls[0]
    assert call.source == "refund"
    assert call.target == "payment.get_payment"
    assert call.resolved is True
    assert call.source_file == "refund.py"


def test_plain_module_import_call_is_resolved_across_files(tmp_path):
    """`import module` + `module.func()` is just as deterministic as `from module import func`."""
    write(tmp_path, "payment.py", "def get_payment():\n    pass\n")
    write(
        tmp_path,
        "refund.py",
        "import payment\n\ndef refund():\n    payment.get_payment()\n",
    )

    parsed_files = parse_repository(tmp_path)
    relationships = extract_repository_relationships(tmp_path, parsed_files)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    assert calls[0].target == "payment.get_payment"
    assert calls[0].resolved is True


# ---------------------------------------------------------------------------
# Test 6 -- Unresolved call: never invent a target
# ---------------------------------------------------------------------------


def test_unresolved_call_does_not_invent_a_target(tmp_path):
    path = write(tmp_path, "refund.py", "def refund():\n    external_unknown_function()\n")
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    call = calls[0]
    assert call.source == "refund"
    assert call.resolved is False
    # The raw name is kept, but nothing invented beyond what was written.
    assert call.target == "external_unknown_function"
    assert call.raw_call == "external_unknown_function()"


def test_unresolved_call_through_an_arbitrary_object(tmp_path):
    """`service.process()` can't be resolved without knowing what `service` is -- and shouldn't be guessed."""
    path = write(
        tmp_path,
        "refund_service.py",
        "def refund(service):\n    service.process()\n",
    )
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    call = calls[0]
    assert call.resolved is False
    assert call.target == "service.process"
    assert call.raw_call == "service.process()"


def test_import_without_matching_definition_is_not_resolved(tmp_path):
    """Importing a name that doesn't actually resolve to a parsed function/class stays unresolved."""
    write(tmp_path, "payment.py", "x = 1\n")  # no get_payment defined here
    write(
        tmp_path,
        "refund.py",
        "from payment import get_payment\n\ndef refund():\n    get_payment()\n",
    )

    parsed_files = parse_repository(tmp_path)
    relationships = extract_repository_relationships(tmp_path, parsed_files)
    calls = relationships_of_type(relationships, RelationshipType.CALLS)

    assert len(calls) == 1
    assert calls[0].resolved is False
    assert calls[0].target == "get_payment"


# ---------------------------------------------------------------------------
# File -> IMPORTS -> File (added for Phase 2's graph, per the sample
# end-to-end test: service.py IMPORTS validator.py)
# ---------------------------------------------------------------------------


def test_file_imports_file_is_resolved_across_files(tmp_path):
    write(tmp_path, "validator.py", "def validate_payment():\n    pass\n")
    write(
        tmp_path,
        "service.py",
        "from validator import validate_payment\n\n\nclass PaymentService:\n\n    def process(self):\n        validate_payment()\n",
    )

    parsed_files = parse_repository(tmp_path)
    relationships = extract_repository_relationships(tmp_path, parsed_files)
    imports = relationships_of_type(relationships, RelationshipType.IMPORTS)

    assert len(imports) == 1
    assert imports[0].source == "service.py"
    assert imports[0].target == "validator.py"
    assert imports[0].resolved is True


def test_file_imports_external_package_produces_no_relationship(tmp_path):
    path = write(tmp_path, "service.py", "import requests\n\ndef fetch():\n    requests.get('x')\n")
    parsed = parse_python_file(path)

    relationships = extract_relationships_for_file(parsed)
    imports = relationships_of_type(relationships, RelationshipType.IMPORTS)

    assert imports == []


def test_relative_import_call_is_resolved_across_files(tmp_path):
    """The Phase 2 sample repo (payment/service.py + validator.py) uses exactly this pattern."""
    nested = tmp_path / "payment"
    nested.mkdir()
    write(nested, "__init__.py", "")
    write(nested, "validator.py", "def validate_payment():\n    pass\n")
    write(
        nested,
        "service.py",
        "from .validator import validate_payment\n\n\nclass PaymentService:\n\n    def process(self):\n        validate_payment()\n",
    )

    parsed_files = parse_repository(tmp_path)
    relationships = extract_repository_relationships(tmp_path, parsed_files)

    calls = relationships_of_type(relationships, RelationshipType.CALLS)
    assert any(
        r.source == "PaymentService.process"
        and r.target == "payment.validator.validate_payment"
        and r.resolved
        for r in calls
    )

    imports = relationships_of_type(relationships, RelationshipType.IMPORTS)
    assert any(
        r.source == "payment/service.py" and r.target == "payment/validator.py" and r.resolved
        for r in imports
    )


def test_relative_import_beyond_repository_root_is_unresolved(tmp_path):
    """`from .. import x` at the repository root has nowhere to go -- must not crash or guess."""
    path = write(tmp_path, "module.py", "from .. import something\n\n\ndef use():\n    something()\n")
    parsed = parse_python_file(path)
    parsed_files = parse_repository(tmp_path)
    relationships = extract_repository_relationships(tmp_path, parsed_files)

    calls = relationships_of_type(relationships, RelationshipType.CALLS)
    assert any(r.source == "use" and r.target == "something" and not r.resolved for r in calls)
