"""Phase 6: deterministic validation infrastructure.

    ValidationRequest (repository + exact revision + optional patch)
            v
    Isolated environment (Docker) + temporary dependencies (Testcontainers)
            v
    build -> start application -> wait for readiness -> run tests
            v
    logs + exit codes -> ResultEvaluator -> ValidationResult
            v
    cleanup (always)

PASS/FAIL comes only from what actually ran: exit codes, timeouts and
readiness checks. No LLM is involved anywhere in this package. It validates
a change; it never decides what the change should be.

Public API:
    ValidationOrchestrator(config=...).validate(request) -> ValidationResult
    build_validation_request(repository, localization=..., impact=...) -> ValidationRequest
"""

from .dependencies import ResolvedDependency, TestcontainersDependencyProvider
from .docker import DockerEnvironment, docker_available
from .environment import Environment, LocalProcessEnvironment
from .evaluator import ResultEvaluator
from .integration import build_validation_request
from .models import (
    CommandRun,
    DependencyKind,
    DependencySpec,
    EnvironmentStatus,
    ExecutionStatus,
    ReadinessKind,
    ReadinessSpec,
    ResourceLimits,
    StartupResult,
    StartupStatus,
    TestExecutionResult,
    Timeouts,
    ValidationEvidence,
    ValidationLogs,
    ValidationRequest,
    ValidationResult,
    ValidationStatus,
)
from .orchestrator import Backend, ValidationConfig, ValidationOrchestrator
from .revision import PreparedRepository, prepare_repository

__all__ = [
    "Backend",
    "CommandRun",
    "DependencyKind",
    "DependencySpec",
    "DockerEnvironment",
    "Environment",
    "EnvironmentStatus",
    "ExecutionStatus",
    "LocalProcessEnvironment",
    "PreparedRepository",
    "ReadinessKind",
    "ReadinessSpec",
    "ResolvedDependency",
    "ResourceLimits",
    "ResultEvaluator",
    "StartupResult",
    "StartupStatus",
    "TestExecutionResult",
    "TestcontainersDependencyProvider",
    "Timeouts",
    "ValidationConfig",
    "ValidationEvidence",
    "ValidationLogs",
    "ValidationOrchestrator",
    "ValidationRequest",
    "ValidationResult",
    "ValidationStatus",
    "build_validation_request",
    "docker_available",
    "prepare_repository",
]
