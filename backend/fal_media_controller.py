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
_ACTIVE = {"submitting", "queued", "running", "cancelling", "stalled", "unconfirmed"}
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
        # Approvals never survive controller recreation or session restore.
        self._quote_generation: dict[str, int] = {}
        self._pending_quotes: dict[str, str] = {}
        self._submissions_in_flight: set[str] = set()
        self._active_polls: set[str] = set()
        self._catalog_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()

    def _live(self, node_id):
        return optional_node(self.document.nodes, node_id, "fal_media", FalMediaState)

    async def _publish(self):
        await self.bus.publish("scene")

    def _client(self, *, public: bool = False) -> FalClient:
        key = self.settings.get_fal_api_key()
        if not public and not key:
            raise FalError("Configure a Fal.ai API key in Settings before running a model.")
        return FalClient(key)

    def _favorites(self) -> list[str]:
        return self.settings.get_fal_media_favorites()

    async def search(self, node_id: str, mode: str, operation: str,
                     query: str = "", cursor: str = "") -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        seq = self._catalog_generation.get(node_id, 0) + 1
        self._catalog_generation[node_id] = seq
        signature = _json({"mode": mode, "operation": operation, "query": query})
        current: dict[str, Any] = {}
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
            # Rates can be account-specific. A credential switch must never
            # reuse another account's cached catalog/prices.
            credential = self.settings.get_fal_api_key()
            cache_key = (hashlib.sha256(credential.encode("utf-8")).digest(),
                         mode, operation, query, cursor)
            cached = self._catalog_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < _CACHE_TTL:
                response = cached[1]
            else:
                response = await asyncio.to_thread(
                    model_catalog_page, self._client(public=True),
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
        # Unfiltered browsing uses one page at a time so every cursor remains reachable.
        models = response["models"] if mode == "all" else retained + response["models"]
        unique = {row["id"]: row for row in models if isinstance(row, dict) and isinstance(row.get("id"), str)}
        sorted_rows = sorted(unique.values(), key=lambda x: (
            x.get("price") is None,
            str(x.get("unit") or "").casefold(), str(x.get("currency") or ""),
            float(x["price"]) if isinstance(x.get("price"), (int, float)) else math.inf,
            str(x.get("name") or "").casefold(),
        ))[:MAX_CATALOG_MODELS]
        live.state.catalog_json = _json({
            "status": "ready", "models": sorted_rows,
            "hasMore": bool(response["hasMore"]) and (mode == "all" or len(sorted_rows) < MAX_CATALOG_MODELS),
            "currentCursor": cursor,
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
                client = self._client(public=True)
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
        seq = self._quote_generation.get(node_id, 0) + 1
        self._quote_generation[node_id] = seq
        self._pending_quotes.pop(node_id, None)
        media["quote"] = {"status": "loading"}
        store_media(node, media)
        await self._publish()
        try:
            cost = await asyncio.to_thread(_quote_cost, self._client(), model_id)
        except (FalError, ValueError, TypeError) as exc:
            live = self._live(node_id)
            if live is node and self._quote_generation.get(node_id) == seq:
                failed = read_media(live)
                failed["quote"] = None
                failed["error"] = str(exc)[:250]
                store_media(live, failed)
                await self._publish()
            return
        live = self._live(node_id)
        if live is not node or self._quote_generation.get(node_id) != seq:
            return
        current = read_media(live)
        if current["status"] in _ACTIVE or current["modelId"] != model_id:
            return
        if _fingerprint(model_id, request_arguments(current)) != fingerprint:
            return
        nonce = secrets.token_urlsafe(24)
        self._pending_quotes[node_id] = nonce
        current["quote"] = {
            "status": "ready", "id": nonce,
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
        quote: dict[str, Any] = media["quote"] if isinstance(media.get("quote"), dict) else {}
        args = request_arguments(media)
        model_id = model_path(media["modelId"])
        if not (
            isinstance(approval_id, str) and approval_id
            and quote.get("status") == "ready"
            and secrets.compare_digest(str(quote.get("id") or ""), approval_id)
            and secrets.compare_digest(self._pending_quotes.get(node_id, ""), approval_id)
            and quote.get("expiresAt", 0) >= time.time()
            and quote.get("modelId") == model_id
            and quote.get("fingerprint") == _fingerprint(model_id, args)
        ):
            if quote.get("status") == "ready" and node_id not in self._pending_quotes:
                # A restored scene may display a persisted quote that cannot
                # be authorized by this controller. Clear it so the UI offers
                # a fresh price review instead of a dead confirmation button.
                media["quote"] = None
                store_media(node, media)
                await self._publish()
            raise FalError("Quote expired or inputs changed. Review the cost again before generating.")
        # Missing local credentials are recoverable without reconciling a remote job.
        try:
            client = self._client()
        except FalError as exc:
            self._pending_quotes.pop(node_id, None)
            media.update({"quote": None, "error": str(exc)})
            store_media(node, media)
            await self._publish()
            return
        # Consume this approval before ANY await. The in-flight marker lets
        # status refresh distinguish a live POST from a restart-interrupted one.
        self._pending_quotes.pop(node_id, None)
        self._submissions_in_flight.add(node_id)
        try:
            media.update({"status": "submitting", "requestId": "", "queuePosition": 0,
                          "error": "", "quote": None})
            store_media(node, media)
            await self._publish()
            try:
                response = await asyncio.to_thread(
                    client.submit, model_id, args,
                    options={"store_io": False},
                )
            except (FalError, TypeError, ValueError) as exc:
                live = self._live(node_id)
                if live is node:
                    failed = read_media(live)
                    # A dropped POST response may mean Fal accepted a paid
                    # job. A normal retry could double-bill the user.
                    failed["status"] = "unconfirmed"
                    failed["error"] = (
                        "Submission outcome is unknown. Fal may have accepted "
                        "and charged this request. Check the Fal dashboard "
                        "before unlocking this Studio to submit another. "
                        f"({type(exc).__name__})"
                    )
                    store_media(live, failed)
                    await self._publish()
                return

            request_id = str(response["request_id"])  # validated by FalClient.submit
            position = response.get("queue_position")
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                position = 0
            # Save the accepted request ID *before* the fallible Settings
            # ledger write. Even if the disk is full, users can track a paid
            # job from its Studio and will not be stuck in "submitting".
            live = self._live(node_id)
            if live is node:
                pending = read_media(live)
                pending.update({"requestId": request_id, "status": "queued",
                                "queuePosition": position})
                store_media(live, pending)
                await self._publish()
            try:
                # Deleted nodes still get a ledger entry when storage works.
                await asyncio.to_thread(run_locked, self.settings.upsert_fal_job,
                                        model_id, request_id, str(response.get("status") or "IN_QUEUE"))
            except Exception as exc:
                logger.warning("Could not record accepted Fal job in settings (%s).", type(exc).__name__)
                live = self._live(node_id)
                if live is node and read_media(live)["requestId"] == request_id:
                    warning = read_media(live)
                    warning["error"] = (
                        "Fal accepted this job but Settings history could not be saved. "
                        "Keep this Studio and copy its Job ID for recovery."
                    )
                    store_media(live, warning)
                    await self._publish()
        finally:
            self._submissions_in_flight.discard(node_id)

    async def refresh(self, node_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        if node_id in self._active_polls:
            return  # One network request at a time per node.
        media = read_media(node)
        if media["status"] == "submitting":
            if node_id in self._submissions_in_flight:
                return  # A POST is actually active in this controller.
            # An interrupted POST may already have charged the user. Do not
            # allow a retry without explicit dashboard acknowledgement.
            media["status"] = "queued" if media["requestId"] else "unconfirmed"
            if not media["requestId"]:
                media["error"] = (
                    "Graphlink restarted during submission. Fal may have accepted "
                    "this paid request. Check the Fal dashboard before unlocking."
                )
            store_media(node, media)
            await self._publish()
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
        if update["status"] not in {"queued", "running", "cancelling", "stalled"}:
            return  # Do not overwrite a terminal cancellation with stale polling.
        code = status["status"]
        if code == "COMPLETED":
            if status.get("error"):
                update["status"] = "failed"
                update["error"] = "Fal marked the request as unsuccessful. Review inputs or try a different model."
            else:
                update = completed_media(update, result)
        else:
            if update["status"] != "cancelling":
                update["status"] = "queued" if code == "IN_QUEUE" else "running"
            position = status.get("queue_position")
            update["queuePosition"] = (
                position if isinstance(position, int) and not isinstance(position, bool) and position >= 0 else 0
            )
            update["error"] = ""
        store_media(live, update)
        try:
            await asyncio.to_thread(run_locked, self.settings.upsert_fal_job,
                                    model_id, request_id,
                                    "FAILED" if code == "COMPLETED" and status.get("error") else code, result)
        except Exception as exc:
            logger.warning("Could not update Fal job history (%s).", type(exc).__name__)
        await self._publish()

    async def acknowledge_unconfirmed(self, node_id: str) -> None:
        """Only the user may clear an ambiguous submission after checking Fal.

        Deliberately no network or automatic retry: the dashboard is the
        authority when Graphlink never received a request ID.
        """
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        media = read_media(node)
        if media["status"] != "unconfirmed":
            raise FalError("This studio has no unconfirmed submission.")
        media.update({"status": "idle", "error": "", "requestId": "", "quote": None})
        store_media(node, media)
        await self._publish()

    async def cancel(self, node_id: str) -> None:
        node = require_node(self.document.nodes, node_id, "fal_media", FalMediaState)
        media = read_media(node)
        if media["status"] not in {"queued", "running", "stalled"} or not media["requestId"]:
            raise FalError("No cancellable Fal job exists for this node.")
        model_id, request_id = media["modelId"], media["requestId"]
        # Commit cancellation intent before the network await so two clicks
        # cannot send overlapping cancellation requests.
        media["status"] = "cancelling"
        store_media(node, media)
        await self._publish()
        try:
            output = await asyncio.to_thread(self._client().cancel, model_id, request_id)
        except (FalError, TypeError, ValueError):
            live = self._live(node_id)
            if live is node and read_media(live)["requestId"] == request_id:
                update = read_media(live)
                if update["status"] == "cancelling":
                    update["status"] = "stalled"
                    update["error"] = "Cancellation could not be confirmed. Check status or retry cancellation."
                    store_media(live, update)
                    await self._publish()
            return
        live = self._live(node_id)
        if live is not node or read_media(live)["requestId"] != request_id:
            return
        update = read_media(live)
        if update["status"] not in {"queued", "running", "cancelling", "stalled"}:
            return
        code = str(output.get("status") or "")
        if code == "OK":
            update["status"] = "cancelled"
        elif code == "ALREADY_COMPLETED":
            update["status"] = "queued"  # refresh for the actual terminal result
        else:
            update["status"] = "cancelling"
        store_media(live, update)
        try:
            await asyncio.to_thread(run_locked, self.settings.upsert_fal_job,
                                    model_id, request_id, code)
        except Exception as exc:
            logger.warning("Could not record Fal cancellation (%s).", type(exc).__name__)
        await self._publish()
