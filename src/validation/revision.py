"""Exact-revision preparation.

Validation never touches the developer's working tree: the repository is
cloned into a throw-away directory and checked out at precisely the
requested commit/branch/tag (plus an optional patch), and the resolved
commit hash is recorded as evidence.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, Repo

from .errors import PreparationError

__all__ = ["PreparedRepository", "prepare_repository"]


@dataclass
class PreparedRepository:
    path: Path  # the checked-out source tree
    commit: str  # the exact commit hash that was checked out
    _root: Path  # the temporary directory that owns ``path``
    patch_applied: bool = False

    def cleanup(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)


def _resolve(repo: Repo, revision: str) -> str:
    # A branch only exists as ``origin/<name>`` in a fresh clone, so try both spellings.
    for candidate in (revision, f"origin/{revision}"):
        try:
            return repo.git.rev_parse("--verify", "--quiet", f"{candidate}^{{commit}}").strip()
        except GitCommandError:
            continue
    raise PreparationError(f"revision {revision!r} was not found in the repository")


def prepare_repository(
    source: str,
    revision: str | None = None,
    patch: str | None = None,
    *,
    workspace_root: str | Path | None = None,
) -> PreparedRepository:
    """Clone ``source`` and check out ``revision`` (HEAD when omitted)."""
    root = Path(tempfile.mkdtemp(prefix="rootfix-validation-", dir=workspace_root))
    checkout = root / "source"
    try:
        try:
            repo = Repo.clone_from(source, str(checkout), no_checkout=True)
        except GitCommandError as exc:
            raise PreparationError(f"could not clone {source!r}: {exc.stderr.strip()}") from exc

        try:
            commit = _resolve(repo, revision) if revision else repo.git.rev_parse("HEAD").strip()
            repo.git.checkout("--force", "--detach", commit)
        except GitCommandError as exc:
            raise PreparationError(f"could not check out {revision or 'HEAD'!r}: {exc}") from exc

        applied = False
        if patch:
            patch_file = root / "change.patch"  # outside the source tree
            patch_file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8")
            try:
                repo.git.apply("--whitespace=nowarn", str(patch_file))
            except GitCommandError as exc:
                raise PreparationError(
                    f"the patch does not apply cleanly: {exc.stderr.strip()}"
                ) from exc
            applied = True
        return PreparedRepository(path=checkout, commit=commit, _root=root, patch_applied=applied)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
