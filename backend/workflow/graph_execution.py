"""Approved execution services for registered Graph Runner steps.

The intent owns admission, scheduling, serialization and shared checkpoint.
Reuse existing model/science/MCP operations and request fences; adapters do
not infer approval or retry an external request.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.domain.graph import SceneDocument
from backend.domain.graph_run_external import RECONCILIATION_DETAIL, ExternalCall, dispatch_external_attempt
from backend.domain.graph_runner import execute_graph_step, validate_graph_run_ancestors
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import ModelState, PersistedPluginState, PluginStateSnapshot
from backend.domain.nodes_content import _text_digest
from backend.events import SessionBus
from graphlink_plugins.scientific.data_table import TableState
from graphlink_plugins.scientific.graph_run_adapter import prepare as prepare_science
from graphlink_plugins.scientific.workbench import _run_token

from .graph_steps import _inspect, _matching_node, _output_digest, pin_output_version
from .model_note_adapter import _digest, _record_model_usage


def _state_data(node: SceneNode) -> dict[str, Any]:
    """A scientific node's state payload; a node without state cannot run."""
    if not isinstance(node.state, (PersistedPluginState, PluginStateSnapshot, TableState)):
        raise SceneError("The scientific node has no saved plugin state.")
    return node.state.data


def _adopt_scientific_result(
    document: SceneDocument, step: dict[str, Any], by_node_id: dict[str, Any],
    *, detail: str, elapsed_ms: int,
) -> bool:
    """Recover a committed result when its run receipt was not checkpointed.

    Check live inputs, configuration, ancestor receipts and the output before
    claiming completion. Used after dispatch, on resume, and when cancellation
    races with a calculation that has already committed.
    """
    try:
        validate_graph_run_ancestors(document, step, by_node_id)
        node = _matching_node(document, step)
        live = step.copy()
        _inspect(document, live)
        if (live["status"] != "current" or live["configHash"] != step["configHash"]
                or (step["inputHash"] and live["inputHash"] != step["inputHash"])):
            return False
        latest = _state_data(node).get("latest")
        if not isinstance(latest, dict):
            return False
        step.update(
            status="completed", detail=detail, inputHash=live["inputHash"],
            outputHash=_output_digest(node), scientificResult=latest,
            elapsedMs=elapsed_ms,
        )
        return True
    except SceneError:
        return False


async def _run_model_step(
    document: SceneDocument, confirmed_checkpoint: Callable[[], Awaitable[bool]],
    step: dict[str, Any], by_node_id: dict[str, Any], record: Callable,
) -> None:
    """One reviewed local Model step with a durable fence, never auto-retried."""
    # Imported at execution time to keep the existing GraphModel one-shot
    # API's dependency on _digest out of module import cycles.
    from backend.api import intents_graph_model as local

    validate_graph_run_ancestors(document, step, by_node_id)
    node = _matching_node(document, step)
    if not isinstance(node.state, ModelState):
        raise SceneError("This is not an executable Model node.")
    if any(
        prior is not step and prior.get("nodeId") == node.id
        and prior.get("status") == "reconciliation-required"
        for record in document.graph_runs for prior in record.get("steps", [])
    ):
        raise SceneError("This Model output has an unresolved request; reconcile it before another call.")
    endpoint = local._require_loopback_ollama()
    if step.get("endpointHash") != _digest(["local_model_endpoint", endpoint]):
        raise SceneError("The Ollama endpoint changed since preview. Preview the graph again.")
    live = step.copy()
    _inspect(document, live)
    if live["status"] not in {"ready", "current"}:
        raise SceneError("Model input cannot be executed in its current state.")
    for key in ("configHash", "targetHash"):
        if live[key] != step[key]:
            raise SceneError("A Model configuration or output changed since preview.")
    if step["inputHash"] and step["inputHash"] != live["inputHash"]:
        raise SceneError("The Model input changed since preview.")
    # Deferred hashes become concrete only after the upstream Model
    # completion is committed. Save it with the same request fence.
    step["inputHash"] = live["inputHash"]
    binding = node.state.model_binding
    identity = binding.binding_id
    model_id, provider = binding.model_id, binding.provider_name
    prompt = document._text_output(step["sourceId"])

    async def invoke(_request_id: str) -> Any:
        return await asyncio.to_thread(
            local.local_ollama_complete, prompt=prompt, model_id=model_id, endpoint=endpoint,
        )

    async def apply_result(payload: Any) -> str:
        _record_model_usage(step, payload)
        text = local._response_text(payload)
        current = _matching_node(document, step)
        if not isinstance(current.state, ModelState):
            raise SceneError("The Model node changed during inference.")
        new_live = step.copy()
        _inspect(document, new_live)
        if any(new_live[key] != step[key] for key in ("inputHash", "configHash", "targetHash")):
            raise SceneError("The Model input, configuration or output changed during inference.")
        current_model_state = current.state
        if current_model_state.model_binding.binding_id != identity:
            raise SceneError("The Model port was rebound during inference.")
        output_hash = _text_digest(text)

        def apply() -> None:
            current.content = text
            current_model_state.model_binding.last_input_hash = step["inputHash"]
            current_model_state.model_binding.last_output_hash = output_hash

        record(apply, (current.id,))
        return output_hash

    await dispatch_external_attempt(
        step, ExternalCall(
            kind="model", provider_name=provider, model_id=model_id,
            input_ref=f"graph-text:{step['sourceId']}:{step['inputHash']}",
            input_hash=step["inputHash"],
        ), checkpoint=confirmed_checkpoint, invoke=invoke, apply_result=apply_result,
    )

async def _run_scientific_step(
    document: SceneDocument, bus: SessionBus, settings_manager: Any,
    run: dict[str, Any], step: dict[str, Any], by_node_id: dict[str, Any],
) -> None:
    """Run only an explicitly configured workbench through its existing
    granted, capped, killable PR 621 worker. No generic tool dispatch.
    """
    started = time.monotonic()

    def elapsed() -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    validate_graph_run_ancestors(document, step, by_node_id)
    node = _matching_node(document, step)
    live = step.copy()
    _inspect(document, live)
    if live["configHash"] != step["configHash"]:
        raise SceneError("The scientific configuration changed since preview.")
    if step["inputHash"] and live["inputHash"] != step["inputHash"]:
        raise SceneError("The scientific inputs changed since preview.")
    step["inputHash"] = live["inputHash"]

    # A successful calculation may have committed just before an
    # interrupted receipt checkpoint. Do not repeat it on resume.
    if live["status"] == "current":
        if not _adopt_scientific_result(
            document, step, by_node_id,
            detail="Verified existing scientific result; not replayed.", elapsed_ms=elapsed(),
        ):
            raise SceneError("The committed scientific result could not be verified.")
        return
    if live["status"] != "ready" or live["targetHash"] != step["targetHash"]:
        raise SceneError("The scientific output changed since preview.")

    pack = node.kind.split(".", 1)[0]
    granted = lambda plugin_id: (
        settings_manager is not None
        and bool(settings_manager.get_plugin_grants().get(plugin_id, False))
    )
    operation, inputs, sources, input_hash, _same = prepare_science(document, node, granted)
    if input_hash != step["inputHash"]:
        raise SceneError("The scientific input fingerprint changed before dispatch.")
    answer = await bus.dispatch_intent("app-plugins", "invokePluginIntent", [
        pack, "run", {
            "node_id": node.id, "operation_id": operation.id,
            "inputs": inputs, "sources": sources,
            "expected_run": _run_token(_state_data(node)),
            "graph_run_id": run["runId"],
            "uncertainty_method": _state_data(node)["runner"].get("uncertaintyMethod", "auto"),
        },
    ])
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        failure = answer if isinstance(answer, dict) else {}
        error_type = failure.get("errorType", "conflict" if failure.get("conflict") is True else "validation")
        if error_type not in {"timeout", "cancelled", "invalid_input", "worker_error", "validation", "conflict"}:
            error_type = "validation"
        measured = failure.get("elapsedMs")
        step.update(
            status="failed" if error_type != "cancelled" else "cancelled",
            errorType=error_type, elapsedMs=measured if type(measured) is int and measured >= 0 else elapsed(),
            detail=str(failure.get("error") or "Scientific execution was not authorized.")[:400],
        )
        return

    if not _adopt_scientific_result(
        document, step, by_node_id, detail="Scientific result recorded.",
        elapsed_ms=elapsed(),
    ):
        raise SceneError("The scientific result or its input changed during execution.")

async def _run_mcp_tool_step(document, bus, checkpoint, step, by_node_id, record):
    """The same MCP adapter serves a direct card run and Graph Runner."""
    from graphlink_plugins.mcp_connect import core

    runtime = getattr(bus, "mcp_connect_runtime", None)
    if runtime is None:
        raise SceneError("MCP Connect is unavailable in this session. Reopen the graph.")
    validate_graph_run_ancestors(document, step, by_node_id)

    def apply_result(payload, request_id, prepared, started_at):
        validate_graph_run_ancestors(document, step, by_node_id)
        node = _matching_node(document, step)
        record(lambda: core.commit_result(
                node, payload, request_id=request_id, input_hash=prepared.input_hash,
                sources=prepared.sources, started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
            ), (node.id,))
        step["outputVersion"] = core.state_data(node)["latest"]["version"]
        return core.output_hash(node)

    await runtime.execute(step, checkpoint, apply_result)


@dataclass
class GraphRunExecution:
    document: SceneDocument
    bus: SessionBus
    settings_manager: Any
    checkpoint: Callable[[], Awaitable[bool]]
    run: dict[str, Any]
    by_node_id: dict[str, Any]
    record: Callable
    started_clock: float

    def elapsed(self) -> int:
        return max(0, int((time.monotonic() - self.started_clock) * 1000))

    async def run_model(self, step: dict[str, Any]) -> None:
        try:
            await _run_model_step(self.document, self.checkpoint, step, self.by_node_id, self.record)
        except asyncio.CancelledError:
            raise
        except SceneError as exc:
            if step["status"] != "reconciliation-required":
                step.update(status="blocked", detail=str(exc))
        except Exception:
            # The external adapter owns the uncertainty fence. An
            # unadmitted failure is local; a sent call is uncertain.
            if "externalAttempt" not in step:
                step.update(status="blocked", detail="Model dispatch could not start.")
            elif step["status"] != "reconciliation-required":
                step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)

    async def scientific(self, step: dict[str, Any]) -> None:
        try:
            await _run_scientific_step(self.document, self.bus, self.settings_manager, self.run, step, self.by_node_id)
        except asyncio.CancelledError:
            raise
        except SceneError as exc:
            step.update(status="blocked", detail=str(exc), errorType="validation", elapsedMs=self.elapsed())
        except Exception:
            step.update(status="failed", detail="The scientific runner could not finish.",
                        errorType="worker_error", elapsedMs=self.elapsed())

    async def tool(self, step: dict[str, Any]) -> None:
        try:
            await _run_mcp_tool_step(self.document, self.bus, self.checkpoint, step, self.by_node_id, self.record)
        except asyncio.CancelledError:
            raise
        except SceneError as exc:
            if "externalAttempt" not in step:
                step.update(status="blocked", detail=str(exc))
        except Exception:
            if "externalAttempt" not in step:
                step.update(status="blocked", detail="The MCP tool could not start. Check its server settings.")
            else:
                step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)

    async def deterministic(self, step: dict[str, Any]) -> None:
        document = self.document
        try:
            validate_graph_run_ancestors(document, step, self.by_node_id)
            if not step["inputHash"]:
                live = step.copy()
                _inspect(document, live)
                if live["configHash"] != step["configHash"] or live["targetHash"] != step["targetHash"]:
                    raise SceneError("The deferred output or configuration changed since preview.")
                step["inputHash"] = live["inputHash"]
            digest, _ = self.record(lambda: execute_graph_step(document, step), (step["nodeId"],))
        except SceneError as exc:
            step.update(status="blocked", detail=str(exc))
        else:
            step.update(status="completed", detail="Output updated.", outputHash=digest)
            pin_output_version(document, step)

    def recover_scientific(self, step: dict[str, Any]) -> bool:
        return _adopt_scientific_result(
            self.document, step, self.by_node_id,
            detail="Committed before cancellation; result preserved.", elapsed_ms=self.elapsed(),
        )
