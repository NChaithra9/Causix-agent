"""Make engine locations match the indexed code.

Person 1's evidence uses file names such as ``refunds.py::is_eligible`` or absolute paths,
while the retrieval index uses repo-relative ids such as ``billing/refunds.py::is_eligible``.
Fix and impact steps need the indexed form, so locations are resolved here.
"""
from src.reasoning.facts import FactsProvider
from src.reasoning.schemas import Evidence, IssueUnderstanding


def _split(location: str) -> tuple[str, str | None]:
    file, sep, name = location.partition("::")
    return file, (name if sep else None)


class LocationIndex:
    """All known chunk ids (``file::Name``), filled whenever a repository is indexed."""

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def load(self, chunks) -> None:
        self._ids = {c.location for c in chunks}

    def __len__(self) -> int:
        return len(self._ids)

    def resolve(self, location: str | None) -> str | None:
        """The indexed id for ``location``, or ``location`` unchanged when it is already exact
        or cannot be matched to exactly one indexed item."""
        if not location or location in self._ids:
            return location
        file, name = _split(location)
        if name is None:
            return location
        matches = []
        for chunk_id in self._ids:
            id_file, id_name = _split(chunk_id)
            if id_name != name:
                continue
            if file == id_file or file.endswith("/" + id_file) or id_file.endswith("/" + file):
                matches.append(chunk_id)
        return matches[0] if len(matches) == 1 else location


class NormalizedFacts:
    """Wraps any FactsProvider and rewrites evidence locations to the indexed form."""

    def __init__(self, inner: FactsProvider, index: LocationIndex) -> None:
        self.inner = inner
        self.index = index

    def collect(self, issue: IssueUnderstanding, repository: str | None,
                stack_trace: str | None) -> list[Evidence]:
        out = []
        for ev in self.inner.collect(issue, repository, stack_trace):
            resolved = self.index.resolve(ev.location)
            out.append(ev if resolved == ev.location else ev.model_copy(update={"location": resolved}))
        return out
