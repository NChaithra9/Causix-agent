"""Phase 5: deterministic Impact Analysis Engine.

    Changed method / function
            v
    Direct callers -> indirect callers (reverse CALLS, depth-bounded, cycle-safe)
            v
    Containing classes -> files
            v
    Services -> APIs -> Events -> Databases -> Tables -> downstream services
            v
    Tests
            v
    ImpactAnalysisResult (items + the exact path behind each one + what is UNRESOLVED)

Everything comes from the Phase 2 Neo4j graph (plus Phase 4's documented
test-naming rule). No LLM, embeddings, semantic search or name-guessing:
an element is in the result only because a graph relationship put it
there, and the path that did so is preserved. Categories the graph holds no
data for are reported UNRESOLVED rather than empty.

Public API:
    analyze_impact(connection, localization=... | location=... | method=...) -> ImpactAnalysisResult
    GraphImpactProvider(connection) -- adapter for Person 2's ImpactProvider contract
"""

from .analyzer import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOWNSTREAM_DEPTH,
    DEFAULT_MAX_PATHS_PER_ITEM,
    analyze_impact,
)
from .models import (
    Directness,
    GraphRelationship,
    ImpactAnalysisResult,
    ImpactCategory,
    ImpactItem,
    ImpactPath,
    KnowledgeStatus,
    PathDirection,
    PathStep,
    ResolutionStatus,
    UnresolvedItem,
)
from .provider import GraphImpactProvider

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_DOWNSTREAM_DEPTH",
    "DEFAULT_MAX_PATHS_PER_ITEM",
    "Directness",
    "GraphImpactProvider",
    "GraphRelationship",
    "ImpactAnalysisResult",
    "ImpactCategory",
    "ImpactItem",
    "ImpactPath",
    "KnowledgeStatus",
    "PathDirection",
    "PathStep",
    "ResolutionStatus",
    "UnresolvedItem",
    "analyze_impact",
]
