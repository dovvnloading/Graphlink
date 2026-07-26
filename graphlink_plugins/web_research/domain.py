"""Qt-free Web Research domain contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import Any, Callable


class ResearchStage(str, Enum):
    PREPARING = "preparing"
    SEARCHING = "searching"
    FETCHING = "fetching"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ResearchState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    DISPOSED = "disposed"


class RequestCancelled(RuntimeError):
    """Raised when a research operation is cancelled cooperatively."""

    code = "cancelled"
    retryable = True


class ResearchFailure(RuntimeError):
    """A user-safe, structured research failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "research_failed",
        retryable: bool = True,
        source_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.source_id = source_id


class CancellationToken:
    """Thread-safe cancellation primitive shared across every pipeline stage."""

    def __init__(self):
        self._event = Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise RequestCancelled("Web research was cancelled.")


@dataclass(frozen=True)
class ResearchLimits:
    max_search_results: int = 8
    max_sources: int = 4
    max_redirects: int = 3
    max_bytes_per_source: int = 2 * 1024 * 1024
    max_chars_per_source: int = 18_000
    max_chars_per_evidence_chunk: int = 1_600
    max_evidence_chars: int = 42_000
    max_evidence_tokens: int = 10_000
    max_query_chars: int = 1_000
    max_history_chars: int = 18_000

    def __post_init__(self):
        for name in (
            "max_search_results",
            "max_sources",
            "max_redirects",
            "max_bytes_per_source",
            "max_chars_per_source",
            "max_chars_per_evidence_chunk",
            "max_evidence_chars",
            "max_evidence_tokens",
            "max_query_chars",
            "max_history_chars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True)
class WebResearchRequest:
    request_id: str
    node_id: str
    chat_epoch: int
    original_query: str
    branch_history: list[dict[str, Any]] = field(default_factory=list)
    limits: ResearchLimits = field(default_factory=ResearchLimits)
    provider_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    source_id: str
    title: str
    url: str
    canonical_url: str
    snippet: str = ""
    rank: int = 0
    provider: str = "unknown"


@dataclass(frozen=True)
class FetchedPayload:
    source_id: str
    requested_url: str
    final_url: str
    content_type: str
    body: bytes
    truncated: bool = False
    status_code: int = 200
    duration_ms: int = 0


@dataclass(frozen=True)
class FetchedDocument:
    source_id: str
    title: str
    final_url: str
    content_type: str
    text: str
    sections: tuple[str, ...] = ()
    truncated: bool = False
    content_hash: str = ""
    duration_ms: int = 0


@dataclass(frozen=True)
class SourceAssessment:
    accepted: bool
    policy_status: str = "unknown"
    relevance: str = "unknown"
    quality: str = "unknown"
    reason: str = ""


@dataclass
class ResearchSource:
    source_id: str
    title: str
    url: str
    canonical_url: str
    snippet: str = ""
    rank: int = 0
    provider: str = "unknown"
    final_url: str = ""
    status: str = "discovered"
    error_code: str = ""
    error_message: str = ""
    truncated: bool = False
    content_hash: str = ""
    citation_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "snippet": self.snippet,
            "rank": self.rank,
            "provider": self.provider,
            "final_url": self.final_url,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "truncated": self.truncated,
            "content_hash": self.content_hash,
            "citation_count": self.citation_count,
        }


@dataclass(frozen=True)
class EvidenceChunk:
    source_id: str
    chunk_id: str
    text: str
    heading_path: str = ""
    token_count: int = 0


@dataclass(frozen=True)
class ResearchCitation:
    source_id: str
    marker: str
    claim_context: str = ""


@dataclass(frozen=True)
class ProgressEvent:
    request_id: str
    stage: ResearchStage
    message: str
    completed: int = 0
    total: int = 0
    source_id: str | None = None


@dataclass
class ResearchResult:
    request_id: str
    original_query: str
    effective_query: str
    answer_markdown: str
    sources: list[ResearchSource] = field(default_factory=list)
    citations: list[ResearchCitation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provider_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "original_query": self.original_query,
            "effective_query": self.effective_query,
            "answer_markdown": self.answer_markdown,
            "sources": [source.to_dict() for source in self.sources],
            "citations": [citation.__dict__.copy() for citation in self.citations],
            "warnings": list(self.warnings),
            "provider_snapshot": dict(self.provider_snapshot),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "summary": self.answer_markdown,
            "sources": [source.final_url or source.url for source in self.sources if source.status == "accepted"],
            "query": self.original_query,
            "research_result": self.to_dict(),
        }


ProgressCallback = Callable[[ProgressEvent], None]
