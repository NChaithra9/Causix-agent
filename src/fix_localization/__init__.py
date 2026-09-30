"""Phase 4: deterministic Exact Fix Localization Engine.

    Issue / RCA Evidence (Phase 3)
            v
    Relevant Code Location -> Repository -> File -> Class -> Function/Method
            v
    Relevant Code -> Historical Changes -> Related Tests -> Related PR/Jira
            v
    Structured FixLocalizationResult

Built entirely on top of Phase 3 (``src.rca``): it takes an already-computed
``RCAResult`` and its evidence, and never re-derives what Phase 3 already
established. No LLM, no embeddings, no semantic search anywhere in this
package -- every fact comes from Phase 1's parser, Phase 2's Neo4j graph,
or Git itself.

Person 1 stops here, at facts: the exact repository/file/class/method,
the actual source code, and the historical/test evidence that supports the
location. The natural-language fix *recommendation* is Person 2's job.

Public API:
    localize_fix(rca_result, connection=..., repo_root=...) -> FixLocalizationResult
"""

from .investigator import localize_fix
from .models import (
    CandidateLocation,
    ClassLocalization,
    FixLocalizationResult,
    HistoricalChange,
    LocationRole,
    MethodLocalization,
    RelatedTest,
    RelevantCode,
    RepositoryIdentity,
    ResolutionStatus,
)

__all__ = [
    "CandidateLocation",
    "ClassLocalization",
    "FixLocalizationResult",
    "HistoricalChange",
    "LocationRole",
    "MethodLocalization",
    "RelatedTest",
    "RelevantCode",
    "RepositoryIdentity",
    "ResolutionStatus",
    "localize_fix",
]
