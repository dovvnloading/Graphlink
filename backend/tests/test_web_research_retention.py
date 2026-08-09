"""ADR-017 stage 17.5: Web Research's own opt-in retention of accepted
source documents into the local knowledge store
(WebResearchRequest.retain_to_knowledge -> WebResearchService._retain_
documents -> backend.knowledge_ingest.ingest_text).

No existing backend/tests file drives WebResearchService.run() through its
REAL body with fake ports (every WebResearchService.run() reference in
backend/tests/test_agents.py monkeypatches the whole method as an opaque
seam) - this file builds the minimal fakes for all four ports
(graphlink_plugins/web_research/ports.py) needed to drive one accepted
source through a real run(), since retention is wired inside run()'s own
body, not reachable by patching run() itself away."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from graphlink_plugins.web_research.domain import (
    FetchedDocument,
    FetchedPayload,
    ResearchLimits,
    SearchResult,
    SourceAssessment,
    WebResearchRequest,
)
from graphlink_plugins.web_research.service import WebResearchService


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results):
        self.results = results

    def search(self, query, *, limits, token):
        return self.results


class FakeFetcher:
    def fetch(self, result, *, limits, token):
        return FetchedPayload(
            source_id=result.source_id, requested_url=result.url, final_url=result.url,
            content_type="text/html", body=b"<html>ignored - FakeExtractor supplies the real text</html>",
        )


class FakeExtractor:
    def __init__(self, documents_by_source_id):
        self.documents_by_source_id = documents_by_source_id

    def extract(self, payload, *, limits, token):
        return self.documents_by_source_id[payload.source_id]


class FakeModel:
    def refine_query(self, query, history, *, limits, token):
        return query

    def assess_source(self, query, document, *, limits, token):
        return SourceAssessment(accepted=True)

    def summarize(self, query, history, evidence, *, limits, token):
        return "A synthesized answer [s1]."


def _service(document_text="Retained page content about widgets."):
    search_result = SearchResult(
        source_id="s1", title="Widget Facts", url="https://example.com/widgets", canonical_url="https://example.com/widgets",
    )
    document = FetchedDocument(
        source_id="s1", title="Widget Facts", final_url="https://example.com/widgets",
        content_type="text/html", text=document_text, sections=(document_text,),
    )
    service = WebResearchService(
        search_provider=FakeSearchProvider([search_result]),
        fetcher=FakeFetcher(),
        extractor=FakeExtractor({"s1": document}),
        model=FakeModel(),
    )
    return service


def _request(*, retain_to_knowledge=False):
    return WebResearchRequest(
        request_id="r1", node_id="n1", chat_epoch=1, original_query="widgets",
        retain_to_knowledge=retain_to_knowledge,
    )


class TestRetentionGating:
    def test_default_never_retains_anything(self):
        service = _service()
        with patch("backend.knowledge_ingest.ingest_text") as mock_ingest:
            result = service.run(_request())
        mock_ingest.assert_not_called()
        assert result.answer_markdown  # the run still succeeds normally

    def test_opting_in_retains_every_accepted_document(self):
        service = _service(document_text="Retained page content about widgets.")
        with patch("backend.knowledge_ingest.ingest_text") as mock_ingest:
            service.run(_request(retain_to_knowledge=True))
        mock_ingest.assert_called_once()
        _, kwargs = mock_ingest.call_args
        assert mock_ingest.call_args[0][0] == "Retained page content about widgets."
        assert kwargs["source_uri"] == "https://example.com/widgets"
        assert kwargs["title"] == "Widget Facts"
        assert kwargs["mime"] == "text/html"

    def test_a_retention_failure_never_breaks_the_research_result(self):
        service = _service()
        with patch("backend.knowledge_ingest.ingest_text", side_effect=RuntimeError("disk full")):
            result = service.run(_request(retain_to_knowledge=True))
        assert result.answer_markdown  # the primary operation still succeeded


class TestRetainDocumentsUnit:
    def test_each_document_is_ingested_with_its_own_title_and_final_url(self):
        documents = [
            FetchedDocument(source_id="a", title="A", final_url="https://a.example/", content_type="text/html", text="content a"),
            FetchedDocument(source_id="b", title="B", final_url="https://b.example/", content_type="text/html", text="content b"),
        ]
        sources = [
            type("S", (), {"source_id": "a", "title": "A"})(),
            type("S", (), {"source_id": "b", "title": "B"})(),
        ]
        with patch("backend.knowledge_ingest.ingest_text") as mock_ingest:
            WebResearchService._retain_documents(documents, sources)
        assert mock_ingest.call_count == 2
        calls_by_uri = {c.kwargs["source_uri"]: c for c in mock_ingest.call_args_list}
        assert calls_by_uri["https://a.example/"].args[0] == "content a"
        assert calls_by_uri["https://a.example/"].kwargs["title"] == "A"
        assert calls_by_uri["https://b.example/"].args[0] == "content b"

    def test_one_documents_ingest_failure_does_not_block_the_next(self):
        documents = [
            FetchedDocument(source_id="a", title="A", final_url="https://a.example/", content_type="text/html", text="content a"),
            FetchedDocument(source_id="b", title="B", final_url="https://b.example/", content_type="text/html", text="content b"),
        ]
        sources = [
            type("S", (), {"source_id": "a", "title": "A"})(),
            type("S", (), {"source_id": "b", "title": "B"})(),
        ]
        with patch(
            "backend.knowledge_ingest.ingest_text", side_effect=[RuntimeError("boom"), None],
        ) as mock_ingest:
            WebResearchService._retain_documents(documents, sources)
        assert mock_ingest.call_count == 2  # the second call still happened
