"""First-class fal.ai Builder tools with per-call paid-operation consent.

They use the same ToolRegistry gates as Graphlink's graph, file and MCP
tools. No key is ever included in a ToolSpec, ToolResult or approval payload.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.fal_integration import FalClient, FalError, media_urls, model_path
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
_TOOL_DEFINITIONS: tuple[tuple[str, str, dict[str, Any], list[str], str], ...] = (
    ("fal.search_models", "Search Fal's live model registry by keywords/category and get endpoint IDs. Never guesses endpoints.", {
        "query": {"type": "string", "description": "Search text, e.g. text-to-video or speech"},
        "category": {"type": "string", "description": "Optional category, e.g. image-to-video"},
        "limit": {"type": "integer", "description": "Results per page (1-100, default 20)"},
        "cursor": {"type": "string", "description": "Optional next_cursor from a previous result"},
    }, [], "auto"),
    ("fal.model_details", "Get live Fal model metadata and complete OpenAPI input/output schema before choosing arguments.", {
        "model_id": _MODEL,
        "include_schema": {"type": "boolean", "description": "Include expanded OpenAPI 3.0 schema (default true)"},
    }, ["model_id"], "auto"),
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
        "model_id": _MODEL, "arguments": _ARGS, "options": _OPTIONS,
    }, ["model_id", "arguments"], "always"),
    ("fal.run", "Run a paid fal.ai model synchronously. Requires explicit approval for EACH call.", {
        "model_id": _MODEL, "arguments": _ARGS, "options": _OPTIONS,
    }, ["model_id", "arguments"], "always"),
    ("fal.status", "Read a fal.ai queued request status and optional runner logs.", {
        "model_id": _MODEL, "request_id": _ID, "logs": {"type": "boolean"},
    }, ["model_id", "request_id"], "auto"),
    ("fal.result", "Retrieve results of a completed fal.ai queued request.", {
        "model_id": _MODEL, "request_id": _ID,
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
    raw = json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)
    if len(raw) > 24000:
        return raw[:24000] + "\n[Result truncated. Inspect job history or download the output media.]"
    return raw


def _invoke(manager, operation: str, arguments: dict) -> object:
    """One synchronous operation, always run off the event loop."""
    client = FalClient(manager.get_fal_api_key())
    if operation == "fal.search_models":
        query = arguments.get("query", "")
        category = arguments.get("category", "")
        limit = arguments.get("limit", 20)
        cursor = arguments.get("cursor")
        return {
            "data": client.search_models(
                query=query, category=category, limit=limit, cursor=cursor,
            )
        }
    if operation == "fal.upload":
        path = arguments.get("path")
        if not isinstance(path, str) or not path:
            raise FalError("path must be a local file path.")
        return {"url": client.upload_file(path)}
    candidate = arguments.get("model_id")
    if not isinstance(candidate, str):
        raise FalError("model_id must be a model endpoint string.")
    model = model_path(candidate)
    value = arguments.get("request_id")
    request_id = value if isinstance(value, str) else ""

    def record_job(*args):
        # The same settings lock as all UI intents: Builder calls can run
        # concurrently with Settings mutations and must not lose writes.
        from backend.api._settings_shared import run_locked
        run_locked(manager.upsert_fal_job, *args)
    if operation == "fal.model_details":
        output = client.model_details(model, include_schema=arguments.get("include_schema") is not False)
    elif operation == "fal.model_pricing":
        output = client.model_pricing(model)
    elif operation == "fal.estimate_cost":
        output = client.estimate_cost(
            model, quantity=arguments.get("quantity", 1),
            basis=arguments.get("basis", "historical_api_price"),
        )
    elif operation == "fal.submit":
        output = client.submit(model, arguments.get("arguments", {}), options=arguments.get("options"))
        record_job(model, output["request_id"], output.get("status", "IN_QUEUE"))
    elif operation == "fal.run":
        output = client.run(model, arguments.get("arguments", {}), options=arguments.get("options"))
    elif operation == "fal.status":
        output = client.status(model, request_id, logs=arguments.get("logs") is True)
        record_job(model, request_id, output["status"])
    elif operation == "fal.result":
        output = client.result(model, request_id)
        record_job(model, request_id, "COMPLETED", output)
    elif operation == "fal.cancel":
        output = client.cancel(model, request_id)
        record_job(model, request_id, str(output.get("status", "CANCELLATION_REQUESTED")))
    elif operation == "fal.wait":
        timeout = arguments.get("timeout", 180)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise FalError("timeout must be a number.")
        output = client.wait(model, request_id, timeout=float(timeout))
        record_job(model, request_id, "COMPLETED", output)
    else:
        raise FalError("Unknown fal tool.")
    return {"data": output, "media_urls": media_urls(output)}


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
                output = await asyncio.to_thread(_invoke, settings_manager, _operation, call.arguments)
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
