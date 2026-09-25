"""Tests for `src.parser.scan_source_files` -- Repository File Extraction (Phase 1, requirement 2).

`find_python_files`'s discovery/exclusion behavior already has coverage in
`test_python_parser.py`; these tests focus on the metadata `scan_source_files`
adds on top (relative path, file name, language, line count).
"""

from __future__ import annotations

from pathlib import Path

from src.parser import scan_source_files


def write(tmp_path: Path, name: str, content: str) -> Path:
    file_path = tmp_path / name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    return file_path


def test_scan_source_files_captures_expected_metadata(tmp_path):
    write(tmp_path, "package/service.py", "import os\n\n\ndef helper():\n    pass\n")

    files = scan_source_files(tmp_path)

    assert len(files) == 1
    info = files[0]
    assert info.relative_path == "package/service.py"
    assert info.file_name == "service.py"
    assert info.language == "python"
    assert info.line_count == 5
    assert info.absolute_path.endswith("package/service.py")


def test_scan_source_files_skips_excluded_directories(tmp_path):
    write(tmp_path, "real_module.py", "x = 1\n")
    write(tmp_path, ".venv/lib/ignored.py", "x = 1\n")
    write(tmp_path, "__pycache__/ignored_too.py", "x = 1\n")
    write(tmp_path, "node_modules/pkg/ignored.py", "x = 1\n")

    files = scan_source_files(tmp_path)

    assert [f.relative_path for f in files] == ["real_module.py"]
