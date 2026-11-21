"""Pure Ollama context metadata, memory estimate and safe fallback tests."""

from __future__ import annotations

from backend.ollama_context import (
    CONTEXT_CHOICES,
    DEFAULT_CONTEXT_WINDOW,
    FALLBACK_KV_BYTES_PER_TOKEN,
    estimated_memory_bytes,
    kv_bytes_per_token,
    trained_context_length,
)


def test_choices_and_default_are_fixed():
    assert CONTEXT_CHOICES == (4_096, 8_192, 16_384, 32_768, 65_536, 131_072)
    assert DEFAULT_CONTEXT_WINDOW == 8_192


def test_trained_context_reads_architecture_specific_length():
    assert trained_context_length({
        "general.architecture": "llama",
        "llama.context_length": "131072",
        "another.context_length": "4096",
    }) == 131_072
    assert trained_context_length({"mistral.context_length": 32768}) == 32_768
    assert trained_context_length({"llama.context_length": "broken"}) is None
    assert trained_context_length({"llama.context_length": False}) is None


def test_kv_cache_estimate_uses_model_attention_geometry():
    info = {
        "general.architecture": "llama",
        "llama.block_count": 32,
        "llama.attention.head_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.embedding_length": 4096,
    }
    # 32 layers * 8 KV heads * (128 key + 128 value) * 2 bytes.
    assert kv_bytes_per_token(info) == 131_072
    assert kv_bytes_per_token({}) == FALLBACK_KV_BYTES_PER_TOKEN


def test_memory_estimate_adds_weights_and_served_kv_cache():
    weights = 4 * 1024 ** 3
    assert estimated_memory_bytes(weights, 32768, 131072) == weights + 4 * 1024 ** 3
    assert estimated_memory_bytes(-1, -100, -100) == 0
