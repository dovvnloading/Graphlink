"""Read-only, bounded GGUF header details for Settings model cards.

No llama.cpp import, model instantiation, inference, or tensor loading. A GGUF
header may be incomplete or malformed; unavailable facts stay unavailable.
"""

from __future__ import annotations

from functools import lru_cache
import os
import re
import stat

_MAX_HEADER_BYTES = 2 * 1024 * 1024
_MAX_KEYS = 4096
_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0",
    8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M",
    13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
    17: "Q5_K_M", 18: "Q6_K",
}


def _header_details(path: str) -> tuple[str, int | None, tuple[str, ...]]:
    """Read at most 2 MiB from the GGUF metadata section, not the tensors."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(_MAX_HEADER_BYTES)
    except OSError:
        return "", None, ()
    if len(data) < 24 or data[:4] != b"GGUF" or int.from_bytes(data[4:8], "little") not in (2, 3):
        return "", None, ()
    count = int.from_bytes(data[16:24], "little")
    if count > _MAX_KEYS:
        return "", None, ()
    position = 24
    file_type: int | None = None
    context: int | None = None
    chat_template = False

    def read(size: int) -> bytes:
        nonlocal position
        if size < 0 or size > len(data) - position:
            raise ValueError("GGUF metadata exceeds inspection budget")
        result = data[position:position + size]
        position += size
        return result

    def number(size: int) -> int:
        return int.from_bytes(read(size), "little")

    def string() -> bytes:
        return read(number(8))

    def value(value_type: int) -> bytes:
        if value_type in _SCALAR_SIZES:
            return read(_SCALAR_SIZES[value_type])
        if value_type == 8:  # GGUF string
            return string()
        if value_type == 9:  # GGUF array; skip without materializing entries
            item_type, length = number(4), number(8)
            if item_type in _SCALAR_SIZES:
                read(length * _SCALAR_SIZES[item_type])
            elif item_type == 8 and length <= _MAX_KEYS:
                for _ in range(length):
                    string()
            else:
                raise ValueError("Unsupported or oversized GGUF array")
            return b""
        raise ValueError("Unsupported GGUF metadata type")

    try:
        for _ in range(count):
            key = string().decode("ascii")
            kind = number(4)
            raw = value(kind)
            if key == "general.file_type" and kind in (0, 2, 4, 10):
                file_type = int.from_bytes(raw, "little")
            elif key.endswith(".context_length") and key.count(".") == 1 and kind in (2, 4, 10):
                candidate = int.from_bytes(raw, "little")
                if 0 < candidate <= 10_000_000:
                    context = candidate
            elif key == "tokenizer.chat_template" and kind == 8 and raw.strip():
                chat_template = True
    except (UnicodeError, ValueError):
        # Keep independently verified keys preceding a large/unsupported field.
        pass
    return _FILE_TYPES.get(file_type, ""), context, ("chat",) if chat_template else ()


@lru_cache(maxsize=64)
def _cached_details(path: str, size: int, mtime_ns: int) -> tuple[str, int | None, tuple[str, ...]]:
    return _header_details(path)


def llama_cpp_model_cards(paths: list[str]) -> list[dict]:
    """Describe the two staged GGUF selections; never inspect scan results."""
    cards: list[dict] = []
    seen: set[str] = set()
    for raw in paths:
        path = str(raw or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        size: int | None = None
        quantization = ""
        trained_context: int | None = None
        capabilities: tuple[str, ...] = ()
        try:
            details = os.stat(path)
            if stat.S_ISREG(details.st_mode):
                size = details.st_size
                quantization, trained_context, capabilities = _cached_details(
                    path, details.st_size, details.st_mtime_ns,
                )
        except OSError:
            pass
        cards.append({
            "path": path,
            "name": re.split(r"[\\/]", path)[-1],
            "sizeBytes": size,
            "quantization": quantization,
            "trainedContextLength": trained_context,
            "capabilities": list(capabilities),
        })
    return cards
