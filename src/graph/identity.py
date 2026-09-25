"""Deterministic identity for graph entities -- Phase 2, requirements 4 & 8.

Every node id here is a stable hash of the entity's *real* identifying
information (repository URL/path, relative file path, class/method/function
name, line number, commit SHA) -- never a randomly generated id, and never a
display name alone. The same source entity, ingested twice (or ingested by
two different runs), always hashes to exactly the same id, which is what
lets Neo4j's ``MERGE`` find the existing node instead of creating a
duplicate.

Hashing (rather than plain string concatenation) just keeps ids short and
free of characters that would need escaping -- it doesn't add any
randomness or guessing: the hash of the same inputs is always the same
output.
"""

from __future__ import annotations

import hashlib

__all__ = [
    "repository_id",
    "file_id",
    "class_id",
    "method_id",
    "function_id",
    "commit_id",
]

_SEPARATOR = "␟"  # a control-picture character, vanishingly unlikely to appear in real input


def _stable_id(*parts: str) -> str:
    """A deterministic id derived from `parts` -- same input, same output, always."""
    joined = _SEPARATOR.join(parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def repository_id(*, remote_url: str | None, root_path: str) -> str:
    """Identity for a Repository node.

    Prefers the remote URL (stable across clones/machines) when one exists;
    falls back to the resolved root path for a repository with no remote
    configured -- still deterministic for that same local checkout.
    """
    if remote_url:
        return _stable_id("repository", remote_url)
    return _stable_id("repository", "local", root_path)


def file_id(repo_id: str, relative_path: str) -> str:
    """Identity for a File node: scoped by repository + its path *within* that repository.

    This is exactly why `File.name` (or even `File.path`) alone must never
    be treated as globally unique -- two different repositories can both
    have a `utils.py`; scoping by `repo_id` keeps them distinct.
    """
    return _stable_id("file", repo_id, relative_path)


def class_id(file_node_id: str, class_name: str) -> str:
    """Identity for a Class node: the file it's defined in + its name."""
    return _stable_id("class", file_node_id, class_name)


def method_id(class_node_id: str, method_name: str) -> str:
    """Identity for a Method node: the class it belongs to + its name.

    The class id already encodes the file (and repository), so this alone
    is enough to scope a method uniquely.
    """
    return _stable_id("method", class_node_id, method_name)


def function_id(file_node_id: str, function_name: str, line: int) -> str:
    """Identity for a top-level Function node: its file + name + definition line.

    The line number is included because a single file can legally contain
    two top-level `def`s with the same name (a redefinition) -- Step 4
    records both as distinct facts, and this keeps them distinct here too,
    rather than silently merging two different functions into one node.
    """
    return _stable_id("function", file_node_id, function_name, str(line))


def commit_id(repo_id: str, commit_hash: str) -> str:
    """Identity for a Commit node: scoped by repository + its SHA.

    A commit SHA is already effectively unique on its own, but scoping by
    `repo_id` avoids ever conflating commits from two unrelated repositories
    ingested into the same graph.
    """
    return _stable_id("commit", repo_id, commit_hash)
