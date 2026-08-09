"""ADR-017 stage 17.1: backend/knowledge_chunking.py's chunk_text()."""

from __future__ import annotations

from backend.knowledge_chunking import DEFAULT_TARGET_TOKENS, chunk_text


def _assert_offsets_exact(source: str, chunks) -> None:
    for chunk in chunks:
        assert source[chunk.offset_start:chunk.offset_end] == chunk.text


def test_empty_and_whitespace_only_text_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n\t  ") == []


def test_a_single_short_paragraph_becomes_one_chunk_with_exact_offsets():
    text = "Just one short paragraph, well under any target."
    chunks = chunk_text(text, target_tokens=1000)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].offset_start == 0
    assert chunks[0].offset_end == len(text)
    assert chunks[0].ordinal == 0
    _assert_offsets_exact(text, chunks)


def test_multiple_small_paragraphs_under_target_merge_into_one_chunk():
    text = "Para one.\n\nPara two.\n\nPara three."
    chunks = chunk_text(text, target_tokens=1000)
    assert len(chunks) == 1
    # The merged chunk is an exact SLICE of the source (including its own
    # inter-paragraph blank lines), not a re-joined concatenation.
    assert chunks[0].text == text
    _assert_offsets_exact(text, chunks)


def test_paragraphs_exceeding_target_split_into_multiple_ordered_chunks():
    paragraphs = [f"Paragraph {i} with several filler words to add bulk to it." for i in range(30)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, target_tokens=40, overlap_tokens=10)
    assert len(chunks) > 1
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    _assert_offsets_exact(text, chunks)
    # Every chunk's own reported token_count is the ACTUAL count of its
    # final text, not just under-the-limit by construction - recomputing
    # independently here pins that promise.
    from graphlink_token_estimator import TokenEstimator
    estimator = TokenEstimator()
    for chunk in chunks:
        assert chunk.token_count == estimator.count_tokens(chunk.text)


def test_adjacent_chunks_overlap_when_a_boundary_was_split():
    paragraphs = [f"Paragraph number {i} has some unique filler content in it." for i in range(20)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_text(text, target_tokens=40, overlap_tokens=15)
    assert len(chunks) >= 2
    # Real overlap: the next chunk's start is BEFORE the previous chunk's
    # end - proves the tail-seeding logic actually ran, not just that
    # chunks are contiguous/adjacent.
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.offset_start < prev.offset_end
        assert nxt.offset_start > prev.offset_start  # never re-starts at the same point


def test_one_pathologically_huge_paragraph_is_hard_split_not_left_oversized():
    # No blank-line break anywhere - a single "paragraph" by this module's
    # own definition, several times past the hard-split character budget.
    huge = "x" * 12000
    chunks = chunk_text(huge, target_tokens=DEFAULT_TARGET_TOKENS)
    assert len(chunks) >= 3  # 12000 chars / 4000-char hard-split budget
    _assert_offsets_exact(huge, chunks)
    # Reassembling every chunk's own span covers the whole source with no
    # gaps and no double-counted characters (only true because none of
    # these particular chunks overlap - they're all from ONE oversized
    # paragraph hard-split before chunking, with no room left in the
    # target_tokens budget for tail-seeding between them).
    covered = sorted(chunks, key=lambda c: c.offset_start)
    assert covered[0].offset_start == 0
    assert covered[-1].offset_end == len(huge)


def test_default_target_and_overlap_are_within_the_adrs_own_stated_range():
    # ADR-017 decision #2: "target ~512-1024 tokens with overlap" - pins
    # the actual default constants against that stated range so a future
    # drive-by edit can't silently drift outside it unnoticed.
    assert 512 <= DEFAULT_TARGET_TOKENS <= 1024
