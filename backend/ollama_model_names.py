"""Strict Ollama model identifiers for client and daemon-facing operations.

Ollama's model APIs accept names, not local filesystem paths, URLs, or shell
commands. Check the entire identifier at ingress (including daemon lists and
manifest names) before forwarding it to /api/pull, /api/show, /api/delete or
/api/chat. Registry/library/name:tag and namespace/name:tag remain supported.
"""

from __future__ import annotations

import re

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_MODEL_NAME = re.compile(rf"{_SEGMENT}(?:/{_SEGMENT}){{0,2}}(?::{_SEGMENT})?\Z")
MAX_OLLAMA_MODEL_NAME_LENGTH = 255


def valid_ollama_model_name(value: object) -> bool:
    """Reject traversal, URI, option, control-character and oversized names."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_OLLAMA_MODEL_NAME_LENGTH
        and _MODEL_NAME.fullmatch(value) is not None
    )
