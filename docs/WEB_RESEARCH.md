# Web Research: evidence-backed reports

Web Research now returns a claim-based report rather than publishing unverified
model prose. The existing search, user/network approval, URL and IP policy,
robots.txt checks, crawl delays, request limits, cancellation, and optional
knowledge retention remain in their established pipeline. No additional page
requests are made for metadata or verification.

## Pipeline

1. Search and fetch through the existing bounded adapters. The fetcher captures a
   UTC fetch timestamp after it has read the page. Extraction obtains the page
   title, visible bounded text, publisher metadata and publication date (when
   available). Publisher falls back to the URL hostname and unknown publication
   dates remain empty rather than guessed.
2. The source assessor decides acceptance/relevance/quality as before.
   Accepted pages with the same final URL or identical/near-identical visible
   text are merged. Duplicate records remain visible with status "duplicate"
   and a "merged_into" source ID, but duplicates are not counted as independent
   evidence, passed to synthesis, or retained twice.
3. The summarizer proposes atomic claims and verbatim evidence quotations as a
   JSON object. It must use the full fetched source ID and identify competing
   values under the same topic, including opposing evidence as conflicts.
4. A deterministic verifier checks every proposed citation against the bounded
   text extracted from that specific fetched document. Whitespace is folded,
   but a quote shorter than 12 characters or absent from that page is rejected.
   Neither search-result snippets nor the model's own text can satisfy this
   check. Unsupported claims are not printed as factual findings.
5. Claims are qualified rather than stated as facts when supported only by one
   source not assessed high quality, when part of their citations cannot be
   verified, or when their conflict classification is incomplete. Explicit
   opposing citations, and differing values for the same topic across verified
   claims, mark the claims as disputed.

## Summarizer response

The model's answer is a JSON object with a "claims" list; each claim has the
following shape (the quotes here are illustrative, not retrieved citations):

    {
      "claims": [
        {
          "text": "The 2025 water budget was 42 million dollars.",
          "topic": "2025 water budget",
          "value": "42",
          "evidence": [
            {
              "source_id": "s1-0123456789",
              "quote": "The city budget for water projects in 2025 was 42 million dollars."
            }
          ],
          "conflicts": [
            {
              "source_id": "s2-abcdef0123",
              "quote": "The city budget for water projects in 2025 was 39 million dollars."
            }
          ]
        }
      ]
    }

A free-form Markdown response, an unknown source ID, or a source ID that refers
to a rejected/duplicate page is not enough to establish evidence. A bad quote
removes that citation; a claim with no verified supporting citations is omitted
from the Markdown answer. There is no "append a list of sources to model prose"
fallback that could make an unsupported factual answer look sourced.

## Output and provenance

The existing ResearchResult and legacy response shape are preserved. The
structured research_result payload additionally contains:

- "claims": claim_id, text, topic, value, status (supported, qualified,
  conflicted, unverified), flags, and citations;
- "citations": source_id, marker, claim_context, exact quote, and role
  (support or conflict); only verified citations to accepted pages appear;
- "sources": search URL, canonical URL, fetched final URL, page title,
  publisher, published_at date, fetched_at UTC timestamp, quality, status,
  merged_into, and citation_count, along with existing fields.

The Markdown output includes "Findings" with claim-level citations and status
labels, followed by a "Sources" inventory with URL, title, publisher, publish
date, and fetch time. Unknown values are labeled unknown. Sources that were
rejected or could not be fetched remain in the structured source audit trail,
not as factual citations.

A verified quote proves that text appears in the fetched and bounded page, not
that the page itself is truthful or that a claim is logically entailed. Source
quality assessment and conflict extraction are model-assisted; both may be
incomplete. The report therefore uses explicit uncertainty labels rather than
claiming independent verification of the underlying facts.

## Tests

Recorded HTML pages live under backend/tests/test_web_research_fixtures/.
The evidence tests prohibit socket connections, use fixture-backed search and
fetch adapters, and exercise real extraction and the service orchestration
without public network activity.

Run the focused regression suite:

    python -m pytest -q backend/tests/test_web_research_*.py

Run the repository-wide suite in a complete local checkout:

    python -m pytest -q

The existing fetch-policy, robots, pinned-connection, timeout, cancellation,
and retention tests remain important regression checks. No test should fetch a
live page.
