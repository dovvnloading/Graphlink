"""Pure Ollama context metadata, memory estimate and safe fallback tests."""

from __future__ import annotations

from backend.ollama_context import (
    CONTEXT_CHOICES,
    DEFAULT_CONTEXT_WINDOW,
    FALLBACK_KV_BYTES_PER_TOKEN,
    estimated_kv_cache_bytes,
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


def _gemma4_12b_metadata():
    # Real /api/show GGUF shape: per-layer KV heads, 5 sliding + 1 global,
    # separate sliding K/V widths and a 1024-token local attention window.
    return {
        "general.architecture": "gemma4",
        "gemma4.block_count": 48,
        "gemma4.context_length": 131_072,
        "gemma4.attention.head_count": 16,
        "gemma4.attention.head_count_kv": [8] * 48,
        "gemma4.attention.key_length": 512,
        "gemma4.attention.value_length": 512,
        "gemma4.attention.key_length_swa": 256,
        "gemma4.attention.value_length_swa": 256,
        "gemma4.attention.sliding_window": 1024,
        "gemma4.attention.sliding_window_pattern": [i % 6 != 5 for i in range(48)],
    }


def test_gemma4_kv_estimate_caps_local_layers_but_not_global_layers():
    info = _gemma4_12b_metadata()
    # The old single full-window formula used 16 Q heads for all 48 layers,
    # yielding 12 GiB at 8k. The correct pattern retains only 1024 tokens
    # for 40 local layers and all 8192 for the 8 global/GQA layers.
    local = 40 * 8 * (256 + 256) * 2 * 1024
    global_8k = 8 * 8 * (512 + 512) * 2 * 8192
    assert kv_bytes_per_token(info) * 8192 == 12 * 1024 ** 3
    assert estimated_kv_cache_bytes(info, 8192) == local + global_8k
    assert estimated_kv_cache_bytes(info, 8192) < 2 * 1024 ** 3
    assert estimated_kv_cache_bytes(info, 16384) == local + 2 * global_8k
    assert estimated_kv_cache_bytes(info, 4096) == local + global_8k // 2


def test_kv_window_estimate_preserves_standard_gqa_and_rejects_bad_layer_arrays():
    info = {
        "general.architecture": "llama",
        "llama.block_count": 32,
        "llama.attention.head_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.embedding_length": 4096,
    }
    assert estimated_kv_cache_bytes(info, 16384) == 131072 * 16384
    gemma = _gemma4_12b_metadata()
    for broken in ([8] * 47, [8] * 47 + [False], [8] * 47 + [100_000_001]):
        bad = {**gemma, "gemma4.attention.head_count_kv": broken}
        assert estimated_kv_cache_bytes(bad, 8192) == 8192 * kv_bytes_per_token(bad)
    invalid_pattern = {**gemma, "gemma4.attention.sliding_window_pattern": [True] * 47 + ["yes"]}
    assert estimated_kv_cache_bytes(invalid_pattern, 8192) > estimated_kv_cache_bytes(gemma, 8192)
    assert estimated_kv_cache_bytes(gemma, -1) == 0
    assert estimated_kv_cache_bytes(gemma, 0) == 0


def test_memory_estimate_adds_weights_and_served_kv_cache():
    weights = 4 * 1024 ** 3
    assert estimated_memory_bytes(weights, 32768, 131072) == weights + 4 * 1024 ** 3
    assert estimated_memory_bytes(-1, -100, -100) == 0


def test_untrusted_context_geometry_uses_safe_fallback():
    # Ollama's /api/show metadata is untrusted: fractional, infinite,
    # negative or enormous fields cannot become oversized memory estimates.
    base = {
        "general.architecture": "llama",
        "llama.block_count": 32,
        "llama.attention.head_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.embedding_length": 4096,
    }
    for key, bad in [
        ("llama.block_count", 10**100),
        ("llama.block_count", 3.5),
        ("llama.block_count", float("inf")),
        ("llama.block_count", True),
        ("llama.attention.head_count", 3),  # embedding cannot divide evenly
        ("llama.attention.head_count_kv", 64),  # more KV heads than heads
        ("llama.attention.key_length", 10**100),
    ]:
        assert kv_bytes_per_token({**base, key: bad}) == FALLBACK_KV_BYTES_PER_TOKEN


def test_metadata_trained_length_rejects_nonintegral_or_unbounded_numbers():
    for value in [3.5, 1e308, 10**99, False, "nan", "-1", "1000000000"]:
        assert trained_context_length({"llama.context_length": value}) is None
    assert trained_context_length({"llama.context_length": "131072"}) == 131072
