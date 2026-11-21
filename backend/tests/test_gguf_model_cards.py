"""The GGUF settings inventory never loads a llama.cpp model."""

import os
import struct

from backend.gguf_model_cards import llama_cpp_model_cards


def _string(data: bytes) -> bytes:
    return struct.pack("<Q", len(data)) + data


def _gguf(*entries: tuple[str, int, bytes]) -> bytes:
    return b"GGUF" + struct.pack("<IQQ", 3, 0, len(entries)) + b"".join(
        _string(key.encode("ascii")) + struct.pack("<I", value_type) + value
        for key, value_type, value in entries
    )


def test_reads_known_gguf_header_without_loading_tensors(tmp_path):
    model = tmp_path / "quantized.gguf"
    model.write_bytes(_gguf(
        ("general.file_type", 4, struct.pack("<I", 15)),
        ("llama.context_length", 4, struct.pack("<I", 32768)),
        ("tokenizer.chat_template", 8, _string(b"{{ message }}")),
    ) + b"fake tensor data")

    row, = llama_cpp_model_cards([str(model)])
    assert row == {
        "path": str(model), "name": "quantized.gguf", "sizeBytes": model.stat().st_size,
        "quantization": "Q4_K_M", "trainedContextLength": 32768,
        "capabilities": ["chat"],
    }


def test_de_duplicates_staged_selections_and_ignores_scan_only_paths(tmp_path):
    model = tmp_path / "one.gguf"
    model.write_bytes(b"not gguf")
    missing = tmp_path / "absent.gguf"
    result = llama_cpp_model_cards(["", str(model), str(model), str(missing)])
    assert [item["path"] for item in result] == [str(model), str(missing)]
    assert result[0]["sizeBytes"] == len(b"not gguf")
    assert result[0]["quantization"] == ""
    assert result[0]["trainedContextLength"] is None
    assert result[0]["capabilities"] == []
    assert result[1]["sizeBytes"] is None


def test_malformed_or_oversized_gguf_header_keeps_unverified_fields_unknown(tmp_path):
    model = tmp_path / "bad.gguf"
    model.write_bytes(_gguf(
        ("general.file_type", 4, struct.pack("<I", 18)),
        ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 1_000_000_000)),
        ("llama.context_length", 4, struct.pack("<I", 131072)),
    ))
    row, = llama_cpp_model_cards([str(model)])
    assert row["quantization"] == "Q6_K"
    assert row["trainedContextLength"] is None
    assert row["capabilities"] == []


def test_cache_refreshes_when_selected_gguf_changes(tmp_path):
    model = tmp_path / "changed.gguf"
    model.write_bytes(_gguf(("llama.context_length", 4, struct.pack("<I", 4096))))
    assert llama_cpp_model_cards([str(model)])[0]["trainedContextLength"] == 4096
    model.write_bytes(_gguf(
        ("llama.context_length", 4, struct.pack("<I", 8192)),
        ("general.file_type", 4, struct.pack("<I", 15)),
    ))
    os.utime(model, None)
    assert llama_cpp_model_cards([str(model)])[0]["trainedContextLength"] == 8192
