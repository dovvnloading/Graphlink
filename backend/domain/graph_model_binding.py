"""Validated, versioned identity for a local-only text-to-text model connection.

The binding is configuration, not authorization: every execution still passes
through Graph Runner's explicit preview, per-request durable fence and policy.
"""

from __future__ import annotations

import re

from backend.domain.model import SceneError
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA

MODEL_INPUT_VERSION = 1
_MODEL_ID = re.compile(r"[A-Za-z0-9._:/@+-]{1,192}")


def validate_local_model_identity(provider: str, model_id: str) -> None:
    if provider != LOCAL_PROVIDER_OLLAMA:
        raise SceneError("Graph Runner currently permits only explicitly selected local Ollama models.")
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        raise SceneError("Choose a valid installed Ollama model ID (1–192 safe characters).")
    if model_id.lower().endswith(("-cloud", ":cloud")):
        raise SceneError("Cloud-backed Ollama models are not allowed in this local Graph Runner step.")
