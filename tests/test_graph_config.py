"""Tests for `src.graph.config` -- Neo4j configuration from environment variables."""

from __future__ import annotations

import pytest

from src.graph import Neo4jConfig, Neo4jConfigError


def test_config_loads_from_environment_variables(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "s3cret")
    monkeypatch.delenv("NEO4J_DATABASE", raising=False)

    config = Neo4jConfig.from_env()

    assert config.uri == "bolt://localhost:7687"
    assert config.username == "neo4j"
    assert config.password == "s3cret"
    assert config.database == "neo4j"  # default


def test_config_honors_a_custom_database_name(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "s3cret")
    monkeypatch.setenv("NEO4J_DATABASE", "rootfix")

    config = Neo4jConfig.from_env()

    assert config.database == "rootfix"


def test_config_fails_cleanly_when_required_variables_are_missing(monkeypatch):
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    with pytest.raises(Neo4jConfigError) as excinfo:
        Neo4jConfig.from_env()

    # The error names exactly what's missing, rather than a generic failure.
    assert "NEO4J_URI" in str(excinfo.value)
    assert "NEO4J_USERNAME" in str(excinfo.value)
    assert "NEO4J_PASSWORD" in str(excinfo.value)


def test_config_fails_cleanly_when_only_password_is_missing(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    with pytest.raises(Neo4jConfigError) as excinfo:
        Neo4jConfig.from_env()

    assert "NEO4J_PASSWORD" in str(excinfo.value)
    assert "NEO4J_URI" not in str(excinfo.value)
