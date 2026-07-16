"""Ports used by the Web Research application service."""

from __future__ import annotations

from typing import Protocol, Sequence

from .domain import (
    CancellationToken,
    FetchedDocument,
    FetchedPayload,
    ResearchLimits,
    SearchResult,
    SourceAssessment,
)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limits: ResearchLimits, token: CancellationToken) -> list[SearchResult]:
        ...


class DocumentFetcher(Protocol):
    def fetch(self, result: SearchResult, *, limits: ResearchLimits, token: CancellationToken) -> FetchedPayload:
        ...


class ContentExtractor(Protocol):
    def extract(self, payload: FetchedPayload, *, limits: ResearchLimits, token: CancellationToken) -> FetchedDocument:
        ...


class ResearchModel(Protocol):
    def refine_query(self, query: str, history: Sequence[dict], *, limits: ResearchLimits, token: CancellationToken) -> str:
        ...

    def assess_source(
        self,
        query: str,
        document: FetchedDocument,
        *,
        limits: ResearchLimits,
        token: CancellationToken,
    ) -> SourceAssessment:
        ...

    def summarize(
        self,
        query: str,
        history: Sequence[dict],
        evidence: Sequence[str],
        *,
        limits: ResearchLimits,
        token: CancellationToken,
    ) -> str:
        ...
