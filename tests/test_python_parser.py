"""Tests for `src.parser` -- the deterministic Python code parser (Step 4).

Each test writes a small, throwaway Python source file into pytest's
`tmp_path` and checks what the parser extracts from it. No real repository,
network access, or LLM call is involved anywhere here.
"""

from __future__ import annotations

from pathlib import Path

from src.parser import find_python_files, parse_python_file, parse_repository

REFUND_SERVICE_SOURCE = """
from payment_service import get_payment


class RefundService:

    def check_refund(self, payment_id):
        payment = get_payment(payment_id)
        return payment.amount > 100
"""


def write(tmp_path: Path, name: str, content: str) -> Path:
    """Write `content` to `tmp_path/name` and return the resulting path."""
    file_path = tmp_path / name
    file_path.write_text(content)
    return file_path


# ---------------------------------------------------------------------------
# The worked example from the spec: a class with one method, and an import.
# ---------------------------------------------------------------------------


def test_parses_a_python_file(tmp_path):
    path = write(tmp_path, "refund_service.py", REFUND_SERVICE_SOURCE)
    parsed = parse_python_file(path)

    assert parsed.error is None
    assert parsed.file_path == str(path)


def test_class_is_detected(tmp_path):
    path = write(tmp_path, "refund_service.py", REFUND_SERVICE_SOURCE)
    parsed = parse_python_file(path)

    assert [c.name for c in parsed.classes] == ["RefundService"]
    assert parsed.classes[0].line == 5


def test_class_method_is_detected(tmp_path):
    path = write(tmp_path, "refund_service.py", REFUND_SERVICE_SOURCE)
    parsed = parse_python_file(path)

    refund_service = parsed.classes[0]
    assert [m.name for m in refund_service.methods] == ["check_refund"]

    method = refund_service.methods[0]
    assert method.qualified_name == "RefundService.check_refund"
    assert method.args == ["self", "payment_id"]
    assert method.line == 7

    # A method must never also be reported as a top-level function.
    assert parsed.functions == []


def test_import_is_detected(tmp_path):
    path = write(tmp_path, "refund_service.py", REFUND_SERVICE_SOURCE)
    parsed = parse_python_file(path)

    assert [imp.qualified_name for imp in parsed.imports] == ["payment_service.get_payment"]


# ---------------------------------------------------------------------------
# Top-level functions
# ---------------------------------------------------------------------------


def test_top_level_function_is_detected(tmp_path):
    source = "def get_payment(payment_id):\n    return payment_id\n"
    path = write(tmp_path, "payment_service.py", source)
    parsed = parse_python_file(path)

    assert [f.name for f in parsed.functions] == ["get_payment"]
    assert parsed.functions[0].args == ["payment_id"]
    assert parsed.functions[0].line == 1
    assert parsed.classes == []


# ---------------------------------------------------------------------------
# Import styles
# ---------------------------------------------------------------------------


def test_different_import_styles(tmp_path):
    source = (
        "import os\n"
        "import requests\n"
        "from foo import bar\n"
        "from foo.bar import baz\n"
        "from foo import qux as q\n"
    )
    path = write(tmp_path, "imports_example.py", source)
    parsed = parse_python_file(path)

    qualified_names = [imp.qualified_name for imp in parsed.imports]
    assert qualified_names == ["os", "requests", "foo.bar", "foo.bar.baz", "foo.qux"]

    aliased = next(imp for imp in parsed.imports if imp.name == "qux")
    assert aliased.alias == "q"


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------


def test_line_numbers_are_reported(tmp_path):
    source = (
        "# a leading comment\n"
        "\n"
        "import os\n"
        "\n"
        "class Example:\n"
        "    def method_one(self):\n"
        "        pass\n"
        "\n"
        "def top_level():\n"
        "    pass\n"
    )
    path = write(tmp_path, "lines.py", source)
    parsed = parse_python_file(path)

    assert parsed.imports[0].line == 3
    assert parsed.classes[0].line == 5
    assert parsed.classes[0].methods[0].line == 6
    assert parsed.functions[0].line == 9


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_file(tmp_path):
    path = write(tmp_path, "empty.py", "")
    parsed = parse_python_file(path)

    assert parsed.error is None
    assert parsed.imports == []
    assert parsed.classes == []
    assert parsed.functions == []


def test_file_with_only_imports(tmp_path):
    path = write(tmp_path, "only_imports.py", "import os\nimport sys\n")
    parsed = parse_python_file(path)

    assert len(parsed.imports) == 2
    assert parsed.classes == []
    assert parsed.functions == []


def test_file_with_only_functions(tmp_path):
    source = "def a():\n    pass\n\n\ndef b(x, y):\n    pass\n"
    path = write(tmp_path, "only_functions.py", source)
    parsed = parse_python_file(path)

    assert [f.name for f in parsed.functions] == ["a", "b"]
    assert parsed.classes == []
    assert parsed.imports == []


def test_file_with_only_a_class(tmp_path):
    source = "class Empty:\n    pass\n"
    path = write(tmp_path, "only_class.py", source)
    parsed = parse_python_file(path)

    assert [c.name for c in parsed.classes] == ["Empty"]
    assert parsed.classes[0].methods == []
    assert parsed.functions == []
    assert parsed.imports == []


def test_syntax_error_is_captured_not_raised(tmp_path):
    path = write(tmp_path, "broken.py", "def broken(:\n    pass\n")
    parsed = parse_python_file(path)

    assert parsed.error is not None
    assert "SyntaxError" in parsed.error
    assert parsed.imports == []
    assert parsed.classes == []
    assert parsed.functions == []


# ---------------------------------------------------------------------------
# Repository-level scanning
# ---------------------------------------------------------------------------


def test_find_python_files_skips_noisy_directories(tmp_path):
    write(tmp_path, "real_module.py", "x = 1\n")

    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    write(venv_dir, "ignored.py", "x = 1\n")

    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    write(cache_dir, "ignored_too.py", "x = 1\n")

    found = find_python_files(tmp_path)

    assert [p.name for p in found] == ["real_module.py"]


def test_parse_repository_scans_a_small_sample_repo(tmp_path):
    write(tmp_path, "payment_service.py", "def get_payment(payment_id):\n    return payment_id\n")
    write(tmp_path, "refund_service.py", REFUND_SERVICE_SOURCE)

    nested = tmp_path / "package"
    nested.mkdir()
    write(nested, "__init__.py", "")
    write(nested, "utils.py", "import json\n\ndef helper():\n    pass\n")

    results = parse_repository(tmp_path)
    file_paths = sorted(r.file_path for r in results)

    assert file_paths == [
        "package/__init__.py",
        "package/utils.py",
        "payment_service.py",
        "refund_service.py",
    ]

    refund = next(r for r in results if r.file_path == "refund_service.py")
    assert refund.classes[0].methods[0].qualified_name == "RefundService.check_refund"
