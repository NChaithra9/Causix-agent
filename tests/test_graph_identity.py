"""Tests for `src.graph.identity` -- stable, deterministic node identity.

These are pure-function tests: no Neo4j involved. They exist to prove the
guarantee ingestion idempotency depends on -- the same source entity always
produces the same id, and different entities never collide.
"""

from __future__ import annotations

from src.graph import identity


def test_repository_id_is_stable_and_prefers_remote_url():
    a = identity.repository_id(remote_url="https://github.com/org/repo.git", root_path="/tmp/a")
    b = identity.repository_id(remote_url="https://github.com/org/repo.git", root_path="/tmp/b")

    assert a == b  # same remote, different local checkout path -> same repo


def test_repository_id_falls_back_to_root_path_without_a_remote():
    a = identity.repository_id(remote_url=None, root_path="/tmp/repo-a")
    b = identity.repository_id(remote_url=None, root_path="/tmp/repo-b")

    assert a != b  # no remote to unify them -- distinct local repos stay distinct


def test_file_id_is_scoped_by_repository_not_just_path():
    """Two repositories both containing `utils.py` must not collide -- this is exactly
    the requirement 4 example (`File.name` alone is not globally unique)."""
    repo_a = identity.repository_id(remote_url=None, root_path="/tmp/repo-a")
    repo_b = identity.repository_id(remote_url=None, root_path="/tmp/repo-b")

    file_in_a = identity.file_id(repo_a, "utils.py")
    file_in_b = identity.file_id(repo_b, "utils.py")

    assert file_in_a != file_in_b


def test_file_id_is_deterministic_for_the_same_repo_and_path():
    repo_id = identity.repository_id(remote_url="https://example.com/x.git", root_path="/tmp/x")

    assert identity.file_id(repo_id, "a/b.py") == identity.file_id(repo_id, "a/b.py")


def test_class_and_method_ids_are_scoped_by_their_containing_file_and_class():
    file_a = identity.file_id("repo1", "service.py")
    file_b = identity.file_id("repo1", "other_service.py")

    class_in_a = identity.class_id(file_a, "PaymentService")
    class_in_b = identity.class_id(file_b, "PaymentService")
    assert class_in_a != class_in_b  # same class name, different file -- must not collide

    method_a = identity.method_id(class_in_a, "process")
    method_b = identity.method_id(class_in_b, "process")
    assert method_a != method_b


def test_function_id_disambiguates_same_name_different_line():
    """A file that legally redefines a top-level function twice must not collapse into one node."""
    file_id = identity.file_id("repo1", "module.py")

    first = identity.function_id(file_id, "helper", 3)
    second = identity.function_id(file_id, "helper", 10)

    assert first != second
    assert identity.function_id(file_id, "helper", 3) == first  # deterministic


def test_commit_id_is_scoped_by_repository():
    """The same commit SHA in two different repositories (extremely unlikely, but
    possible) must not be conflated into one node."""
    commit_in_a = identity.commit_id("repo1", "abc123")
    commit_in_b = identity.commit_id("repo2", "abc123")

    assert commit_in_a != commit_in_b
    assert identity.commit_id("repo1", "abc123") == commit_in_a
