"""The deterministic fact extractors. Each is ``(tree, ctx, builder) -> None``."""

from .code import extract_code
from .compose import extract_compose
from .context import ExtractionContext
from .declared import extract_declared
from .manifests import extract_manifests

DEFAULT_EXTRACTORS = (extract_compose, extract_manifests, extract_code, extract_declared)

__all__ = [
    "DEFAULT_EXTRACTORS",
    "ExtractionContext",
    "extract_code",
    "extract_compose",
    "extract_declared",
    "extract_manifests",
]
