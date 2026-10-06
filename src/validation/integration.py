"""Bridge from Phase 4 (fix localization) and Phase 5 (impact analysis) to a
``ValidationRequest``.

Only deterministic facts are carried over: the exact localized
file/method becomes the target and changed file, and the tests those phases
already linked to the code become the test command's targets. Nothing here
decides what the fix should be.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence

from src.fix_localization.models import FixLocalizationResult
from src.impact_analysis.models import ImpactAnalysisResult

from .models import ValidationRequest

__all__ = ["build_validation_request", "test_files_from_phases"]


def test_files_from_phases(
    localization: FixLocalizationResult | None, impact: ImpactAnalysisResult | None
) -> list[str]:
    """Test files Phase 4 / Phase 5 linked to the change, de-duplicated, in order."""
    files: list[str] = []
    if localization is not None:
        files.extend(t.test_file for t in localization.related_tests if t.test_file)
    if impact is not None:
        files.extend(t.file for t in impact.tests if t.file)
    return list(dict.fromkeys(files))


test_files_from_phases.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def build_validation_request(
    repository: str,
    *,
    revision: str | None = None,
    localization: FixLocalizationResult | None = None,
    impact: ImpactAnalysisResult | None = None,
    test_commands: Sequence[str] | None = None,
    test_command_prefix: str = "pytest",
    changed_files: Iterable[str] | None = None,
    **request_fields: object,
) -> ValidationRequest:
    """Build a request from earlier-phase output.

    ``test_commands`` wins when given. Otherwise a single command
    ``"<test_command_prefix> <test files...>"`` is derived from the tests the
    earlier phases linked to the change; when there are none, this raises
    rather than guessing a command. Any other ``ValidationRequest`` field
    (build_command, start_command, readiness, ...) passes through.
    """
    target = None
    primary_file = None
    if localization is not None and localization.primary_location is not None:
        location = localization.primary_location.location
        primary_file = location.file_path
        if location.file_path and location.qualified_name:
            target = f"{location.file_path}::{location.qualified_name}"
        else:
            target = location.file_path or location.qualified_name

    if changed_files is None:
        changed_files = [primary_file] if primary_file else []

    commands = list(test_commands or [])
    if not commands:
        files = test_files_from_phases(localization, impact)
        if not files:
            raise ValueError(
                "no test_commands were given and Phase 4/5 found no linked tests to derive one from"
            )
        commands = [f"{test_command_prefix} {' '.join(shlex.quote(f) for f in files)}"]

    return ValidationRequest(
        repository=repository,
        revision=revision,
        test_commands=tuple(commands),
        changed_files=tuple(changed_files),
        target_location=target,
        **request_fields,  # type: ignore[arg-type]
    )
