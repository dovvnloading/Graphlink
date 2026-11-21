"""Ollama context choices, trained limits and conservative memory estimates.

Values shown in Settings are estimates, not allocator guarantees: KV-cache
precision, parallel requests, GPU offload and runtime overhead vary by model.
No network I/O is performed here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

CONTEXT_CHOICES = (4_096, 8_192, 16_384, 32_768, 65_536, 131_072)
DEFAULT_CONTEXT_WINDOW = 8_192
# Conservative fallback for a typical 7-8B GQA model when Ollama metadata
# lacks the attention geometry. The UI identifies the figure as an estimate.
FALLBACK_KV_BYTES_PER_TOKEN = 128 * 1024
# Ollama's metadata comes from an HTTP response, not a trusted allocator.
# Avoid accepting malformed/fractional/unreasonably large geometry as RAM advice.
MAX_METADATA_DIMENSION = 1_000_000
MAX_KV_BYTES_PER_TOKEN = 16 * 1024 * 1024


def _positive_int(value: object) -> int | None:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 7:
        number = int(value)
    else:
        return None
    return number if 0 < number <= MAX_METADATA_DIMENSION else None


def trained_context_length(model_info: object) -> int | None:
    """The architecture's trained maximum, never the Modelfile's num_ctx."""
    if not isinstance(model_info, Mapping):
        return None
    architecture = str(model_info.get("general.architecture") or "").strip()
    keys = ([f"{architecture}.context_length"] if architecture else [])
    keys += [str(key) for key in model_info if str(key).endswith(".context_length")]
    for key in keys:
        length = _positive_int(model_info.get(key))
        if length:
            return length
    return None


def kv_bytes_per_token(model_info: object) -> int:
    """Estimate key+value cache bytes/token from GGUF attention metadata.

    Assumes float16 KV storage. Ollama can use other KV types, so this
    estimate is deliberately labeled approximate by the settings screen.
    """
    if not isinstance(model_info, Mapping):
        return FALLBACK_KV_BYTES_PER_TOKEN
    architecture = str(model_info.get("general.architecture") or "").strip()
    if not architecture:
        return FALLBACK_KV_BYTES_PER_TOKEN
    prefix = f"{architecture}."
    layers = _positive_int(model_info.get(prefix + "block_count"))
    kv_heads = _positive_int(model_info.get(prefix + "attention.head_count_kv"))
    heads = _positive_int(model_info.get(prefix + "attention.head_count"))
    embedding = _positive_int(model_info.get(prefix + "embedding_length"))
    key_length = _positive_int(model_info.get(prefix + "attention.key_length"))
    value_length = _positive_int(model_info.get(prefix + "attention.value_length"))
    if not key_length and heads and embedding and embedding % heads == 0:
        key_length = embedding // heads
    if not value_length:
        value_length = key_length
    if kv_heads and heads and kv_heads > heads:
        return FALLBACK_KV_BYTES_PER_TOKEN
    if layers and (kv_heads or heads) and key_length and value_length:
        estimate = layers * (kv_heads or heads) * (key_length + value_length) * 2
        if estimate <= MAX_KV_BYTES_PER_TOKEN:
            return estimate
    return FALLBACK_KV_BYTES_PER_TOKEN


def estimated_kv_cache_bytes(model_info: object, context_window: int) -> int:
    """Estimate an actual window's KV allocation, including hybrid attention.

    Some GGUF architectures (notably Gemma 4) expose per-layer KV head counts
    and a sliding/global layer pattern. Sliding layers retain at most their
    local window, while global layers retain all served tokens. Using one
    full-context KV cost for every layer can overstate Gemma 4 by many GiB.
    Without complete geometry, keep the existing approximate fallback.
    """
    if type(context_window) is not int or context_window <= 0:
        return 0
    fallback = context_window * kv_bytes_per_token(model_info)
    if not isinstance(model_info, Mapping):
        return fallback
    architecture = str(model_info.get("general.architecture") or "").strip()
    if not architecture:
        return fallback
    prefix = f"{architecture}."
    layers = _positive_int(model_info.get(prefix + "block_count"))
    # Bound iteration over the untrusted /api/show metadata.
    if not layers or layers > 1024:
        return fallback
    heads = _positive_int(model_info.get(prefix + "attention.head_count"))
    raw_kv_heads = model_info.get(prefix + "attention.head_count_kv")
    if isinstance(raw_kv_heads, (list, tuple)):
        if len(raw_kv_heads) != layers:
            return fallback
        kv_heads = [_positive_int(count) for count in raw_kv_heads]
        if any(count is None or (heads is not None and count > heads) for count in kv_heads):
            return fallback
    else:
        kv_count = _positive_int(raw_kv_heads) or heads
        if not kv_count or (heads is not None and kv_count > heads):
            return fallback
        kv_heads = [kv_count] * layers

    key_length = _positive_int(model_info.get(prefix + "attention.key_length"))
    value_length = _positive_int(model_info.get(prefix + "attention.value_length"))
    embedding = _positive_int(model_info.get(prefix + "embedding_length"))
    if not key_length and heads and embedding and embedding % heads == 0:
        key_length = embedding // heads
    if not key_length:
        return fallback
    if not value_length:
        value_length = key_length

    pattern = model_info.get(prefix + "attention.sliding_window_pattern")
    sliding_window = _positive_int(model_info.get(prefix + "attention.sliding_window"))
    # Never assume local retention from a missing or malformed layer pattern.
    if (not isinstance(pattern, (list, tuple)) or len(pattern) != layers
            or any(type(local) is not bool for local in pattern) or not sliding_window):
        pattern = [False] * layers
    swa_key = _positive_int(model_info.get(prefix + "attention.key_length_swa")) or key_length
    swa_value = _positive_int(model_info.get(prefix + "attention.value_length_swa")) or value_length

    total = 0
    for kv_head_count, local in zip(kv_heads, pattern):
        tokens = min(context_window, sliding_window) if local else context_window
        key = swa_key if local else key_length
        value = swa_value if local else value_length
        total += kv_head_count * (key + value) * 2 * tokens  # float16 key + value
    return total if 0 < total <= MAX_KV_BYTES_PER_TOKEN * context_window else fallback


def estimated_memory_bytes(model_size_bytes: int, context_window: int, kv_bytes: int) -> int:
    """Approximate resident model weights plus a single request's KV cache."""
    return max(0, int(model_size_bytes)) + max(0, int(context_window)) * max(0, int(kv_bytes))


def available_memory_bytes() -> int:
    """Best-effort currently available system RAM; zero means unknown.

    GPU VRAM is not included; callers must not present this as available VRAM.
    """
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.avail_phys)
        except (AttributeError, OSError):
            pass
        return 0

    # Linux's MemAvailable includes reclaimable cache, unlike MemFree.
    try:
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        return int(os.sysconf("SC_AVPHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0
