"""Application service for bounded, evidence-first Web Research."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from .domain import (
    CancellationToken,
    EvidenceChunk,
    ProgressCallback,
    ProgressEvent,
    ResearchFailure,
    ResearchLimits,
    ResearchResult,
    ResearchSource,
    ResearchStage,
    SearchResult,
    WebResearchRequest,
)
from .ports import ContentExtractor, DocumentFetcher, ResearchModel, SearchProvider
from .providers import BeautifulSoupContentExtractor, DuckDuckGoSearchProvider, RequestsDocumentFetcher, ApiResearchModel


class WebResearchService:
    def __init__(
        self,
        *,
        search_provider: SearchProvider | None = None,
        fetcher: DocumentFetcher | None = None,
        extractor: ContentExtractor | None = None,
        model: ResearchModel | None = None,
    ):
        self.search_provider = search_provider or DuckDuckGoSearchProvider()
        self.fetcher = fetcher or RequestsDocumentFetcher()
        self.extractor = extractor or BeautifulSoupContentExtractor()
        self.model = model or ApiResearchModel()

    @staticmethod
    def _emit(request: WebResearchRequest, callback: ProgressCallback | None, stage: ResearchStage, message: str, completed: int = 0, total: int = 0, source_id: str | None = None):
        if callback:
            callback(ProgressEvent(request.request_id, stage, message, completed, total, source_id))

    @staticmethod
    def _select_evidence(documents, limits: ResearchLimits, token: CancellationToken) -> list[EvidenceChunk]:
        chunks: list[EvidenceChunk] = []
        total_chars = 0
        total_tokens = 0
        chunk_size = min(limits.max_chars_per_evidence_chunk, limits.max_chars_per_source)
        for document in documents:
            per_source = 0
            sections = document.sections or tuple(document.text.splitlines())
            for index, section in enumerate(sections):
                token.raise_if_cancelled()
                text = " ".join(str(section).split()).strip()
                if not text:
                    continue
                for offset in range(0, len(text), chunk_size):
                    remaining_source_chars = limits.max_chars_per_source - per_source
                    marker_overhead = len(f"[{document.source_id}] ")
                    remaining_evidence_chars = limits.max_evidence_chars - total_chars - marker_overhead
                    remaining_evidence_tokens = limits.max_evidence_tokens - total_tokens
                    if min(remaining_source_chars, remaining_evidence_chars, remaining_evidence_tokens * 4) <= 0:
                        break
                    piece = text[offset : offset + min(chunk_size, remaining_source_chars, remaining_evidence_chars, remaining_evidence_tokens * 4)]
                    piece_tokens = max(1, len(piece) // 4)
                    while piece and piece_tokens > remaining_evidence_tokens:
                        piece = piece[:-4]
                        piece_tokens = max(1, len(piece) // 4)
                    if not piece:
                        break
                    chunks.append(EvidenceChunk(document.source_id, f"{document.source_id}-{index}-{offset}", piece, token_count=piece_tokens))
                    per_source += len(piece)
                    total_chars += len(piece)
                    total_tokens += piece_tokens
        return chunks

    @staticmethod
    def _citation_markers(answer: str) -> set[str]:
        return {marker.lower() for marker in re.findall(r"\[(s\d+(?:-[a-f0-9]+)?)\]", answer, flags=re.IGNORECASE)}

    def run(self, request: WebResearchRequest, *, token: CancellationToken | None = None, progress: ProgressCallback | None = None) -> ResearchResult:
        token = token or CancellationToken()
        query = " ".join(str(request.original_query or "").split()).strip()
        if not query:
            raise ResearchFailure("Query cannot be empty.", code="empty_query", retryable=False)
        if len(query) > request.limits.max_query_chars:
            raise ResearchFailure("Query is too long for web research.", code="query_too_long", retryable=False)

        self._emit(request, progress, ResearchStage.PREPARING, "Preparing research request.")
        effective_query = self.model.refine_query(query, request.branch_history, limits=request.limits, token=token)
        token.raise_if_cancelled()

        self._emit(request, progress, ResearchStage.SEARCHING, "Searching for relevant sources.")
        try:
            search_results = self.search_provider.search(effective_query, limits=request.limits, token=token)
        except ResearchFailure:
            raise
        except Exception as exc:
            raise ResearchFailure("The search provider failed.", code="search_failed") from exc
        if not search_results:
            raise ResearchFailure("No search results were found for this query.", code="no_search_results", retryable=False)

        source_records: list[ResearchSource] = []
        accepted_documents = []
        warnings: list[str] = []
        candidates = search_results[: request.limits.max_sources]
        for index, result in enumerate(candidates, start=1):
            token.raise_if_cancelled()
            source = ResearchSource(
                source_id=result.source_id,
                title=result.title,
                url=result.url,
                canonical_url=result.canonical_url,
                snippet=result.snippet,
                rank=result.rank,
                provider=result.provider,
                status="fetching",
            )
            source_records.append(source)
            self._emit(request, progress, ResearchStage.FETCHING, f"Fetching source {index} of {len(candidates)}.", index - 1, len(candidates), result.source_id)
            try:
                payload = self.fetcher.fetch(result, limits=request.limits, token=token)
                source.final_url = payload.final_url
                source.truncated = payload.truncated
                self._emit(request, progress, ResearchStage.EXTRACTING, f"Extracting source {index} of {len(candidates)}.", index - 1, len(candidates), result.source_id)
                document = self.extractor.extract(payload, limits=request.limits, token=token)
                source.title = document.title or source.title
                source.final_url = document.final_url
                source.content_hash = document.content_hash
                self._emit(request, progress, ResearchStage.VALIDATING, f"Assessing source {index} of {len(candidates)}.", index, len(candidates), result.source_id)
                assessment = self.model.assess_source(effective_query, document, limits=request.limits, token=token)
                if not assessment.accepted:
                    source.status = "rejected"
                    source.error_code = assessment.reason or "source_rejected"
                    warnings.append(f"Source {index} was not used ({source.error_code}).")
                    continue
                source.status = "accepted"
                accepted_documents.append(document)
                if document.truncated:
                    warnings.append(f"Source {index} was truncated to stay within limits.")
            except ResearchFailure as exc:
                if exc.code == "cancelled":
                    raise
                source.status = "failed"
                source.error_code = exc.code
                source.error_message = str(exc)
                warnings.append(f"Source {index} could not be used ({exc.code}).")
            except Exception as exc:
                source.status = "failed"
                source.error_code = "source_failed"
                source.error_message = "The source failed during research."
                warnings.append(f"Source {index} could not be used.")

        if not accepted_documents:
            raise ResearchFailure("No usable source content could be retrieved.", code="no_usable_sources", retryable=True)

        chunks = self._select_evidence(accepted_documents, request.limits, token)
        if not chunks:
            raise ResearchFailure("Usable sources did not contain bounded evidence.", code="no_evidence", retryable=False)
        evidence_payload = [f"[{chunk.source_id}] {chunk.text}" for chunk in chunks]
        self._emit(request, progress, ResearchStage.SYNTHESIZING, "Synthesizing a cited answer.", len(candidates), len(candidates))
        answer = self.model.summarize(query, request.branch_history, evidence_payload, limits=request.limits, token=token)
        markers = self._citation_markers(answer)
        citations = []
        for source in source_records:
            if source.status != "accepted":
                continue
            source_marker = source.source_id.lower()
            source.citation_count = 1 if source_marker in markers else 0
            citations.append({"source_id": source.source_id, "marker": f"[{source.source_id}]"})
        if not markers:
            warnings.append("The model did not emit inline citations; source references are shown below.")
            answer = answer.rstrip() + "\n\n### Sources\n" + "\n".join(f"- [{source.source_id}] {source.title}" for source in source_records if source.status == "accepted")

        from .domain import ResearchCitation

        result = ResearchResult(
            request_id=request.request_id,
            original_query=query,
            effective_query=effective_query,
            answer_markdown=answer,
            sources=source_records,
            citations=[ResearchCitation(**citation) for citation in citations],
            warnings=warnings,
            provider_snapshot=dict(request.provider_snapshot),
        )
        self._emit(request, progress, ResearchStage.COMPLETED, "Research completed.", len(candidates), len(candidates))
        return result
