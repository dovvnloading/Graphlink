"""Contract tests for the production Web Research service boundary."""

import sys
import socket
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_plugins.web_research.domain import (
    CancellationToken,
    FetchedDocument,
    FetchedPayload,
    RequestCancelled,
    ResearchFailure,
    ResearchLimits,
    SearchResult,
    SourceAssessment,
    WebResearchRequest,
)
from graphlink_plugins.web_research.fetch_policy import FetchPolicy, URLPolicyError
from graphlink_plugins.web_research import providers
from graphlink_plugins.web_research.providers import RequestsDocumentFetcher
from graphlink_plugins.web_research.service import WebResearchService


class FakeSearchProvider:
    name = "fake"

    def search(self, query, *, limits, token):
        return [
            SearchResult("s1", "First source", "https://example.com/one", "https://example.com/one", "first", 1, self.name),
            SearchResult("s2", "Second source", "https://example.com/two", "https://example.com/two", "second", 2, self.name),
        ]


class FakeFetcher:
    def fetch(self, result, *, limits, token):
        if result.source_id == "s2":
            raise ResearchFailure("blocked", code="url_blocked_by_policy", source_id=result.source_id)
        return FetchedPayload(result.source_id, result.url, result.url, "text/html", b"ignored")


class FakeExtractor:
    def extract(self, payload, *, limits, token):
        return FetchedDocument(
            payload.source_id,
            "Readable source",
            payload.final_url,
            payload.content_type,
            "paragraph one " * 100,
            ("paragraph one " * 100,),
        )


class FakeModel:
    def __init__(self):
        self.evidence = []

    def refine_query(self, query, history, *, limits, token):
        return query

    def assess_source(self, query, document, *, limits, token):
        return SourceAssessment(True, "allow", "high", "high", "ok")

    def summarize(self, query, history, evidence, *, limits, token):
        self.evidence = list(evidence)
        return "A cited answer [s1]."


def _request(**kwargs):
    original_query = kwargs.pop("original_query", "What happened?")
    return WebResearchRequest(
        request_id="request-1",
        node_id="node-1",
        chat_epoch=1,
        original_query=original_query,
        limits=ResearchLimits(max_sources=2, max_chars_per_source=300, max_evidence_chars=240, max_evidence_tokens=100),
        **kwargs,
    )


def test_service_returns_typed_result_and_partial_source_warning():
    model = FakeModel()
    service = WebResearchService(
        search_provider=FakeSearchProvider(),
        fetcher=FakeFetcher(),
        extractor=FakeExtractor(),
        model=model,
    )

    result = service.run(_request())

    assert result.request_id == "request-1"
    assert result.effective_query == "What happened?"
    assert result.sources[0].status == "accepted"
    assert result.sources[1].status == "failed"
    assert result.sources[1].error_code == "url_blocked_by_policy"
    assert result.citations[0].source_id == "s1"
    assert any("Source 2" in warning for warning in result.warnings)
    assert sum(len(item) for item in model.evidence) <= 240


def test_service_rejects_empty_query_before_provider_calls():
    service = WebResearchService(search_provider=FakeSearchProvider(), fetcher=FakeFetcher(), extractor=FakeExtractor(), model=FakeModel())
    with pytest.raises(ResearchFailure) as raised:
        service.run(_request(original_query="   "))
    assert raised.value.code == "empty_query"


def test_service_honors_cancellation_before_network_work():
    token = CancellationToken()
    token.cancel()
    service = WebResearchService(search_provider=FakeSearchProvider(), fetcher=FakeFetcher(), extractor=FakeExtractor(), model=FakeModel())
    with pytest.raises(RequestCancelled):
        service.run(_request(), token=token)


def test_fetch_policy_rejects_private_addresses_and_credentials():
    policy = FetchPolicy(resolver=lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    with pytest.raises(URLPolicyError):
        policy.validate("https://127.0.0.1/private")
    with pytest.raises(URLPolicyError):
        policy.validate("https://user:password@example.com/secret")


def test_fetch_policy_rechecks_resolved_host_for_public_dns():
    policy = FetchPolicy(resolver=lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))])
    with pytest.raises(URLPolicyError):
        policy.validate("https://example.com/private")


def test_fetcher_revalidates_redirect_destination_before_following(monkeypatch):
    class RedirectResponse:
        status_code = 302
        headers = {"Location": "https://127.0.0.1/private"}
        is_redirect = True
        is_permanent_redirect = False

        def close(self):
            pass

    class FakeSession:
        trust_env = True
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return RedirectResponse()

    monkeypatch.setattr(providers.requests, "Session", FakeSession)
    policy = FetchPolicy(resolver=lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    fetcher = RequestsDocumentFetcher(policy)

    with pytest.raises(ResearchFailure) as raised:
        fetcher.fetch(
            SearchResult("s1", "Source", "https://example.com", "https://example.com", rank=1),
            limits=ResearchLimits(),
            token=CancellationToken(),
        )

    assert raised.value.code == "url_blocked_by_policy"
