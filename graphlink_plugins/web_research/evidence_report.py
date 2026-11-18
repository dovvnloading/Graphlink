"""Offline, deterministic claim and citation verification for Web Research.

The language model proposes claims and verbatim support; this module is the
only path from its untrusted response to user-visible factual statements.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
import json
import re
from typing import Sequence

from .domain import CancellationToken, FetchedDocument, ResearchCitation, ResearchClaim, ResearchSource


def document_text(document: FetchedDocument) -> str:
    """Use extracted, bounded page text; sections support legacy test adapters."""
    return document.text or "\n".join(document.sections)


def _normalized(value: str) -> str:
    return " ".join(value.split())


def quote_appears(quote: str, document: FetchedDocument) -> bool:
    """Exact text with only whitespace folding; snippets/model text never count."""
    quote = _normalized(quote)
    return len(quote) >= 12 and quote in _normalized(document_text(document))


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.casefold())
    return {tuple(words[i:i + 5]) for i in range(len(words) - 4)}


def find_duplicate(document: FetchedDocument, previous: Sequence[FetchedDocument]) -> str | None:
    """Merge matching final URLs, exact content, and very similar articles.

    A stringent similarity threshold helps avoid merging different reporting
    on the same event; duplicated pages never count as independent support.
    """
    content = _normalized(document_text(document)).casefold()
    shingles = _shingles(content) if len(content) >= 200 else set()
    for existing in previous:
        original = _normalized(document_text(existing)).casefold()
        if document.final_url == existing.final_url or (content and content == original):
            return existing.source_id
        if 100 <= min(len(content), len(original)) < 200:
            if SequenceMatcher(None, content, original, autojunk=False).ratio() >= 0.91:
                return existing.source_id
            continue
        if not shingles or len(original) < 200:
            continue
        # Fast length guard before comparing shingles; no quadratic LCS on
        # potentially 18,000-character pages.
        if min(len(content), len(original)) / max(len(content), len(original)) < 0.85:
            continue
        old = _shingles(original)
        if len(shingles & old) / max(len(shingles | old), 1) >= 0.90:
            return existing.source_id
    return None


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith(chr(96) * 3 + "json") and raw.endswith(chr(96) * 3):
        raw = raw[7:-3].strip()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("claims"), list) else None


def _safe_line(value: object, limit: int) -> str:
    return _normalized(str(value or ""))[:limit]


def _source_link(source: ResearchSource) -> str:
    # URLs in fetch results are policy-approved and need no refetch here.
    url = source.final_url or source.canonical_url or source.url
    # Avoid letting a malformed mocked URL escape a Markdown link.
    return url.replace(")", "%29").replace("(", "%28")


def validate_report(
    raw: str,
    sources: list[ResearchSource],
    documents: Sequence[FetchedDocument],
    token: CancellationToken,
) -> tuple[str, list[ResearchClaim], list[ResearchCitation], list[str]]:
    """Accept ONLY structured claims with quotes anchored in fetched pages.

    Missing/invalid citations cannot be repaired from search snippets. Model
    conflict hints make a claim uncertain; different values for one topic
    additionally trigger a deterministic cross-claim conflict check.
    """
    warnings: list[str] = []
    parsed = _parse_json(raw) if len(raw) <= 42_000 else None
    if parsed is None:
        warnings.append("The model did not return a verifiable claims report; its prose was not published.")
        proposed = []
    else:
        proposed = parsed["claims"][:30]
        if len(parsed["claims"]) > 30:
            warnings.append("Excess claims were omitted to respect the report limit.")

    accepted = {s.source_id.lower(): s for s in sources if s.status == "accepted"}
    pages = {doc.source_id.lower(): doc for doc in documents if doc.source_id.lower() in accepted}
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for source_id in accepted:
        shorthand = source_id.split("-", 1)[0]
        if shorthand in aliases:
            ambiguous.add(shorthand)
        else:
            aliases[shorthand] = source_id
    for shorthand in ambiguous:
        aliases.pop(shorthand, None)

    claims: list[ResearchClaim] = []
    citations: list[ResearchCitation] = []
    for index, proposed_claim in enumerate(proposed, start=1):
        token.raise_if_cancelled()
        if not isinstance(proposed_claim, dict):
            warnings.append(f"Claim {index} was not a structured claim and was omitted.")
            continue
        statement = _safe_line(proposed_claim.get("text"), 800)
        # The model must not smuggle citations directly into claim prose.
        statement = re.sub(r"\[s\d+(?:-[0-9a-f]+)?\]", "", statement, flags=re.IGNORECASE).strip()
        if not statement:
            continue
        topic = _safe_line(proposed_claim.get("topic"), 160).casefold()
        value = _safe_line(proposed_claim.get("value"), 160).casefold()
        flags: list[str] = []
        verified: list[ResearchCitation] = []
        unverified_reference = False

        def verify_entries(field: str, role: str) -> None:
            nonlocal unverified_reference
            entries = proposed_claim.get(field, [])
            if not isinstance(entries, list):
                entries = []
                unverified_reference = True
            for entry in entries[:20]:
                token.raise_if_cancelled()
                if not isinstance(entry, dict):
                    unverified_reference = True
                    continue
                ref = _safe_line(entry.get("source_id"), 80).strip("[]").lower()
                ref = ref if ref in accepted else aliases.get(ref, "")
                quote = _safe_line(entry.get("quote"), 900)
                document = pages.get(ref)
                if not document or not quote_appears(quote, document):
                    unverified_reference = True
                    warnings.append(f"Claim {index}: rejected a {role} citation whose quote is absent from its fetched page.")
                    continue
                if any(c.source_id.lower() == ref and c.role == role for c in verified):
                    continue
                verified.append(ResearchCitation(source_id=accepted[ref].source_id, marker=f"[{accepted[ref].source_id}]", claim_context=statement, quote=quote, role=role))

        verify_entries("evidence", "support")
        verify_entries("conflicts", "conflict")
        supporting = [c for c in verified if c.role == "support"]
        conflicting = [c for c in verified if c.role == "conflict"]
        conflict_proposed = bool(proposed_claim.get("conflicts")) or bool(proposed_claim.get("conflicted"))
        # For numeric claims, a quotation which never mentions the claimed
        # number cannot be treated as strong evidence for that number. When
        # multiple pages give mismatched numbers, flag the conflict even if
        # the model failed to put those pages in the conflicts list.
        value_numbers = re.findall(r"\d+(?:[.,]\d+)*", value)
        if len(value_numbers) == 1 and supporting:
            missing_value = [c for c in supporting if value_numbers[0] not in re.findall(r"\d+(?:[.,]\d+)*", c.quote)]
            if missing_value:
                flags.append("numeric_value_not_quoted")
                if len(supporting) > 1 and any(re.search(r"\d", c.quote) for c in missing_value):
                    conflict_proposed = True
                else:
                    unverified_reference = True
        if unverified_reference:
            flags.append("invalid_citation")
        if not supporting:
            flags.append("no_verified_support")
            status = "unverified"
        elif conflicting or conflict_proposed:
            flags.append("conflicting_sources" if conflicting else "unverified_conflict")
            status = "conflicted"
        elif not topic or not value:
            flags.append("conflict_not_assessed")
            status = "qualified"
        elif len(supporting) == 1 and accepted[supporting[0].source_id.lower()].quality.lower() != "high":
            flags.append("single_weak_source")
            status = "qualified"
        elif unverified_reference:
            status = "qualified"
        else:
            status = "supported"
        claim = ResearchClaim(
            claim_id=f"c{index}", text=statement, status=status, topic=topic,
            value=value, citations=verified, flags=flags,
        )
        claims.append(claim)

    # Conflicting values for the same subject cannot each be repeated as facts.
    grouped: dict[str, list[ResearchClaim]] = defaultdict(list)
    for claim in claims:
        if claim.status != "unverified" and claim.topic and claim.value:
            grouped[claim.topic].append(claim)
    for group in grouped.values():
        if len({c.value for c in group}) > 1:
            for claim in group:
                claim.status = "conflicted"
                if "conflicting_sources" not in claim.flags:
                    claim.flags.append("conflicting_sources")

    lines = ["### Findings"]
    shown = 0
    for claim in claims:
        token.raise_if_cancelled()
        if claim.status == "unverified":
            warnings.append(f"{claim.claim_id}: unsupported claim omitted from the answer.")
            continue
        shown += 1
        prefix = {
            "supported": "",
            "qualified": "**Uncertain — limited or unverifiable evidence:** ",
            "conflicted": "**Disputed — conflicting evidence (not established):** ",
        }[claim.status]
        references = " ".join(dict.fromkeys(
            f"{c.marker}({_source_link(accepted[c.source_id.lower()])})" for c in claim.citations
        ))
        lines.append(f"- {prefix}{claim.text} {references}".rstrip())
        citations.extend(claim.citations)
    if not shown:
        lines.append("No claims could be verified from the fetched pages.")

    lines.extend(["", "### Sources"])
    for source in sources:
        token.raise_if_cancelled()
        if source.status not in {"accepted", "duplicate"}:
            continue
        metadata = f"{source.publisher or 'Publisher unknown'}; published {source.published_at or 'date unknown'}; fetched {source.fetched_at or 'time unknown'}"
        suffix = f"; merged into [{source.merged_into}]" if source.status == "duplicate" else ""
        lines.append(f"- [{source.source_id}] {source.title} ({_source_link(source)}) — {metadata}{suffix}")

    for source in sources:
        source.citation_count = sum(c.source_id == source.source_id for c in citations)
    return "\n".join(lines), claims, citations, warnings
