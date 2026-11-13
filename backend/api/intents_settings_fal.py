"""User-facing fal.ai settings actions: no credentials cross the WebSocket.

Long-running network work runs on a worker thread; the UI receives only
non-secret request IDs, status, output summaries, and media URLs.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.api._settings_shared import SettingsSessionState, run_locked
from backend.events import SessionBus
from backend.fal_integration import FalClient, FalError, media_urls
from graphlink_settings_store import SettingsManager


def _json_object(text: str, label: str) -> dict:
    if not isinstance(text, str) or len(text) > 24 * 1024 * 1024:
        raise FalError(f"{label} must be a JSON object (maximum 24 MiB).")
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        raise FalError(f"{label} is not valid JSON.") from None
    if not isinstance(value, dict):
        raise FalError(f"{label} must be a JSON object.")
    return value


def _json_summary(result: Any) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text[:32000] + ("\n[truncated]" if len(text) > 32000 else "")


def _discovery_snapshot(kind: str, data: Any) -> str:
    payload = {"kind": kind, "data": data}
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(text) > 180000:
        # Do not emit half of a JSON payload: preserve a usable valid result
        # with its identity, and direct users to inspect the model online.
        payload = {"kind": kind, "truncated": True,
                   "data": {"endpoint_id": data.get("endpoint_id") if isinstance(data, dict) else "",
                            "notice": "Model schema is too large for the Settings preview."}}
        text = json.dumps(payload, ensure_ascii=False)
    return text


def register_settings_fal_intents(
    bus: SessionBus, manager: SettingsManager, state: SettingsSessionState,
) -> None:
    async def _perform(action) -> None:
        if state.fal_busy:
            state.fal_notice = "Another Fal.ai request is still running in this settings session."
            await bus.publish("app-settings")
            return
        state.fal_busy = True
        state.fal_notice = "Contacting Fal.ai..."
        await bus.publish("app-settings")
        try:
            message = await asyncio.to_thread(action)
            state.fal_notice = str(message)[:400]
        except (FalError, ValueError, TypeError) as exc:
            state.fal_notice = str(exc)[:400]
        except Exception as exc:
            # Never echo an SDK exception or response body: those may contain
            # credentials or user media URLs.
            state.fal_notice = f"Fal.ai operation failed ({type(exc).__name__})."
        finally:
            state.fal_busy = False
            await bus.publish("app-settings")

    async def search_fal_models(query: str = "", category: str = "", limit: int = 20,
                                cursor: str = "") -> None:
        def work() -> str:
            data = FalClient(manager.get_fal_api_key()).search_models(
                query=query, category=category, limit=limit, cursor=cursor or None,
            )
            # Model metadata can contain verbose descriptions. Preserve valid JSON
            # and stay comfortably within the app-settings WebSocket size budget.
            state.fal_discovery_json = _discovery_snapshot("models", data)
            return f"Found {len(data['models'])} model endpoints in the current catalog page."
        await _perform(work)

    async def inspect_fal_model(model_id: str) -> None:
        def work() -> str:
            data = FalClient(manager.get_fal_api_key()).model_details(model_id)
            state.fal_discovery_json = _discovery_snapshot("details", data)
            return f"Loaded model metadata and OpenAPI schema for {model_id}."
        await _perform(work)

    async def get_fal_model_pricing(model_id: str) -> None:
        def work() -> str:
            data = FalClient(manager.get_fal_api_key()).model_pricing(model_id)
            state.fal_discovery_json = _discovery_snapshot("pricing", data)
            return f"Loaded current Fal.ai pricing for {model_id}."
        await _perform(work)

    async def estimate_fal_model_cost(model_id: str, quantity: int = 1,
                                      basis: str = "historical_api_price") -> None:
        def work() -> str:
            data = FalClient(manager.get_fal_api_key()).estimate_cost(
                model_id, quantity=quantity, basis=basis,
            )
            state.fal_discovery_json = _discovery_snapshot("estimate", data)
            return f"Loaded Fal.ai cost estimate for {model_id}."
        await _perform(work)

    async def set_fal_api_key(api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            state.fal_notice = "Enter a valid Fal.ai API key."
            await bus.publish("app-settings")
            return
        await asyncio.to_thread(run_locked, manager.set_fal_api_key, api_key)
        state.fal_notice = "Fal.ai key saved. The key is write-only in the UI."
        await bus.publish("app-settings")

    async def clear_fal_api_key() -> None:
        await asyncio.to_thread(run_locked, manager.set_fal_api_key, "")
        state.fal_notice = "Stored Fal.ai key cleared (environment fallback, if set, still applies)."
        await bus.publish("app-settings")

    async def submit_fal_job(model_id: str, arguments_json: str, options_json: str = "{}") -> None:
        def work() -> str:
            arguments = _json_object(arguments_json, "Model arguments")
            options = _json_object(options_json, "Request options")
            response = FalClient(manager.get_fal_api_key()).submit(model_id, arguments, options=options)
            run_locked(manager.upsert_fal_job, model_id, response["request_id"], response.get("status", "IN_QUEUE"))
            return f"Queued Fal.ai request {response['request_id']}."
        await _perform(work)

    async def run_fal_sync(model_id: str, arguments_json: str, options_json: str = "{}") -> None:
        def work() -> str:
            arguments = _json_object(arguments_json, "Model arguments")
            options = _json_object(options_json, "Request options")
            result = FalClient(manager.get_fal_api_key()).run(model_id, arguments, options=options)
            state.fal_sync_result_json = _json_summary({"data": result, "media_urls": media_urls(result)})
            return "Fal.ai synchronous inference completed."
        await _perform(work)

    async def refresh_fal_job(model_id: str, request_id: str, fetch_result: bool = True) -> None:
        def work() -> str:
            client = FalClient(manager.get_fal_api_key())
            status = client.status(model_id, request_id, logs=False)
            code = status["status"]
            if code == "COMPLETED" and status.get("error"):
                run_locked(manager.upsert_fal_job, model_id, request_id, "FAILED")
                return "Fal.ai request completed with an error. Review the model inputs or request logs."
            if code == "COMPLETED" and fetch_result is True:
                result = client.result(model_id, request_id)
                run_locked(manager.upsert_fal_job, model_id, request_id, code, result)
            else:
                run_locked(manager.upsert_fal_job, model_id, request_id, code)
            return f"Fal.ai request {request_id}: {code}."
        await _perform(work)

    async def cancel_fal_job(model_id: str, request_id: str) -> None:
        def work() -> str:
            output = FalClient(manager.get_fal_api_key()).cancel(model_id, request_id)
            status = str(output.get("status", "CANCELLATION_REQUESTED"))
            run_locked(manager.upsert_fal_job, model_id, request_id, status)
            return f"Fal.ai cancellation response: {status}."
        await _perform(work)

    async def upload_fal_file(path: str) -> None:
        def work() -> str:
            url = FalClient(manager.get_fal_api_key()).upload_file(path)
            state.fal_upload_url = url
            return "File uploaded to Fal.ai CDN. Copy the URL into your model arguments."
        await _perform(work)

    bus.register_intent("app-settings", "searchFalModels", search_fal_models)
    bus.register_intent("app-settings", "inspectFalModel", inspect_fal_model)
    bus.register_intent("app-settings", "getFalModelPricing", get_fal_model_pricing)
    bus.register_intent("app-settings", "estimateFalModelCost", estimate_fal_model_cost)
    bus.register_intent("app-settings", "setFalApiKey", set_fal_api_key)
    bus.register_intent("app-settings", "clearFalApiKey", clear_fal_api_key)
    bus.register_intent("app-settings", "submitFalJob", submit_fal_job)
    bus.register_intent("app-settings", "runFalSync", run_fal_sync)
    bus.register_intent("app-settings", "refreshFalJob", refresh_fal_job)
    bus.register_intent("app-settings", "cancelFalJob", cancel_fal_job)
    bus.register_intent("app-settings", "uploadFalFile", upload_fal_file)
