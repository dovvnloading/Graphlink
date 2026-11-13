"""Per-session Fal Media Studio controller.

User-visible media generation is always queue-backed, quote-reviewed, and
bound to one live node and one exact argument fingerprint. The studio never
calls Fal while the user is merely browsing/searching/selecting a model.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import secrets
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from backend.api._settings_shared import run_locked
from backend.domain.node_access import optional_node, require_node
from backend.domain.node_states import FalMediaState
from backend.fal_integration import FalClient, FalError, model_path
from backend.fal_media_domain import (
    MAX_CATALOG_MODELS, completed_media, model_catalog_page,
    read_media, request_arguments, store_media, summarize_schema,
)

logger = logging.getLogger(__name__)
_ACTIVE = {"submitting", "queued", "running", "cancelling"}
_CACHE_TTL = 150.0


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _fingerprint(model_id: str, arguments: dict) -> str:
    raw = _json({"model": model_id, "args": arguments})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quote_cost(client: FalClient, model_id: str) -> dict:
    """Price and estimate failures degrade to *unknown*, never fabricated $0."""
    snapshot: dict[str, Any] = {
        "price": None, "unit": "", "currency": "",
        "estimate": None, "warning": "",
    }
    try:
        rates = client.model_pricing(model_id).get("prices", [])
        match = next((r for r in rates if isinstance(r, dict)
                      and r.get("endpoint_id") == model_id), None)
        if match:
            raw = match.get("unit_price")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0:
                snapshot["price"] = float(raw)
            snapshot["unit"] = str(match.get("unit") or "")[:80]
            snapshot["currency"] = str(match.get("currency") or "")[:4]
    except (FalError, TypeError, ValueError):
        snapshot["warning"] = "Current unit pricing was unavailable."
    try:
        estimate = client.estimate_cost(model_id, quantity=1)
        raw = estimate.get("total_cost")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0:
            snapshot["estimate"] = float(raw)
            if not snapshot["currency"]:
                snapshot["currency"] = str(estimate.get("currency") or "")[:4]
    except (FalError, TypeError, ValueError):
        if not snapshot["warning"]:
            snapshot["warning"] = "A per-request estimate was unavailable."
    return snapshot


class FalMediaController:
    def __init__(self, bus, document, settings_manager):
        self.bus = bus
        self.document = document
        self.settings = settings_manager
        self._catalog_generation: dict[str, int] = {}
        self._schema_generation: dict[str, int] = {}
        self._active_polls: set[str] = set()
        self._catalog_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()

    def _live(self, node_id):
        return optional_node(self.document.nodes, node_id, "fal_media", FalMediaState)

    async def _publish(self):
        await self.bus.publish("scene")

    def _client(self) -> FalClient:
        return FalClient(self.settings.get_fal_api_key())

    def _favorites(self) -> list[str]:
        return self.settings.get_fal_media_favorites()

    async def search(self, node_id: str, mode: str, operation: str,
                     query: str = "", cursor: str = "") -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        seq = self._catalog_generation.get(node_id, 0) + 1
        self._catalog_generation[node_id] = seq
        signature = _json({"mode": mode, "operation": operation, "query": query})
        current = {}
        try:
            current = json.loads(node.state.catalog_json) if node.state.catalog_json else {}
        except ValueError:
            pass
        if not isinstance(current, dict):
            current = {}
        append = bool(cursor and current.get("signature") == signature)
        retained = current.get("models", []) if append else []
        if not isinstance(retained, list):
            retained = []
        node.state.catalog_json = _json({
            **current, "status": "loading", "signature": signature,
            "mode": mode, "operation": operation, "query": query,
            "models": retained, "error": "", "favorites": self._favorites(),
        })
        await self._publish()
        try:
            cache_key = (mode, operation, query, cursor)
            cached = self._catalog_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < _CACHE_TTL:
                response = cached[1]
            else:
                response = await asyncio.to_thread(
                    model_catalog_page, self._client(),
                    mode=mode, operation=operation, query=query,
                    cursor=cursor or None, limit=40,
                )
                self._catalog_cache[cache_key] = (time.monotonic(), response)
                self._catalog_cache.move_to_end(cache_key)
                while len(self._catalog_cache) > 24:
                    self._catalog_cache.popitem(last=False)
        except (FalError, TypeError, ValueError) as exc:
            live = self._live(node_id)
            if live is not None and self._catalog_generation.get(node_id) == seq:
                live.state.catalog_json = _json({
                    "status": "error", "error": str(exc)[:250], "models": retained,
                    "signature": signature, "mode": mode, "operation": operation,
                    "query": query, "favorites": self._favorites(),
                    "hasMore": False, "nextCursor": "",
                })
                await self._publish()
            return
        live = self._live(node_id)
        if live is None or self._catalog_generation.get(node_id) != seq:
            return
        models = retained + response["models"]
        unique = {row["id"]: row for row in models if isinstance(row, dict) and isinstance(row.get("id"), str)}
        sorted_rows = sorted(unique.values(), key=lambda x: (
            x.get("price") is None,
            str(x.get("unit") or "").casefold(), str(x.get("currency") or ""),
            float(x["price"]) if isinstance(x.get("price"), (int, float)) else math.inf,
            str(x.get("name") or "").casefold(),
        ))[:MAX_CATALOG_MODELS]
        live.state.catalog_json = _json({
            "status": "ready", "models": sorted_rows,
            "hasMore": bool(response["hasMore"]) and len(sorted_rows) < MAX_CATALOG_MODELS,
            "nextCursor": response["nextCursor"],
            "signature": signature, "mode": mode,
            "operation": operation, "query": query,
            "pricingWarning": response["pricingWarning"],
            "favorites": self._favorites(), "error": "",
        })
        await self._publish()

    async def inspect(self, node_id: str, model_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        model_id = model_path(model_id)
        seq = self._schema_generation.get(node_id, 0) + 1
        self._schema_generation[node_id] = seq
        node.state.schema_json = _json({"status": "loading", "modelId": model_id, "fields": []})
        await self._publish()
        try:
            def lookup():
                client = self._client()
                details = client.model_details(model_id)
                schema = summarize_schema(details)
                # A model's display metadata is untrusted; never insert it
                # into HTML, only JSON for React to render as plain text.
                return schema
            schema = await asyncio.to_thread(lookup)
        except (FalError, TypeError, ValueError) as exc:
            schema = {"fields": [], "required": [], "warning": str(exc)[:240]}
        live = self._live(node_id)
        if live is None or self._schema_generation.get(node_id) != seq:
            return
        if read_media(live)["modelId"] != model_id:
            return  # Stale model selection; do not paint the wrong inputs.
        live.state.schema_json = _json({"status": "ready", "modelId": model_id, **schema})
        await self._publish()

    async def set_favorite(self, node_id: str, model_id: str, enabled: bool) -> None:
        require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        await asyncio.to_thread(run_locked, self.settings.set_fal_media_favorite, model_id, enabled)
        favorites = self._favorites()
        for node in self.document.nodes.values():
            if node.kind != "fal_media" or not isinstance(node.state, FalMediaState):
                continue
            try:
                current = json.loads(node.state.catalog_json or "{}")
            except (TypeError, ValueError):
                current = {}
            if isinstance(current, dict):
                current["favorites"] = favorites
                node.state.catalog_json = _json(current)
        await self._publish()

    async def prepare(self, node_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        media = read_media(node)
        if media["status"] in _ACTIVE:
            raise FalError("Wait for the current generation before preparing another.")
        model_id = model_path(media["modelId"])
        args = request_arguments(media)
        fingerprint = _fingerprint(model_id, args)
        media["quote"] = {"status": "loading"}
        store_media(node, media)
        await self._publish()
        cost = await asyncio.to_thread(_quote_cost, self._client(), model_id)
        live = self._live(node_id)
        if live is None:
            return
        current = read_media(live)
        if current["modelId"] != model_id or _fingerprint(model_id, request_arguments(current)) != fingerprint:
            return
        current["quote"] = {
            "status": "ready", "id": secrets.token_urlsafe(24),
            "modelId": model_id, "fingerprint": fingerprint,
            "expiresAt": int(time.time()) + 180, **cost,
        }
        store_media(live, current)
        await self._publish()

    async def generate(self, node_id: str, approval_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        media = read_media(node)
        if media["status"] in _ACTIVE:
            raise FalError("This node already has an active generation.")
        quote = media.get("quote") if isinstance(media.get("quote"), dict) else {}
        args = request_arguments(media)
        model_id = model_path(media["modelId"])
        if not (
            isinstance(approval_id, str) and approval_id
            and quote.get("status") == "ready"
            and secrets.compare_digest(str(quote.get("id") or ""), approval_id)
            and quote.get("expiresAt", 0) >= time.time()
            and quote.get("modelId") == model_id
            and quote.get("fingerprint") == _fingerprint(model_id, args)
        ):
            raise FalError("Quote expired or inputs changed. Review the cost again before generating.")
        media.update({"status": "submitting", "requestId": "", "queuePosition": 0,
                      "error": "", "quote": None})
        store_media(node, media)  # consume quote BEFORE awaiting network
        await self._publish()
        try:
            response = await asyncio.to_thread(
                self._client().submit, model_id, args,
                options={"store_io": False},
            )
        except (FalError, TypeError, ValueError) as exc:
            live = self._live(node_id)
            if live is not None and live is node:
                failed = read_media(live)
                failed["status"] = "failed"
                failed["error"] = str(exc)[:250]
                store_media(live, failed)
                await self._publish()
            return
        request_id = str(response["request_id"])
        # Even if the user deleted the node while Fal accepted the job, keep
        # its ID in Settings job history; never silently lose a paid request.
        await asyncio.to_thread(run_locked, self.settings.upsert_fal_job,
                                model_id, request_id, str(response.get("status") or "IN_QUEUE"))
        live = self._live(node_id)
        if live is not None and live is node:
            pending = read_media(live)
            pending.update({"requestId": request_id, "status": "queued",
                            "queuePosition": int(response.get("queue_position") or 0)})
            store_media(live, pending)
            await self._publish()

    async def refresh(self, node_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        if node_id in self._active_polls:
            return  # One network request at a time per node.
        media = read_media(node)
        model_id, request_id = media["modelId"], media["requestId"]
        if not request_id or media["status"] not in {"queued", "running", "cancelling", "stalled"}:
            return
        self._active_polls.add(node_id)
        try:
            def retrieve():
                client = self._client()
                status = client.status(model_id, request_id)
                if status["status"] == "COMPLETED" and not status.get("error"):
                    return status, client.result(model_id, request_id)
                return status, None
            status, result = await asyncio.to_thread(retrieve)
        except (FalError, TypeError, ValueError) as exc:
            live = self._live(node_id)
            if live is node and read_media(live)["requestId"] == request_id:
                update = read_media(live)
                update["status"] = "stalled"
                update["error"] = f"Status unavailable: {str(exc)[:180]}"
                store_media(live, update)
                await self._publish()
            return
        finally:
            self._active_polls.discard(node_id)
        live = self._live(node_id)
        if live is None or live is not node or read_media(live)["requestId"] != request_id:
            return
        update = read_media(live)
        code = status["status"]
        if code == "COMPLETED":
            if status.get("error"):
                update["status"] = "failed"
                update["error"] = "Fal marked the request as unsuccessful. Review inputs or try a different model."
            else:
                update = completed_media(update, result)
        else:
            update["status"] = "queued" if code == "IN_QUEUE" else "running"
            update["queuePosition"] = int(status.get("queue_position") or 0)
            update["error"] = ""
        store_media(live, update)
        await asyncio.to_thread(run_locked, self.settings.upsert_fal_job,
                                model_id, request_id, code, result)
        await self._publish()

    async def cancel(self, node_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        media = read_media(node)
        if media["status"] not in {"queued", "running", "stalled"} or not media["requestId"]:
            raise FalError("No cancellable Fal job exists for this node.")
        model_id, request_id = media["modelId"], media["requestId"]
        output = await asyncio.to_thread(self._client().cancel, model_id, request_id)
        live = self._live(node_id)
        if live is None or live is not node or read_media(live)["requestId"] != request_id:
            return
        update = read_media(live)
        code = str(output.get("status") or "")
        if code == "OK":
            update["status"] = "cancelled"
        elif code == "ALREADY_COMPLETED":
            update["status"] = "queued"  # refresh for the actual terminal result
        else:
            update["status"] = "cancelling"
        store_media(live, update)
        await asyncio.to_thread(run_locked, self.settings.upsert_fal_job,
                                model_id, request_id, code)
        await self._publish()
