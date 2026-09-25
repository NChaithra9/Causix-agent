"""Phase 1 top-level orchestration: "Structured Engineering Facts".

Ties together everything the other packages already extract, without
duplicating any of their logic:

    src.repo_ingestion  -- identify/access the repository, its root, its metadata
    src.parser          -- discover files, parse Python code, extract relationships
    src.git_history     -- commits, changed files, blame

into one structured result:

    Repository
     |- info              (RepositoryInfo)
     |- source_files      (SourceFileInfo, one per discovered file)
     |- files             (ParsedFile, one per parsed file: classes/methods/functions/imports)
     |- relationships     (CONTAINS / CALLS, from Step 5)
     `- git_history
         |- commits        (CommitInfo)
         `- file_changes    (MODIFIES relationships, derived from commits)

This is meant to be reusable as-is by Phase 2 (e.g. loading into Neo4j):
`files`/`relationships` map onto nodes/edges already, and
`git_history.commits`/`file_changes` add Commit nodes and MODIFIES edges the
same way. Nothing in this module performs any new analysis -- it only calls
the existing, already-tested building blocks and assembles their results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .git_history.git_reader import get_commit_file_relationships, get_commits
from .git_history.models import CommitInfo
from .parser.models import ParsedFile, Relationship, SourceFileInfo
from .parser.python_parser import parse_repository, scan_source_files
from .parser.relationships import extract_repository_relationships
from .repo_ingestion.ingestion import get_repository_info
from .repo_ingestion.models import RepositoryInfo

__all__ = ["GitHistory", "Repository", "build_repository_facts"]


@dataclass
class GitHistory:
    """Commits reachable from HEAD, plus the relationships derived from them.

    Blame is deliberately not bulk-computed here -- it's a per-line,
    on-demand lookup (see ``src.git_history.blame_line``), not something
    useful to precompute for every line of every file up front.
    """

    commits: list[CommitInfo] = field(default_factory=list)
    file_changes: list[Relationship] = field(default_factory=list)


@dataclass
class Repository:
    """Everything Phase 1 knows about one repository."""

    info: RepositoryInfo
    source_files: list[SourceFileInfo] = field(default_factory=list)
    files: list[ParsedFile] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    git_history: GitHistory = field(default_factory=GitHistory)


def build_repository_facts(repo_path: str | Path, *, max_commits: int | None = None) -> Repository:
    """Run the full Phase 1 pipeline against an already-accessible repository path.

    Repository Ingestion -> File Extraction -> Python Parsing ->
    Relationships -> Git History. ``repo_path`` should already point at a
    local, accessible repository -- use ``src.repo_ingestion.clone_repository``
    first if it needs to be cloned from a URL.

    ``max_commits`` optionally caps how much Git history to read (useful for
    a large repository); omit it to read the full history.
    """
    root = Path(repo_path)

    info = get_repository_info(root)
    source_files = scan_source_files(root)
    parsed_files = parse_repository(root)
    relationships = extract_repository_relationships(root, parsed_files)

    commits = get_commits(root, max_count=max_commits)
    file_changes = get_commit_file_relationships(commits)

    return Repository(
        info=info,
        source_files=source_files,
        files=parsed_files,
        relationships=relationships,
        git_history=GitHistory(commits=commits, file_changes=file_changes),
    )
