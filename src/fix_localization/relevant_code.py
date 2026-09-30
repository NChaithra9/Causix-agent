"""Relevant Code -- Phase 4, requirement 7.

Returns the actual source of the localized method/function/class -- never
generated, never modified, and never the whole file when only a method is
relevant. Falls back to the class's own range when there's no method (a
class-level location), and comes back UNRESOLVED (never guessing a range)
when neither is available.
"""

from __future__ import annotations

from pathlib import Path

from .models import ClassLocalization, MethodLocalization, RelevantCode, ResolutionStatus

__all__ = ["extract_relevant_code"]


def extract_relevant_code(
    repo_root: str | Path,
    class_localization: ClassLocalization,
    method_localization: MethodLocalization,
) -> RelevantCode:
    if method_localization.resolution_status == ResolutionStatus.RESOLVED:
        file_path = method_localization.file_path
        start = method_localization.start_line
        end = method_localization.end_line
    elif class_localization.resolution_status == ResolutionStatus.RESOLVED:
        file_path = class_localization.file_path
        start = class_localization.start_line
        end = class_localization.end_line
    else:
        return RelevantCode(resolution_status=ResolutionStatus.UNRESOLVED)

    if not file_path or start is None or end is None:
        return RelevantCode(resolution_status=ResolutionStatus.UNRESOLVED)

    try:
        source_lines = (Path(repo_root) / file_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return RelevantCode(resolution_status=ResolutionStatus.UNRESOLVED, file_path=file_path)

    snippet = source_lines[start - 1 : end]
    if not snippet:
        return RelevantCode(resolution_status=ResolutionStatus.UNRESOLVED, file_path=file_path)

    return RelevantCode(
        resolution_status=ResolutionStatus.RESOLVED,
        file_path=file_path,
        start_line=start,
        end_line=end,
        lines=snippet,
    )
