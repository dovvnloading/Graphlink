"""First-class fal.ai Builder tools with per-call paid-operation consent.

They use the same ToolRegistry gates as Graphlink's graph, file and MCP
tools. No key is ever included in a ToolSpec, ToolResult or approval payload.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from backend.fal_integration import FalClient, FalError, FalJobFailed, model_path, request_path
from backend.fal_json import page_value, select_pointer
from backend.fal_model_contract import model_contract
from backend.fal_results import result_assets
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, PROVIDER_CALL, RunContext, ToolRegistry, ToolResult

_MODEL = {"type": "string", "description": "fal model endpoint, e.g. fal-ai/flux/schnell or nested owner/app/path"}
_ARGS = {"type": "object", "description": "Model-specific input JSON (prompt, image_url, video_url, seed, etc.)"}
_OPTIONS = {
    "type": "object",
    "description": (
        "Optional fal headers/query settings: priority, start_timeout, runner_hint, "
        "no_retry, store_io, disable_fallback, object_lifecycle, retry_config, "
        "webhook_url, max_queue_length."
    ),
}
_ID = {"type": "string", "description": "UUID returned by fal.submit"}
_POINTER = {"type": "string", "description": "RFC 6901 JSON Pointer, e.g. /images/0/url. Empty selects the whole value."}
_OFFSET = {"type": "integer", "minimum": 0, "description": "Offset within a large selected string, array or object."}
_BINDINGS = {
    "type": "array", "maxItems": 12,
    "description": "Fill top-level inputs directly from completed queued jobs, without copying large values. Bound names must be absent from arguments.",
    "items": {
        "type": "object", "properties": {
            "argument": {"type": "string", "description": "Destination top-level input name"},
            "model_id": _MODEL, "request_id": _ID, "pointer": _POINTER,
        }, "required": ["argument", "model_id", "request_id", "pointer"],
    },
}
_TOOL_DEFINITIONS: tuple[tuple[str, str, dict[str, Any], list[str], str], ...] = (
    ("fal.search_models", "Search Fal's live model registry by keywords/category and get endpoint IDs. Never guesses endpoints.", {
        "query": {"type": "string", "description": "Search text, e.g. text-to-video or speech"},
        "category": {"type": "string", "description": "Optional exact Fal category. Omit to search all categories, including 3D, audio, training and analysis."},
        "limit": {"type": "integer", "description": "Results per page (1-100, default 20)"},
        "cursor": {"type": "string", "description": "Optional next_cursor from a previous result"},
    }, [], "auto"),
    ("fal.model_details", "Get live Fal model metadata and complete OpenAPI input/output schema before choosing arguments.", {
        "model_id": _MODEL,
        "include_schema": {"type": "boolean", "description": "Include expanded OpenAPI 3.0 schema (default true)"},
    }, ["model_id"], "auto"),
    ("fal.model_io", "Inspect the selected endpoint’s inference input and result schemas. Follow local references or page large sections using pointer and offset.", {
        "model_id": _MODEL, "pointer": _POINTER, "offset": _OFFSET,
    }, ["model_id"], "auto"),
    ("fal.list_jobs", "Recover model IDs, request IDs and last known statuses from Graphlink’s local Fal history. Use fal.status for current state.", {
        "model_id": _MODEL,
    }, [], "auto"),
    ("fal.model_pricing", "Get live Fal.ai per-unit pricing (no charge incurred by lookup).", {
        "model_id": _MODEL,
    }, ["model_id"], "auto"),
    ("fal.estimate_cost", "Ask Fal.ai to estimate model cost based on call count or billing units before a paid run.", {
        "model_id": _MODEL,
        "quantity": {"type": "integer", "description": "Positive count (default 1)"},
        "basis": {"type": "string", "enum": ["historical_api_price", "unit_price"],
                  "description": "Historical price per request or known price per billing unit"},
    }, ["model_id"], "auto"),
    ("fal.submit", "Queue a paid fal.ai model invocation. Requires explicit approval for EACH call.", {
        "model_id": _MODEL, "arguments": _ARGS, "options": _OPTIONS, "input_bindings": _BINDINGS,
    }, ["model_id", "arguments"], "always"),
    ("fal.run", "Run a paid fal.ai model synchronously. Requires explicit approval for EACH call.", {
        "model_id": _MODEL, "arguments": _ARGS, "options": _OPTIONS, "input_bindings": _BINDINGS,
    }, ["model_id", "arguments"], "always"),
    ("fal.status", "Read a fal.ai queued request status and optional runner logs.", {
        "model_id": _MODEL, "request_id": _ID, "logs": {"type": "boolean"},
    }, ["model_id", "request_id"], "auto"),
    ("fal.result", "Read a completed result or a specific field; returns typed asset URLs and JSON Pointers for chaining. Page large values using offset.", {
        "model_id": _MODEL, "request_id": _ID, "pointer": _POINTER, "offset": _OFFSET,
    }, ["model_id", "request_id"], "auto"),
    ("fal.cancel", "Request cancellation of an outstanding fal.ai queued job. Requires approval.", {
        "model_id": _MODEL, "request_id": _ID,
    }, ["model_id", "request_id"], "always"),
    ("fal.wait", "Poll an existing fal.ai queued request until completion or a bounded timeout.", {
        "model_id": _MODEL, "request_id": _ID,
        "timeout": {"type": "number", "description": "Maximum wait in seconds (default 180, max 3600)."},
    }, ["model_id", "request_id"], "auto"),
    ("fal.upload", "Upload a LOCAL file to fal.ai CDN, revealing its contents to fal. Requires explicit approval EVERY time.", {
        "path": {"type": "string", "description": "Existing local file path; verify it contains no secrets before approving."},
    }, ["path"], "always"),
)


def _format(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _bound_arguments(client, arguments: dict) -> dict:
    inputs = arguments.get("arguments", {})
    if not isinstance(inputs, dict):
        raise FalError("Model arguments must be a JSON object.")
    inputs = dict(inputs)
    bindings = arguments.get("input_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 12:
        raise FalError("input_bindings must be an array with at most 12 entries.")
    sources = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise FalError("Each input binding must be an object.")
        name = binding.get("argument")
        if not isinstance(name, str) or not name or len(name) > 300 or name in inputs:
            raise FalError("Each binding needs a unique input name absent from arguments.")
        model = model_path(binding.get("model_id", ""))
        rid = binding.get("request_id", "")
        route = request_path(model, rid)
        if route not in sources:
            sources[route] = client.result(model, rid)
        if "pointer" not in binding:
            raise FalError("Each input binding must specify a pointer.")
        inputs[name] = select_pointer(sources[route], binding["pointer"])
    return inputs


def _result_view(result, pointer="", offset=0):
    page = page_value(select_pointer(result, pointer), offset=offset)
    assets = []
    chars = 0
    for asset in result_assets(result):
        size = len(_format(asset))
        if chars + size > 6000:
            break
        assets.append(asset)
        chars += size
    return {**page, "assets": assets, "media_urls": [a["url"] for a in assets]}


def _invoke(manager, operation: str, arguments: dict, workspace: Path | None = None) -> object:
    """One synchronous operation, always run off the event loop."""
    from backend.api._settings_shared import run_locked

    if operation == "fal.list_jobs":
        target = model_path(arguments["model_id"]) if "model_id" in arguments else None
        fields = ("modelId", "requestId", "status", "createdAt", "updatedAt")
        jobs = [{key: job.get(key) for key in fields} for job in manager.get_fal_jobs()
                if target is None or job.get("modelId") == target]
        return {"data": jobs}
    client = FalClient(manager.get_fal_api_key())
    if operation == "fal.search_models":
        catalog = client.search_models(
            query=arguments.get("query", ""), category=arguments.get("category", ""),
            limit=arguments.get("limit", 20), cursor=arguments.get("cursor"),
        )
        if len(_format(catalog)) <= 18000:
            return {"data": catalog}
        # Keep every endpoint and the cursor even when provider metadata is verbose.
        models = [{"endpoint_id": model["endpoint_id"]} for model in catalog["models"]
                  if isinstance(model, dict) and isinstance(model.get("endpoint_id"), str)]
        return {"data": {"models": models, "has_more": catalog.get("has_more"),
                         "next_cursor": catalog.get("next_cursor")},
                "notice": "Use fal.model_details for metadata on an endpoint."}
    if operation == "fal.upload":
        from backend.harness.workspace import resolve_under_root
        path = arguments.get("path")
        if not isinstance(path, str) or not path:
            raise FalError("path must be a local file path.")
        if workspace is None:
            raise FalError("A bound workspace is required to upload a local file.")
        return {"url": client.upload_file(str(resolve_under_root(workspace, path)))}
    candidate = arguments.get("model_id")
    if not isinstance(candidate, str):
        raise FalError("model_id must be a model endpoint string.")
    model = model_path(candidate)
    value = arguments.get("request_id")
    request_id = value if isinstance(value, str) else ""

    notice = {}

    def record_job(*args):
        # Remote success survives a local settings write failure. In particular,
        # always hand the accepted request ID back to the agent for recovery.
        try:
            run_locked(manager.upsert_fal_job, *args)
        except Exception:
            notice["history_warning"] = "Local job history could not be saved. Keep the request ID."

    if operation == "fal.model_details":
        details = client.model_details(model, include_schema=arguments.get("include_schema") is not False)
        if len(_format(details)) > 20000:
            return {"data": {"endpoint_id": model, "metadata": page_value(details.get("metadata", {}))},
                    "notice": "Use fal.model_io for this model’s paginated input and output schemas."}
        return {"data": details, "media_urls": []}
    elif operation == "fal.model_io":
        contract = model_contract(client.model_details(model))
        return page_value(select_pointer(contract, arguments.get("pointer", "")),
                          offset=arguments.get("offset", 0))
    elif operation == "fal.model_pricing":
        output = client.model_pricing(model)
    elif operation == "fal.estimate_cost":
        output = client.estimate_cost(
            model, quantity=arguments.get("quantity", 1),
            basis=arguments.get("basis", "historical_api_price"),
        )
    elif operation == "fal.submit":
        output = client.submit(model, _bound_arguments(client, arguments), options=arguments.get("options"))
        request_id = output["request_id"]
        record_job(model, request_id, output.get("status", "IN_QUEUE"))
    elif operation == "fal.run":
        return _result_view(client.run(model, _bound_arguments(client, arguments), options=arguments.get("options")))
    elif operation == "fal.status":
        output = client.status(model, request_id, logs=arguments.get("logs") is True)
        state = "FAILED" if output["status"] == "COMPLETED" and output.get("error") else output["status"]
        record_job(model, request_id, state)
        notice["job_status"] = state
    elif operation == "fal.result":
        output = client.result(model, request_id)
        record_job(model, request_id, "COMPLETED", output)
        return {**_result_view(output, arguments.get("pointer", ""), arguments.get("offset", 0)), **notice}
    elif operation == "fal.cancel":
        output = client.cancel(model, request_id)
        record_job(model, request_id, str(output.get("status", "CANCELLATION_REQUESTED")))
    elif operation == "fal.wait":
        timeout = arguments.get("timeout", 180)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise FalError("timeout must be a number.")
        try:
            output = client.wait(model, request_id, timeout=float(timeout))
        except FalJobFailed:
            record_job(model, request_id, "FAILED")
            raise
        record_job(model, request_id, "COMPLETED", output)
        return {**_result_view(output), **notice}
    else:
        raise FalError("Unknown fal tool.")
    response = {**page_value(output), "media_urls": [], **notice}
    if request_id:
        response.update({"model_id": model, "request_id": request_id})
    return response


def register_fal_tools(registry: ToolRegistry, settings_manager) -> None:
    """Declare real tools, with fresh credentials read for every invocation."""
    if settings_manager is None:
        return
    for name, description, properties, required, approval in _TOOL_DEFINITIONS:
        spec = ToolSpec(
            name=name, description=description,
            input_schema={"type": "object", "properties": properties, "required": required},
        )

        async def handler(call: ToolCall, ctx: RunContext, *, _operation=name) -> ToolResult:
            try:
                output = await asyncio.to_thread(
                    _invoke, settings_manager, _operation, call.arguments,
                    getattr(ctx, "harness_workspace_dir", None),
                )
                return ToolResult(content=_format(output))
            except (FalError, ValueError, TypeError, OverflowError) as exc:
                return ToolResult(content=str(exc), is_error=True)

        registry.register(
            spec, handler,
            scopes={PROVIDER_CALL, FS_READ} if name == "fal.upload" else {PROVIDER_CALL},
            approval=approval,
            # Explicit approval each time: "session" grants and identical-call
            # fingerprints must NEVER silently authorize another paid run or
            # an upload of local user data.
            always_reprompt=(lambda call: True) if approval == "always" else None,
        )
