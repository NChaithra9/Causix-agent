"""Aetherion SDK project for building AI agents and tools."""

try:
    from .agent.agent import Causix

    __all__ = ["Causix"]
except ImportError:
    # `aetherion_sdk` is an external, environment-specific dependency that
    # isn't needed by every submodule of this package (e.g. the deterministic
    # `src.parser` package has no dependencies at all). Don't let its absence
    # block importing unrelated submodules.
    __all__ = []
