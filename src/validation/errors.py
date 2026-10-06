"""Exceptions that mean "the validation could not be set up", as opposed to
"the validated code failed". They map to INFRASTRUCTURE_FAILED."""

from __future__ import annotations

__all__ = ["DependencyError", "EnvironmentError_", "InfrastructureError", "PreparationError"]


class InfrastructureError(Exception):
    """Base class: something outside the validated code prevented the run."""


class PreparationError(InfrastructureError):
    """The repository/revision/patch could not be prepared."""


class EnvironmentError_(
    InfrastructureError
):  # noqa: N801 - avoid shadowing builtin EnvironmentError
    """The isolated environment could not be created or used."""


class DependencyError(InfrastructureError):
    """A temporary dependency container could not be started or did not become ready."""
