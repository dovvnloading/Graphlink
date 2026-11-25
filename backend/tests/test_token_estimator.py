"""Literal tokenizer markers must remain ordinary user/document text."""

from unittest.mock import Mock

import pytest

from graphlink_token_estimator import TokenEstimator


@pytest.fixture
def byte_encoding(monkeypatch):
    # Use the real tiktoken API and special-token rejection path without
    # downloading the cl100k_base vocabulary or relying on its cache.
    import tiktoken

    encoding = tiktoken.Encoding(
        name="graphlink-test-bytes",
        pat_str=r"(?s:.+)",
        mergeable_ranks={bytes([byte]): byte for byte in range(256)},
        special_tokens={"<|endoftext|>": 256, "<|fim_prefix|>": 257},
    )
    TokenEstimator()  # Initialize the singleton before replacing its encoding.
    monkeypatch.setattr(TokenEstimator, "_encoding", encoding)
    return encoding


@pytest.mark.parametrize("text", [
    "",
    "ordinary text",
    "<|endoftext|>",
    "<|fim_prefix|>",
    "Unicode 😀 <|endoftext|> then <|fim_prefix|> and <|endoftext|>.",
])
def test_counts_arbitrary_text_as_ordinary_tokens(byte_encoding, text):
    # The test vocabulary contains only single bytes plus special IDs.
    # Counting a marker as a special ID would incorrectly yield one token.
    assert TokenEstimator().count_tokens(text) == len(text.encode("utf-8"))


def test_uses_ordinary_encoding_not_special_aware_encoding(monkeypatch):
    text = "literal <|endoftext|>"
    encoding = Mock(spec=["encode", "encode_ordinary"])
    encoding.encode.side_effect = ValueError("disallowed special token")
    encoding.encode_ordinary.return_value = [1, 2, 3]
    estimator = TokenEstimator()
    monkeypatch.setattr(TokenEstimator, "_encoding", encoding)

    assert estimator.count_tokens("") == 0
    encoding.encode_ordinary.assert_not_called()
    assert estimator.count_tokens(text) == 3
    encoding.encode_ordinary.assert_called_once_with(text)
    encoding.encode.assert_not_called()


def test_missing_encoding_keeps_character_count_fallback(monkeypatch):
    estimator = TokenEstimator()
    monkeypatch.setattr(TokenEstimator, "_encoding", None)
    assert estimator.count_tokens("") == 0
    text = "<|endoftext|> literal text"
    assert estimator.count_tokens(text) == len(text) // 4


def test_knowledge_ingest_preserves_literal_markers(byte_encoding, tmp_path):
    from backend.knowledge_ingest import ingest_file
    from backend.knowledge_store import list_chunks_for_document

    text = "Tokenizer documentation: <|endoftext|>\n\nExample: <|fim_prefix|>"
    source = tmp_path / "markers.txt"
    source.write_bytes(text.encode("utf-8"))
    db_path = tmp_path / "knowledge.db"

    outcome = ingest_file(str(source), db_path=db_path)
    assert outcome.was_new is True
    assert outcome.chunk_count == 1
    chunks = list_chunks_for_document(db_path, outcome.document_id)
    assert len(chunks) == 1
    assert chunks[0]["text"] == text
    assert chunks[0]["offset_start"] == 0
    assert chunks[0]["offset_end"] == len(text)
    assert chunks[0]["token_count"] == len(text.encode("utf-8"))
