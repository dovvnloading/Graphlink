"""ADR-017 stage 17.1: structure-aware chunking for the knowledge ingestion
pipeline.

Pure and offset-tracked, no I/O - `chunk_text()` takes a plain string and
returns TextChunks carrying `offset_start`/`offset_end` into that SAME
string, which is what lets a later citation ("this answer came from
document 7, offset 412-890") point back at an exact span of the source
rather than just naming the file. Kept in its own module (not
backend/knowledge_store.py) so it can be unit-tested against arbitrary
strings without ever touching a database connection.

POLICY (ADR-017's own decision #2): paragraph-boundary-aware, target
~512-1024 tokens with overlap, never truncated mid-paragraph unless a
SINGLE paragraph itself exceeds the hard-split threshold (a minified file
or one enormous CSV row with no natural break) - see _hard_split's own
docstring for why that fallback exists and how it stays offset-exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from graphlink_token_estimator import TokenEstimator

DEFAULT_TARGET_TOKENS = 768
DEFAULT_OVERLAP_TOKENS = 100

# A paragraph with no internal blank-line break longer than this many
# characters is hard-split into fixed-size pieces before chunking proper
# runs - without this, one enormous no-break paragraph (a minified JS
# bundle, a single huge CSV row) would become ONE chunk far past any
# target_tokens budget, since the accumulation loop below only ever
# decides whether to INCLUDE a whole paragraph, never to cut one.
_HARD_SPLIT_CHARS = 4000

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class TextChunk:
    text: str
    ordinal: int
    token_count: int
    offset_start: int
    offset_end: int


def _split_paragraphs_with_offsets(text: str) -> list[tuple[int, int, str]]:
    """[(start, end, content)] for each paragraph, offsets into `text`
    itself. `content` is the RAW slice (never stripped) - stripping would
    desync it from (start, end), and a caller that wants display-clean text
    can strip it separately without touching the offsets a citation needs
    to stay exact. Blank/whitespace-only input yields an empty list, not a
    single empty-string paragraph."""
    paragraphs: list[tuple[int, int, str]] = []
    pos = 0
    for match in _PARAGRAPH_SPLIT_RE.finditer(text):
        if match.start() > pos:
            paragraphs.append((pos, match.start(), text[pos:match.start()]))
        pos = match.end()
    if pos < len(text):
        paragraphs.append((pos, len(text), text[pos:]))
    return paragraphs


def _hard_split(start: int, end: int, content: str) -> list[tuple[int, int, str]]:
    """Splits ONE paragraph into fixed-size, offset-exact pieces when it
    alone exceeds _HARD_SPLIT_CHARS - a character budget, not a token one,
    deliberately: this is a last-resort safety valve for pathological input
    (no natural break at all), not a tuning knob, so it does not need
    TokenEstimator's cost to decide where to cut. Returns [(start, end,
    content)] unchanged (a single-element list) when already under the
    threshold - the common case, so every other caller can unconditionally
    `paragraphs.extend(_hard_split(...))` without a size check of its own."""
    if len(content) <= _HARD_SPLIT_CHARS:
        return [(start, end, content)]
    pieces: list[tuple[int, int, str]] = []
    offset = 0
    while offset < len(content):
        piece_end = min(offset + _HARD_SPLIT_CHARS, len(content))
        pieces.append((start + offset, start + piece_end, content[offset:piece_end]))
        offset = piece_end
    return pieces


def chunk_text(
    text: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[TextChunk]:
    """Greedily accumulates paragraphs into a chunk while under
    `target_tokens` (measured via TokenEstimator, the same tiktoken-backed
    counter every other token budget in this codebase uses), closing and
    starting the next chunk once the NEXT paragraph would push it over.
    Each new chunk is seeded with the tail of the PREVIOUS chunk (the
    largest whole run of trailing paragraphs that fits within
    `overlap_tokens`) before new paragraphs are added, so retrieval never
    loses context that fell exactly on a chunk boundary - the seeded and
    newly-added paragraphs' offset ranges legitimately overlap between
    adjacent chunks; that is the intended shape, not a bug.

    Blank/whitespace-only text returns an empty list. `token_count` on each
    returned chunk is the EXACT count of that chunk's own final text (not
    the running per-paragraph sum the accumulation loop uses internally to
    decide boundaries, which is a cheap heuristic only)."""
    if not text or not text.strip():
        return []

    estimator = TokenEstimator()
    paragraphs: list[tuple[int, int, str]] = []
    for start, end, content in _split_paragraphs_with_offsets(text):
        paragraphs.extend(_hard_split(start, end, content))

    chunks: list[TextChunk] = []
    current: list[tuple[int, int, str]] = []
    current_tokens = 0

    def _flush() -> None:
        if not current:
            return
        chunk_start = current[0][0]
        chunk_end = current[-1][1]
        chunk_text_value = text[chunk_start:chunk_end]
        chunks.append(TextChunk(
            text=chunk_text_value,
            ordinal=len(chunks),
            token_count=estimator.count_tokens(chunk_text_value),
            offset_start=chunk_start,
            offset_end=chunk_end,
        ))

    for start, end, content in paragraphs:
        piece_tokens = estimator.count_tokens(content)
        if current and current_tokens + piece_tokens > target_tokens:
            _flush()
            # Seed the new chunk with the closed chunk's own trailing
            # paragraphs, largest-first from the end, stopping once adding
            # one more would exceed overlap_tokens - unless NOTHING has
            # been taken yet, in which case one paragraph is kept even if
            # it alone exceeds the overlap budget (a single huge trailing
            # piece is still better overlap-context than none at all, and
            # `_hard_split` above already bounds how huge "huge" can be).
            overlap_pieces: list[tuple[int, int, str]] = []
            overlap_total = 0
            for piece in reversed(current):
                piece_tok = estimator.count_tokens(piece[2])
                if overlap_pieces and overlap_total + piece_tok > overlap_tokens:
                    break
                overlap_pieces.insert(0, piece)
                overlap_total += piece_tok
            current = overlap_pieces
            current_tokens = overlap_total
        current.append((start, end, content))
        current_tokens += piece_tokens

    _flush()
    return chunks
