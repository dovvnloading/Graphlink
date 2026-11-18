"""Evidence report tests use recorded HTML pages and prohibit socket connections."""
from __future__ import annotations

import json
from pathlib import Path
import socket

import pytest
from bs4 import BeautifulSoup

from graphlink_plugins.web_research.domain import (
    CancellationToken, FetchedDocument, FetchedPayload, ResearchSource,
    SearchResult, SourceAssessment, WebResearchRequest,
)
from graphlink_plugins.web_research.evidence_report import find_duplicate, quote_appears, validate_report
from graphlink_plugins.web_research.source_metadata import html_provenance
from graphlink_plugins.web_research.service import WebResearchService
from graphlink_plugins.web_research.providers import BeautifulSoupContentExtractor

FIXTURES = Path(__file__).with_name("test_web_research_fixtures")


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Fixture tests must not touch the network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def _page(name, source_id, quality="high"):
    soup = BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "html.parser")
    publisher, published_at = html_provenance(soup, f"https://fixture.example/{source_id}")
    body = soup.find("main")
    text = "\n".join(item.get_text(" ", strip=True) for item in body.find_all(["h1", "p"]))
    doc = FetchedDocument(
        source_id=source_id, title=soup.title.get_text(" ", strip=True),
        final_url=f"https://fixture.example/{source_id}", content_type="text/html", text=text,
    )
    source = ResearchSource(
        source_id=source_id, title=doc.title, url=doc.final_url,
        canonical_url=doc.final_url, final_url=doc.final_url,
        publisher=publisher, published_at=published_at,
        fetched_at="2026-11-18T17:00:00Z", quality=quality, status="accepted",
    )
    return doc, source


def _run(claims, *pairs):
    docs, sources = zip(*pairs)
    return validate_report(json.dumps({"claims": claims}), list(sources), docs, CancellationToken())


def _claim(value="42", ref="s1", quote="The city budget for water projects in 2025 was 42 million dollars.", **kwargs):
    return dict(
        text=f"The 2025 water budget was {value} million dollars.",
        topic="2025 water budget", value=value,
        evidence=[dict(source_id=ref, quote=quote)], **kwargs,
    )


def test_recorded_html_provides_publisher_publication_date_and_text():
    doc, source = _page("report_primary.html", "s1")
    assert source.publisher == "River Research Board"
    assert source.published_at == "2025-08-17"
    assert "2025 was 42 million" in doc.text


def test_exact_quote_must_appear_in_page():
    doc, _ = _page("report_primary.html", "s1")
    assert quote_appears("The city budget for water projects in 2025 was 42 million dollars.", doc)
    assert not quote_appears("The city budget for water projects in 2025 was 45 million dollars.", doc)


def test_alternate_publisher_and_date_metadata():
    _, source = _page("report_conflict.html", "s3")
    assert (source.publisher, source.published_at) == ("City Audit Office", "2025-09-02")


def test_near_duplicate_is_merged():
    first, _ = _page("report_primary.html", "s1")
    duplicate, _ = _page("report_copy.html", "s2")
    assert find_duplicate(duplicate, [first]) == "s1"


def test_different_numbers_are_not_merged():
    first, _ = _page("report_primary.html", "s1")
    other, _ = _page("report_conflict.html", "s3")
    assert find_duplicate(other, [first]) is None


def test_high_quality_source_with_verified_quote_is_supported():
    answer, claims, citations, warnings = _run([_claim()], _page("report_primary.html", "s1"))
    assert claims[0].status == "supported"
    assert citations[0].quote == "The city budget for water projects in 2025 was 42 million dollars."
    assert "[s1](https://fixture.example/s1)" in answer and "River Research Board" in answer
    assert not warnings


def test_one_medium_quality_source_is_qualified():
    answer, claims, citations, _ = _run([_claim()], _page("report_primary.html", "s1", quality="medium"))
    assert claims[0].status == "qualified"
    assert "single_weak_source" in claims[0].flags
    assert "Uncertain" in answer and len(citations) == 1


def test_hallucinated_quote_rejected_and_claim_omitted():
    answer, claims, citations, warnings = _run(
        [_claim(quote="An imaginary and unsupported quotation.")],
        _page("report_primary.html", "s1"),
    )
    assert claims[0].status == "unverified"
    assert not citations
    assert "No claims could be verified" in answer
    assert "42 million dollars" not in answer.split("### Sources")[0]
    assert any("rejected" in warning for warning in warnings)


def test_a_quote_on_a_different_page_does_not_count():
    _, claims, citations, _ = _run(
        [_claim(ref="s3")], _page("report_primary.html", "s1"), _page("report_conflict.html", "s3"),
    )
    assert not citations and claims[0].status == "unverified"


def test_conflicting_claim_values_are_flagged_even_without_model_dispute_hint():
    first = _claim()
    second = _claim(
        value="39", ref="s3",
        quote="The city budget for water projects in 2025 was 39 million dollars.",
    )
    answer, claims, citations, _ = _run(
        [first, second], _page("report_primary.html", "s1"), _page("report_conflict.html", "s3"),
    )
    assert [claim.status for claim in claims] == ["conflicted", "conflicted"]
    assert len(citations) == 2 and "Disputed" in answer


def test_explicit_dispute_requires_real_opposing_quote():
    claim = _claim(conflicts=[dict(
        source_id="s3", quote="The city budget for water projects in 2025 was 39 million dollars.",
    )])
    _, claims, citations, _ = _run(
        [claim], _page("report_primary.html", "s1"), _page("report_conflict.html", "s3"),
    )
    assert claims[0].status == "conflicted"
    assert {citation.role for citation in citations} == {"support", "conflict"}


def test_unstructured_markdown_cannot_bypass_verification():
    doc, source = _page("report_primary.html", "s1")
    answer, claims, citations, warnings = validate_report(
        "The 2025 water budget was 999 million dollars [s1].",
        [source], [doc], CancellationToken(),
    )
    assert not claims and not citations
    assert "999 million" not in answer and warnings


def test_unknown_source_reference_is_rejected():
    _, claims, citations, _ = _run([_claim(ref="s999")], _page("report_primary.html", "s1"))
    assert claims[0].status == "unverified"
    assert not citations


def test_fabricated_dispute_quote_cannot_turn_into_a_fact():
    claim = _claim(conflicts=[dict(source_id="s3", quote="Fabricated quote claims 80 million dollars.")])
    _, claims, citations, _ = _run(
        [claim], _page("report_primary.html", "s1"), _page("report_conflict.html", "s3"),
    )
    assert claims[0].status == "conflicted"
    assert [citation.role for citation in citations] == ["support"]


def test_complete_pipeline_uses_only_recorded_fixtures_and_merges_duplicates():
    names = ["report_primary.html", "report_copy.html", "report_conflict.html"]
    ids = ["s1", "s2", "s3"]

    class FixtureSearch:
        name = "fixture"
        def search(self, query, *, limits, token):
            return [SearchResult(source_id=source_id, title="fixture", url=f"https://fixture.example/{source_id}",
                                 canonical_url=f"https://fixture.example/{source_id}")
                    for source_id in ids]

    class FixtureFetcher:
        def fetch(self, result, *, limits, token):
            name = names[ids.index(result.source_id)]
            return FetchedPayload(
                source_id=result.source_id, requested_url=result.url, final_url=result.url,
                content_type="text/html", body=(FIXTURES / name).read_bytes(),
                fetched_at="2026-11-18T17:00:00Z",
            )

    class FixtureModel:
        def refine_query(self, query, history, *, limits, token):
            return query
        def assess_source(self, query, document, *, limits, token):
            return SourceAssessment(True, "allow", "high", "high")
        def summarize(self, query, history, evidence, *, limits, token):
            assert not any(chunk.startswith("[s2]") for chunk in evidence)
            return json.dumps({"claims": [_claim(conflicts=[{
                "source_id": "s3",
                "quote": "The city budget for water projects in 2025 was 39 million dollars.",
            }])]})

    service = WebResearchService(
        search_provider=FixtureSearch(), fetcher=FixtureFetcher(),
        extractor=BeautifulSoupContentExtractor(), model=FixtureModel(),
    )
    result = service.run(WebResearchRequest(
        request_id="r-fixture", node_id="n-fixture", chat_epoch=1,
        original_query="water budget", 
    ))
    sources = {source.source_id: source for source in result.sources}
    assert sources["s1"].status == "accepted"
    assert sources["s2"].status == "duplicate" and sources["s2"].merged_into == "s1"
    assert sources["s3"].status == "accepted"
    assert sources["s1"].publisher == "River Research Board"
    assert sources["s1"].published_at == "2025-08-17"
    assert sources["s1"].fetched_at == "2026-11-18T17:00:00Z"
    assert result.claims[0].status == "conflicted"
    assert {citation.source_id for citation in result.citations} == {"s1", "s3"}
    assert sources["s2"].citation_count == 0


def test_two_verified_quotes_with_different_numbers_flag_a_conflict():
    claim = _claim()
    claim["evidence"].append(dict(
        source_id="s3", quote="The city budget for water projects in 2025 was 39 million dollars.",
    ))
    answer, claims, citations, _ = _run(
        [claim], _page("report_primary.html", "s1"), _page("report_conflict.html", "s3"),
    )
    assert claims[0].status == "conflicted"
    assert "numeric_value_not_quoted" in claims[0].flags
    assert len(citations) == 2
