"""Compatibility adapter for the opt-in, one-shot Model output note.

Never discovered by ordinary graph previews. Preserve the selected-note
token, binding identity and durable external fence.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from backend.domain.graph_model_binding import validate_local_model_identity
from backend.domain.graph_run_external import ExternalCall, dispatch_external_attempt
from backend.domain.model import SceneError
from backend.domain.nodes_content import _text_digest

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument

_MAX_INPUT_BYTES = 65536


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def model_step_preview(
    document: SceneDocument, source_id: str, target_id: str, provider: str, model_id: str,
    *, require_endpoint: Callable[[], str],
) -> tuple[str, dict[str, str], str]:
    """Validate and freeze what a one-shot model call would consume and replace."""
    validate_local_model_identity(provider, model_id)
    endpoint = require_endpoint()
    if source_id == target_id:
        raise SceneError("A model's source and output must be different nodes.")
    source_text = document._text_output(source_id)
    target = document._plain_note(target_id)
    if target.state.text_input_source_id or target.state.table_output is not None:
        raise SceneError("Choose an unbound plain note for the model output.")
    binding = target.state.model_text_input
    if binding is not None and (binding.source_id, binding.provider_name, binding.model_id) != (source_id, provider, model_id):
        raise SceneError("This note has a different saved model connection. Save the new connection first.")
    # A reusable binding is not permission to consume obsolete upstream model
    # replies; explicit re-execution of that upstream model is required.
    document.require_current_model_source(source_id, check_text_inputs=True)
    if not source_text.strip() or len(source_text.encode("utf-8", errors="surrogatepass")) > _MAX_INPUT_BYTES:
        raise SceneError("Model input must contain text and be at most 64 KiB.")
    fingerprint = _text_digest(source_text)
    step = {
        "nodeId": target_id, "sourceId": source_id,
        "kind": "model", "operation": "model",
        "label": target.title or "Model output",
        "status": "ready",
        "detail": f"Local Ollama model: {model_id}. Loopback request; verify custom aliases do not route to cloud.",
        "inputHash": fingerprint,
        "configHash": _digest(["local_model_v1", provider, model_id, endpoint, binding.binding_id if binding else ""]),
        "targetHash": _text_digest(target.content),
    }
    token = _digest([str(document.graph_run_epoch), step])
    return token, step, source_text


async def run_model_note(
    document: SceneDocument, selection: Any, step: dict[str, Any], prompt: str,
    endpoint: str, pinned_binding_id: str,
    *, checkpoint: Callable[[], Awaitable[bool]], complete: Callable, response_text: Callable,
    record: Callable,
) -> None:
    # The endpoint is pinned before journal admission. The dedicated
    # client will NOT read OLLAMA_HOST or proxy settings itself.
    async def invoke(_request_id: str) -> Any:
        # The model SDK's module-level chat() binds a process-global
        # client at import time and can retain a stale/cloud host.
        # Our dedicated Client pins this loopback URL, disables proxies
        # and redirects, and never inherits active provider credentials.
        return await asyncio.to_thread(
            complete, prompt=prompt, model_id=selection.modelId, endpoint=endpoint,
        )

    async def apply_result(payload: Any) -> str:
        text = response_text(payload)
        # Other canvas intents can edit notes while an Ollama request
        # awaits its answer. Never silently replace work the user wrote
        # since the preview, or publish a response for a now-changed
        # source as though it reflected the current text.
        if _text_digest(document._text_output(selection.sourceId)) != step["inputHash"]:
            raise SceneError("The source changed during inference; the model reply was not applied.")
        target = document._plain_note(selection.targetId)
        if target.state.text_input_source_id or target.state.table_output is not None:
            raise SceneError("The output note acquired another input; the model reply was not applied.")
        current_binding = target.state.model_text_input
        if ((current_binding.binding_id if current_binding is not None else "") != pinned_binding_id or
                (current_binding is not None and
                 (current_binding.source_id, current_binding.provider_name, current_binding.model_id) !=
                 (selection.sourceId, selection.providerName, selection.modelId))):
            raise SceneError("The saved model connection changed during inference; the reply was not applied.")
        if _text_digest(target.content) != step["targetHash"]:
            raise SceneError("The output note was edited during inference; the model reply was not applied.")
        # We are still inside the session mutation guard. The output,
        # undo record and completion receipt persist in one graph save.
        output_hash = _text_digest(text)

        def write_answer_and_provenance() -> None:
            document.set_note_content(selection.targetId, text)
            if current_binding is not None:
                current_binding.last_input_hash = step["inputHash"]
                current_binding.last_output_hash = output_hash

        record(write_answer_and_provenance, (selection.targetId,))
        return output_hash

    await dispatch_external_attempt(
        step,
        ExternalCall(
            kind="model", provider_name=selection.providerName, model_id=selection.modelId,
            input_ref=f"graph-text:{selection.sourceId}:{step['inputHash']}",
            input_hash=step["inputHash"],
        ),
        checkpoint=checkpoint, invoke=invoke, apply_result=apply_result,
    )


class ModelNoteAdapter:
    """Separate legacy entry point sharing the same workflow registry."""

    preview = staticmethod(model_step_preview)
    run = staticmethod(run_model_note)
