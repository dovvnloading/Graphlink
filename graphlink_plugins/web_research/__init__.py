"""Provider-neutral Web Research plugin services.

The Qt node and window action layer are adapters around this package.  Keeping the
research contract here makes network policy, evidence budgeting, and lifecycle
behavior testable without constructing the application window.
"""

from .domain import (
    CancellationToken,
    EvidenceChunk,
    ProgressEvent,
    ResearchFailure,
    ResearchLimits,
    ResearchResult,
    ResearchStage,
    ResearchState,
    WebResearchRequest,
)
from .service import WebResearchService

__all__ = [
    "CancellationToken",
    "EvidenceChunk",
    "ProgressEvent",
    "ResearchFailure",
    "ResearchLimits",
    "ResearchResult",
    "ResearchStage",
    "ResearchState",
    "WebResearchRequest",
    "WebResearchService",
]
