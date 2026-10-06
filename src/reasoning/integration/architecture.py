"""Architecture change summary (Person 1's Phase 7) for the incident report."""
from collections.abc import Callable

from src.reasoning.schemas import ArchitectureChangeItem, ArchitectureSummary


def summarize(result) -> ArchitectureSummary:
    status = result.status.value
    items = []
    for c in result.changes:
        before, after = c.previous_state, c.current_state
        detail = f"{before} -> {after}" if before and after else (c.source and c.target and f"{c.source} -> {c.target}") or ""
        items.append(ArchitectureChangeItem(change_type=c.change_type.value, category=c.category.value,
                                            name=c.name, detail=str(detail)))
    by_cat = result.summary.by_category
    text = ("No architecture changes between the two revisions." if status == "NO_CHANGES" else
            f"{result.summary.total} change(s): " + ", ".join(f"{n} x {k}" for k, n in sorted(by_cat.items()))
            if status == "CHANGES_DETECTED" else "The comparison could not be completed.")
    return ArchitectureSummary(status=status, previous_revision=result.previous_revision,
                               current_revision=result.current_revision, summary=text, changes=items,
                               unresolved=[u.reason for u in result.unresolved_items])


def skipped(reason: str) -> ArchitectureSummary:
    return ArchitectureSummary(status="SKIPPED", summary=reason)


class GitArchitectureProvider:
    """Compares the architecture at two exact revisions of the indexed repository."""

    def __init__(self, repo_provider: Callable[[], str | None], detect=None) -> None:
        self.repo_provider = repo_provider
        self._detect = detect

    def compare(self, repository: str | None, previous: str, current: str) -> ArchitectureSummary:
        path = repository or self.repo_provider()
        if not path:
            return skipped("No repository path is known for the architecture comparison.")
        detect = self._detect
        if detect is None:
            from src.architecture import detect_architecture_changes as detect
        return summarize(detect(path, previous, current))
