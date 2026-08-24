"""ADR-014 H3: graphlink_plugins/web_research/domain.py and service.py had
ZERO direct unit tests. This file closes that gap for the CORE orchestration
logic - domain.py's value/error contracts, and WebResearchService's own
behavior (evidence selection, citation formatting, source aggregation, error
handling) - using fake in-process implementations of ports.py's four
Protocols (SearchProvider, DocumentFetcher, ContentExtractor, ResearchModel).
No real network call is made anywhere in this file.

Deliberately out of scope (covered elsewhere / by a different increment):
  - providers.py, fetch_policy.py, crawl_etiquette.py (the network-fetching
    layer: SSRF/IP-pinning, robots.txt, redirects, timeouts) - see
    test_web_research_fetch_policy.py, test_web_research_crawl_etiquette.py,
    test_web_research_providers_fetcher.py.
  - WebResearchService._retain_documents / retain_to_knowledge wiring -
    already has dedicated, thorough coverage in
    test_web_research_retention.py; not re-covered here beyond incidental
    exercise (every request built below defaults retain_to_knowledge=False).
"""

from __future__ import annotations

import pytest

from graphlink_plugins.web_research.domain import (
    CancellationToken,
    FetchedDocument,
    FetchedPayload,
    ProgressEvent,
    RequestCancelled,
    ResearchCitation,
    ResearchFailure,
    ResearchLimits,
    ResearchResult,
    ResearchSource,
    ResearchStage,
    SearchResult,
    SourceAssessment,
    WebResearchRequest,
)
from graphlink_plugins.web_research.service import WebResearchService


# ---------------------------------------------------------------------------
# Fakes for the four ports.py Protocols. Kept intentionally simple (canned
# results / scripted errors keyed by source_id) rather than MagicMock, so the
# test bodies below stay readable - mirrors test_web_research_retention.py's
# existing Fake* convention.
# ---------------------------------------------------------------------------


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results, *, error=None):
        self._results = list(results)
        self._error = error
        self.calls: list[str] = []

    def search(self, query, *, limits, token):
        self.calls.append(query)
        if self._error is not None:
            raise self._error
        return list(self._results)


class FakeFetcher:
    def __init__(self, *, payloads=None, errors=None):
        self._payloads = payloads or {}
        self._errors = errors or {}
        self.calls: list[str] = []

    def fetch(self, result, *, limits, token):
        self.calls.append(result.source_id)
        if result.source_id in self._errors:
            raise self._errors[result.source_id]
        payload = self._payloads.get(result.source_id)
        if payload is not None:
            return payload
        return FetchedPayload(
            source_id=result.source_id,
            requested_url=result.url,
            final_url=result.url,
            content_type="text/html",
            body=b"<html>ignored - FakeExtractor supplies the real text</html>",
        )


class FakeExtractor:
    def __init__(self, documents=None, *, errors=None):
        self._documents = documents or {}
        self._errors = errors or {}
        self.calls: list[str] = []

    def extract(self, payload, *, limits, token):
        self.calls.append(payload.source_id)
        if payload.source_id in self._errors:
            raise self._errors[payload.source_id]
        document = self._documents.get(payload.source_id)
        if document is not None:
            return document
        return _document(payload.source_id, sections=("Default fake content.",), final_url=payload.final_url)


class FakeModel:
    def __init__(self, *, refined_query=None, assessments=None, answer="A synthesized answer [s1].", refine_error=None):
        self._refined_query = refined_query
        self._assessments = assessments or {}
        self._answer = answer
        self._refine_error = refine_error
        self.refine_calls: list[tuple] = []
        self.assess_calls: list[str] = []
        self.summarize_calls: list[list[str]] = []

    def refine_query(self, query, history, *, limits, token):
        self.refine_calls.append((query, history))
        if self._refine_error is not None:
            raise self._refine_error
        return self._refined_query if self._refined_query is not None else query

    def assess_source(self, query, document, *, limits, token):
        self.assess_calls.append(document.source_id)
        return self._assessments.get(document.source_id, SourceAssessment(accepted=True))

    def summarize(self, query, history, evidence, *, limits, token):
        self.summarize_calls.append(list(evidence))
        return self._answer


# ---------------------------------------------------------------------------
# Construction helpers.
# ---------------------------------------------------------------------------


def _search_result(source_id="s1", *, url=None, title=None, rank=0, snippet=""):
    url = url or f"https://example.com/{source_id}"
    return SearchResult(source_id=source_id, title=title or f"Title {source_id}", url=url, canonical_url=url, snippet=snippet, rank=rank, provider="fake")


def _document(source_id, *, text="", sections=(), title="Doc", final_url=None, truncated=False, content_hash=""):
    return FetchedDocument(
        source_id=source_id,
        title=title,
        final_url=final_url or f"https://example.com/{source_id}",
        content_type="text/html",
        text=text,
        sections=sections,
        truncated=truncated,
        content_hash=content_hash,
    )


def _request(*, query="widgets", history=None, limits=None, retain_to_knowledge=False, provider_snapshot=None, request_id="r1"):
    return WebResearchRequest(
        request_id=request_id,
        node_id="n1",
        chat_epoch=1,
        original_query=query,
        branch_history=history or [],
        limits=limits or ResearchLimits(),
        provider_snapshot=provider_snapshot or {},
        retain_to_knowledge=retain_to_knowledge,
    )


def _service(*, search=None, fetcher=None, extractor=None, model=None):
    return WebResearchService(
        search_provider=search or FakeSearchProvider([_search_result("s1")]),
        fetcher=fetcher or FakeFetcher(),
        extractor=extractor or FakeExtractor({"s1": _document("s1", sections=("Widgets are great.",))}),
        model=model or FakeModel(),
    )


# ===========================================================================
# domain.py: value objects, error types, cancellation primitive
# ===========================================================================


class TestResearchLimitsValidation:
    @pytest.mark.parametrize(
        "field_name",
        [
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
        ],
    )
    def test_zero_value_raises_value_error(self, field_name):
        with pytest.raises(ValueError, match=field_name):
            ResearchLimits(**{field_name: 0})

class TestCancellationToken:
    def test_raise_if_cancelled_raises_request_cancelled_once_cancelled(self):
        token = CancellationToken()
        token.cancel()
        with pytest.raises(RequestCancelled):
            token.raise_if_cancelled()

    def test_request_cancelled_is_retryable(self):
        assert RequestCancelled.retryable is True
        assert RequestCancelled.code == "cancelled"


class TestWebResearchRequestDefaults:
    def test_retain_to_knowledge_defaults_to_false(self):
        request = WebResearchRequest(request_id="r", node_id="n", chat_epoch=1, original_query="q")
        assert request.retain_to_knowledge is False

class TestResearchSourceToDict:
    def test_round_trips_every_field(self):
        source = ResearchSource(
            source_id="s1",
            title="T",
            url="https://x",
            canonical_url="https://x",
            snippet="snip",
            rank=1,
            provider="ddg",
            final_url="https://y",
            status="accepted",
            error_code="",
            error_message="",
            truncated=True,
            content_hash="abc",
            citation_count=2,
        )
        assert source.to_dict() == {
            "source_id": "s1",
            "title": "T",
            "url": "https://x",
            "canonical_url": "https://x",
            "snippet": "snip",
            "rank": 1,
            "provider": "ddg",
            "final_url": "https://y",
            "status": "accepted",
            "error_code": "",
            "error_message": "",
            "truncated": True,
            "content_hash": "abc",
            "citation_count": 2,
        }


class TestResearchResultToDict:
    def test_serializes_nested_sources_and_citations(self):
        source = ResearchSource(source_id="s1", title="T", url="u", canonical_url="u", status="accepted")
        citation = ResearchCitation(source_id="s1", marker="[s1]", claim_context="ctx")
        result = ResearchResult(
            request_id="r1",
            original_query="q",
            effective_query="q2",
            answer_markdown="answer [s1]",
            sources=[source],
            citations=[citation],
            warnings=["w1"],
            provider_snapshot={"provider": "anthropic"},
        )
        d = result.to_dict()
        assert d["request_id"] == "r1"
        assert d["sources"] == [source.to_dict()]
        assert d["citations"] == [{"source_id": "s1", "marker": "[s1]", "claim_context": "ctx"}]
        assert d["warnings"] == ["w1"]
        assert d["provider_snapshot"] == {"provider": "anthropic"}


class TestResearchResultToLegacyDict:
    def test_only_accepted_sources_are_included_preferring_final_url_over_url(self):
        accepted_with_final = ResearchSource(source_id="s1", title="A", url="https://a", canonical_url="https://a", final_url="https://a-final", status="accepted")
        accepted_no_final = ResearchSource(source_id="s2", title="B", url="https://b", canonical_url="https://b", final_url="", status="accepted")
        rejected = ResearchSource(source_id="s3", title="C", url="https://c", canonical_url="https://c", status="rejected")
        failed = ResearchSource(source_id="s4", title="D", url="https://d", canonical_url="https://d", status="failed")
        result = ResearchResult(
            request_id="r1",
            original_query="q",
            effective_query="q",
            answer_markdown="answer",
            sources=[accepted_with_final, accepted_no_final, rejected, failed],
        )
        legacy = result.to_legacy_dict()
        assert legacy["sources"] == ["https://a-final", "https://b"]
        assert legacy["summary"] == "answer"
        assert legacy["query"] == "q"
        assert legacy["research_result"] == result.to_dict()


# ===========================================================================
# WebResearchService: construction
# ===========================================================================


class TestServiceConstruction:
    def test_defaults_to_the_real_adapter_classes_when_none_given(self):
        from graphlink_plugins.web_research.providers import (
            ApiResearchModel,
            BeautifulSoupContentExtractor,
            DuckDuckGoSearchProvider,
            RequestsDocumentFetcher,
        )

        service = WebResearchService()
        assert isinstance(service.search_provider, DuckDuckGoSearchProvider)
        assert isinstance(service.fetcher, RequestsDocumentFetcher)
        assert isinstance(service.extractor, BeautifulSoupContentExtractor)
        assert isinstance(service.model, ApiResearchModel)

    def test_explicit_overrides_are_used_verbatim_not_wrapped(self):
        search = FakeSearchProvider([])
        fetcher = FakeFetcher()
        extractor = FakeExtractor()
        model = FakeModel()
        service = WebResearchService(search_provider=search, fetcher=fetcher, extractor=extractor, model=model)
        assert service.search_provider is search
        assert service.fetcher is fetcher
        assert service.extractor is extractor
        assert service.model is model


# ===========================================================================
# WebResearchService._emit
# ===========================================================================


class TestEmit:
    def test_calls_the_callback_with_a_fully_populated_progress_event(self):
        request = _request(request_id="req-1")
        events: list[ProgressEvent] = []
        WebResearchService._emit(request, events.append, ResearchStage.SEARCHING, "msg", 1, 2, "s1")
        assert events == [ProgressEvent("req-1", ResearchStage.SEARCHING, "msg", 1, 2, "s1")]

# ===========================================================================
# WebResearchService._citation_markers
# ===========================================================================


class TestCitationMarkers:
    def test_extracts_simple_markers(self):
        assert WebResearchService._citation_markers("See [s1] and [s2].") == {"s1", "s2"}

    def test_dedups_repeated_markers_into_a_set(self):
        assert WebResearchService._citation_markers("[s1] blah [s1] blah") == {"s1"}

    def test_ignores_bracketed_text_that_does_not_match_the_marker_shape(self):
        assert WebResearchService._citation_markers("[not a marker] [source1] [s1]") == {"s1"}


# ===========================================================================
# WebResearchService._select_evidence
# ===========================================================================


class TestSelectEvidence:
    def test_multiple_sections_produce_multiple_chunks_indexed_in_the_chunk_id(self):
        doc = _document("s1", sections=("First section.", "Second section."))
        chunks = WebResearchService._select_evidence([doc], ResearchLimits(), CancellationToken())
        assert [c.chunk_id for c in chunks] == ["s1-0-0", "s1-1-0"]
        assert [c.text for c in chunks] == ["First section.", "Second section."]

    def test_blank_and_whitespace_only_sections_are_skipped_but_index_still_reflects_position(self):
        doc = _document("s1", sections=("", "   ", "Real content."))
        chunks = WebResearchService._select_evidence([doc], ResearchLimits(), CancellationToken())
        assert len(chunks) == 1
        assert chunks[0].text == "Real content."
        assert chunks[0].chunk_id == "s1-2-0"

    def test_falls_back_to_splitting_text_into_lines_when_sections_is_empty(self):
        doc = _document("s1", text="Line A\nLine B", sections=())
        chunks = WebResearchService._select_evidence([doc], ResearchLimits(), CancellationToken())
        assert [c.text for c in chunks] == ["Line A", "Line B"]

    def test_a_long_section_is_split_across_multiple_offset_chunks(self):
        limits = ResearchLimits(max_chars_per_evidence_chunk=10, max_chars_per_source=1000)
        doc = _document("s1", sections=("a" * 25,))
        chunks = WebResearchService._select_evidence([doc], limits, CancellationToken())
        assert [c.chunk_id for c in chunks] == ["s1-0-0", "s1-0-10", "s1-0-20"]
        assert [len(c.text) for c in chunks] == [10, 10, 5]

    def test_max_chars_per_source_caps_one_documents_total_but_the_next_document_gets_its_own_budget(self):
        limits = ResearchLimits(max_chars_per_evidence_chunk=10, max_chars_per_source=15)
        doc_a = _document("a", sections=("a" * 30,))
        doc_b = _document("b", sections=("b" * 30,))
        chunks = WebResearchService._select_evidence([doc_a, doc_b], limits, CancellationToken())
        a_total = sum(len(c.text) for c in chunks if c.source_id == "a")
        b_total = sum(len(c.text) for c in chunks if c.source_id == "b")
        assert a_total == 15
        assert b_total == 15

    def test_max_evidence_chars_caps_the_total_across_all_documents(self):
        limits = ResearchLimits(max_chars_per_evidence_chunk=1000, max_chars_per_source=1000, max_evidence_chars=12)
        doc_a = _document("a", sections=("a" * 20,))
        doc_b = _document("b", sections=("b" * 20,))
        chunks = WebResearchService._select_evidence([doc_a, doc_b], limits, CancellationToken())
        assert [c.source_id for c in chunks] == ["a"]
        assert chunks[0].text == "a" * 8  # 12 - len("[a] ") == 8 chars of budget left

    def test_max_evidence_tokens_bounds_a_pieces_length_to_four_chars_per_token(self):
        limits = ResearchLimits(max_chars_per_evidence_chunk=1000, max_chars_per_source=1000, max_evidence_tokens=2)
        doc = _document("a", sections=("a" * 40,))
        chunks = WebResearchService._select_evidence([doc], limits, CancellationToken())
        assert len(chunks) == 1
        assert chunks[0].token_count <= 2
        assert len(chunks[0].text) <= 8

    def test_raises_request_cancelled_when_the_token_is_already_cancelled(self):
        doc = _document("a", sections=("first",))
        token = CancellationToken()
        token.cancel()
        with pytest.raises(RequestCancelled):
            WebResearchService._select_evidence([doc], ResearchLimits(), token)


# ===========================================================================
# WebResearchService.run - query validation
# ===========================================================================


class TestRunQueryValidation:
    def test_empty_query_raises_before_any_search_call(self):
        search = FakeSearchProvider([_search_result("s1")])
        service = _service(search=search)
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request(query="   "))
        assert exc_info.value.code == "empty_query"
        assert exc_info.value.retryable is False
        assert search.calls == []

    def test_query_over_the_configured_limit_raises_query_too_long(self):
        service = _service()
        limits = ResearchLimits(max_query_chars=5)
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request(query="a very long query", limits=limits))
        assert exc_info.value.code == "query_too_long"
        assert exc_info.value.retryable is False

    def test_query_whitespace_is_collapsed_before_being_sent_to_search(self):
        search = FakeSearchProvider([_search_result("s1")])
        service = _service(search=search)
        service.run(_request(query="  widgets   for   home  "))
        assert search.calls == ["widgets for home"]


# ===========================================================================
# WebResearchService.run - search-stage failures
# ===========================================================================


class TestRunSearchFailures:
    def test_a_research_failure_from_the_search_provider_propagates_unchanged(self):
        boom = ResearchFailure("search down", code="custom_search_fail")
        service = _service(search=FakeSearchProvider([], error=boom))
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request())
        assert exc_info.value is boom

    def test_a_generic_exception_from_the_search_provider_is_wrapped_as_search_failed(self):
        service = _service(search=FakeSearchProvider([], error=RuntimeError("timeout")))
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request())
        assert exc_info.value.code == "search_failed"
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    def test_no_search_results_raises_no_search_results_and_is_not_retryable(self):
        service = _service(search=FakeSearchProvider([]))
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request())
        assert exc_info.value.code == "no_search_results"
        assert exc_info.value.retryable is False


# ===========================================================================
# WebResearchService.run - source selection / aggregation
# ===========================================================================


class TestRunSourceSelection:
    def test_candidates_are_truncated_to_limits_max_sources(self):
        results = [_search_result(f"s{i}") for i in range(1, 6)]
        fetcher = FakeFetcher()
        extractor = FakeExtractor({f"s{i}": _document(f"s{i}", sections=(f"Content {i}.",)) for i in range(1, 6)})
        service = _service(search=FakeSearchProvider(results), fetcher=fetcher, extractor=extractor)
        result = service.run(_request(limits=ResearchLimits(max_sources=2)))
        assert len(result.sources) == 2
        assert fetcher.calls == ["s1", "s2"]

    def test_accepted_rejected_and_failed_sources_all_land_with_the_right_status(self):
        results = [_search_result("accepted"), _search_result("rejected"), _search_result("failed")]
        extractor = FakeExtractor(
            {
                "accepted": _document("accepted", sections=("Good content.",)),
                "rejected": _document("rejected", sections=("Bad content.",)),
            }
        )
        fetcher = FakeFetcher(errors={"failed": ResearchFailure("dead link", code="fetch_dead")})
        model = FakeModel(assessments={"rejected": SourceAssessment(accepted=False, reason="off_topic")}, answer="Answer citing [accepted].")
        service = _service(search=FakeSearchProvider(results), fetcher=fetcher, extractor=extractor, model=model)
        result = service.run(_request())

        by_id = {s.source_id: s for s in result.sources}
        assert by_id["accepted"].status == "accepted"
        assert by_id["rejected"].status == "rejected"
        assert by_id["rejected"].error_code == "off_topic"
        assert by_id["failed"].status == "failed"
        assert by_id["failed"].error_code == "fetch_dead"
        assert any("was not used" in w for w in result.warnings)
        assert any("could not be used" in w for w in result.warnings)

    def test_rejection_without_an_explicit_reason_defaults_error_code_to_source_rejected(self):
        results = [_search_result("s1"), _search_result("s2")]
        extractor = FakeExtractor(
            {
                "s1": _document("s1", sections=("Rejected content.",)),
                "s2": _document("s2", sections=("Accepted content.",)),
            }
        )
        model = FakeModel(assessments={"s1": SourceAssessment(accepted=False)}, answer="Answer [s2].")
        service = _service(search=FakeSearchProvider(results), extractor=extractor, model=model)
        result = service.run(_request())
        by_id = {s.source_id: s for s in result.sources}
        assert by_id["s1"].status == "rejected"
        assert by_id["s1"].error_code == "source_rejected"

    def test_a_payload_level_truncation_is_recorded_on_the_source_record(self):
        # source.truncated is populated from the FETCH-stage payload (network
        # byte-cap truncation), set right after fetch() returns.
        payload = FetchedPayload(
            source_id="s1", requested_url="https://example.com/s1", final_url="https://example.com/s1",
            content_type="text/html", body=b"<html>hi</html>", truncated=True,
        )
        fetcher = FakeFetcher(payloads={"s1": payload})
        extractor = FakeExtractor({"s1": _document("s1", sections=("Some content.",))})
        service = _service(fetcher=fetcher, extractor=extractor)
        result = service.run(_request())
        assert result.sources[0].status == "accepted"
        assert result.sources[0].truncated is True

    def test_a_document_level_truncation_still_warns_and_still_accepts_even_though_it_never_updates_source_truncated(self):
        # KNOWN GAP (flagged, not fixed here - out of this test file's
        # remit): the "Source N was truncated..." warning is driven by
        # document.truncated (the EXTRACT-stage/content-layer signal), but
        # ResearchSource.truncated is only ever assigned once, right after
        # fetch(), from payload.truncated (the FETCH-stage/network-layer
        # signal) - see service.py's run(). It is never re-synced from
        # document.truncated afterward, so a source whose *content* was cut
        # short during extraction (this test's case) still reports
        # truncated=False on the ResearchSource/to_dict() record, even
        # though the warning text told the user it was truncated. This
        # test pins the CURRENT (inconsistent) behavior rather than the
        # arguably-more-correct one, since fixing it would mean editing
        # service.py, which is out of scope for this test-only file.
        extractor = FakeExtractor({"s1": _document("s1", sections=("Some content.",), truncated=True)})
        service = _service(extractor=extractor)
        result = service.run(_request())
        assert result.sources[0].status == "accepted"
        assert any("truncated" in w for w in result.warnings)
        assert result.sources[0].truncated is False  # NOT True - see docstring above


# ===========================================================================
# WebResearchService.run - error handling
# ===========================================================================


class TestRunErrorHandling:
    def test_a_non_cancelled_fetch_failure_marks_that_source_failed_but_the_run_continues(self):
        results = [_search_result("bad"), _search_result("good")]
        fetcher = FakeFetcher(errors={"bad": ResearchFailure("blocked", code="url_blocked_by_policy")})
        extractor = FakeExtractor({"good": _document("good", sections=("Fine content.",))})
        service = _service(search=FakeSearchProvider(results), fetcher=fetcher, extractor=extractor)
        result = service.run(_request())
        by_id = {s.source_id: s for s in result.sources}
        assert by_id["bad"].status == "failed"
        assert by_id["bad"].error_code == "url_blocked_by_policy"
        assert by_id["good"].status == "accepted"

    def test_a_cancelled_fetch_aborts_the_whole_run_instead_of_being_recorded_as_a_failed_source(self):
        results = [_search_result("s1"), _search_result("s2")]
        fetcher = FakeFetcher(errors={"s1": ResearchFailure("stop", code="cancelled")})
        service = _service(search=FakeSearchProvider(results), fetcher=fetcher)
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request())
        assert exc_info.value.code == "cancelled"
        assert fetcher.calls == ["s1"]  # s2 was never attempted - the run aborted immediately

    def test_a_generic_exception_is_recorded_with_a_safe_message_not_the_raw_exception_text(self):
        results = [_search_result("bad"), _search_result("good")]
        extractor = FakeExtractor({"good": _document("good", sections=("Fine.",))}, errors={"bad": RuntimeError("raw internal detail")})
        service = _service(search=FakeSearchProvider(results), extractor=extractor)
        result = service.run(_request())
        by_id = {s.source_id: s for s in result.sources}
        assert by_id["bad"].status == "failed"
        assert by_id["bad"].error_code == "source_failed"
        assert by_id["bad"].error_message == "The source failed during research."
        assert "raw internal detail" not in by_id["bad"].error_message

    def test_no_usable_sources_raised_when_every_candidate_is_rejected_or_failed(self):
        results = [_search_result("rej"), _search_result("fail")]
        extractor = FakeExtractor({"rej": _document("rej", sections=("x",))})
        fetcher = FakeFetcher(errors={"fail": ResearchFailure("dead", code="fetch_dead")})
        model = FakeModel(assessments={"rej": SourceAssessment(accepted=False, reason="no")})
        service = _service(search=FakeSearchProvider(results), fetcher=fetcher, extractor=extractor, model=model)
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request())
        assert exc_info.value.code == "no_usable_sources"
        assert exc_info.value.retryable is True

    def test_no_evidence_raised_when_accepted_documents_have_no_extractable_text(self):
        extractor = FakeExtractor({"s1": _document("s1", sections=("   ", ""))})
        service = _service(extractor=extractor)
        with pytest.raises(ResearchFailure) as exc_info:
            service.run(_request())
        assert exc_info.value.code == "no_evidence"
        assert exc_info.value.retryable is False

    def test_cancellation_before_search_stops_before_any_search_call(self):
        search = FakeSearchProvider([_search_result("s1")])
        service = _service(search=search)
        token = CancellationToken()
        token.cancel()
        with pytest.raises(RequestCancelled):
            service.run(_request(), token=token)
        assert search.calls == []


# ===========================================================================
# WebResearchService.run - citation formatting
# ===========================================================================


class TestRunCitationFormatting:
    def test_citation_count_is_set_per_source_by_whether_the_answer_actually_used_its_marker(self):
        results = [_search_result("s1"), _search_result("s2")]
        extractor = FakeExtractor(
            {
                "s1": _document("s1", sections=("Content one.",)),
                "s2": _document("s2", sections=("Content two.",)),
            }
        )
        model = FakeModel(answer="Only cites [s1] here.")
        service = _service(search=FakeSearchProvider(results), extractor=extractor, model=model)
        result = service.run(_request())
        by_id = {s.source_id: s for s in result.sources}
        assert by_id["s1"].citation_count == 1
        assert by_id["s2"].citation_count == 0
        assert {c.source_id for c in result.citations} == {"s1", "s2"}
        assert {c.marker for c in result.citations} == {"[s1]", "[s2]"}

    def test_missing_citations_appends_a_sources_section_and_a_warning(self):
        model = FakeModel(answer="An answer with no markers at all.")
        service = _service(model=model)
        result = service.run(_request())
        assert "### Sources" in result.answer_markdown
        assert "[s1]" in result.answer_markdown
        assert any("did not emit inline citations" in w for w in result.warnings)

    def test_citations_already_present_leave_the_answer_untouched(self):
        model = FakeModel(answer="Cited answer [s1].")
        service = _service(model=model)
        result = service.run(_request())
        assert result.answer_markdown == "Cited answer [s1]."
        assert not any("did not emit inline citations" in w for w in result.warnings)

    def test_rejected_sources_never_appear_in_the_citations_list(self):
        results = [_search_result("s1"), _search_result("s2")]
        extractor = FakeExtractor({"s1": _document("s1", sections=("Content.",))})
        model = FakeModel(assessments={"s2": SourceAssessment(accepted=False, reason="off_topic")}, answer="[s1] cited.")
        service = _service(search=FakeSearchProvider(results), extractor=extractor, model=model)
        result = service.run(_request())
        assert {c.source_id for c in result.citations} == {"s1"}


# ===========================================================================
# WebResearchService.run - result shape / progress events
# ===========================================================================


class TestRunResultShape:
    def test_result_carries_request_id_normalized_query_effective_query_and_provider_snapshot(self):
        request = WebResearchRequest(
            request_id="req-42",
            node_id="n1",
            chat_epoch=1,
            original_query="  widgets  ",
            provider_snapshot={"provider": "anthropic"},
        )
        model = FakeModel(refined_query="widgets and gadgets")
        service = _service(model=model)
        result = service.run(request)
        assert result.request_id == "req-42"
        assert result.original_query == "widgets"
        assert result.effective_query == "widgets and gadgets"
        assert result.provider_snapshot == {"provider": "anthropic"}


class TestRunProgressEvents:
    def test_emits_stages_in_order_starting_preparing_and_ending_completed(self):
        events: list[ProgressEvent] = []
        service = _service()
        service.run(_request(), progress=events.append)
        stages = [e.stage for e in events]
        assert stages[0] == ResearchStage.PREPARING
        assert stages[-1] == ResearchStage.COMPLETED
        assert ResearchStage.SEARCHING in stages
        assert ResearchStage.FETCHING in stages
        assert ResearchStage.EXTRACTING in stages
        assert ResearchStage.VALIDATING in stages
        assert ResearchStage.SYNTHESIZING in stages
        assert all(e.request_id == "r1" for e in events)

    def test_no_progress_callback_is_a_valid_no_op_call_shape(self):
        service = _service()
        result = service.run(_request())  # progress=None (the default)
        assert result.answer_markdown  # the run still completes normally
