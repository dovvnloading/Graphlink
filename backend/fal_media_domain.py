"""Fal Media Studio domain rules.

No HTTP, WebSocket, files, or UI dependencies. A node stores just a small
JSON state; heavy catalog/schema data is deliberately transient and omitted
from session serialization. A model's billing unit is always displayed:
$0.01/image and $0.01/megapixel are NOT comparable prices per output.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from backend.fal_integration import FalError, media_urls, model_path

MODES = {
    "image": ("text-to-image", "image-to-image"),
    "video": ("text-to-video", "image-to-video", "video-to-video"),
    "audio": ("text-to-audio", "text-to-speech", "audio-to-audio"),
}
DEFAULT_OPERATION = {mode: categories[0] for mode, categories in MODES.items()}
MAX_ARGUMENT_BYTES = 24 * 1024 * 1024
MAX_HISTORY = 12
MAX_CATALOG_MODELS = 120
MAX_RESULTS = 12


def fresh_media() -> dict[str, Any]:
    return {
        "mode": "image",
        "operation": "text-to-image",
        "modelId": "",
        "modelName": "",
        "prompt": "",
        "argumentsJson": "{}",
        "status": "idle",
        "requestId": "",
        "queuePosition": 0,
        "error": "",
        "history": [],
        "quote": None,
    }


def read_media(node) -> dict[str, Any]:
    raw = getattr(node.state, "media_json", "")
    state = fresh_media()
    if isinstance(raw, str) and raw:
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = {}
        if isinstance(decoded, dict):
            state.update({key: value for key, value in decoded.items() if key in state})
    if state["mode"] not in MODES:
        state["mode"] = "image"
    if state["operation"] not in MODES[state["mode"]]:
        state["operation"] = DEFAULT_OPERATION[state["mode"]]
    if not isinstance(state.get("history"), list):
        state["history"] = []
    state["history"] = state["history"][:MAX_HISTORY]
    return state


def store_media(node, state: dict) -> None:
    node.state.media_json = json.dumps(
        {key: state.get(key) for key in fresh_media()},
        ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    )


def validate_edit(media: dict, mode: str, operation: str, model_id: str,
                  model_name: str, prompt: str, arguments_json: str) -> dict:
    """Strictly validate an incoming user draft without modifying the caller."""
    if mode not in MODES or operation not in MODES[mode]:
        raise FalError("Select a valid Image, Video, or Audio generation category.")
    if not isinstance(model_id, str):
        raise FalError("Model ID must be text.")
    if model_id:
        model_id = model_path(model_id)
    if not isinstance(model_name, str) or len(model_name) > 160:
        raise FalError("Invalid model name.")
    if not isinstance(prompt, str) or len(prompt) > 16000:
        raise FalError("The prompt cannot exceed 16,000 characters.")
    arguments = parse_arguments(arguments_json)
    updated = copy.deepcopy(media)
    active = updated.get("status") in {"submitting", "queued", "running", "cancelling", "stalled"}
    if active and (
        mode != updated["mode"] or operation != updated["operation"]
        or model_id != updated["modelId"] or model_name != updated["modelName"]
        or prompt != updated["prompt"] or arguments != parse_arguments(updated["argumentsJson"])
    ):
        raise FalError("A job is active. Wait for it to finish before editing its inputs.")
    updated.update({
        "mode": mode, "operation": operation, "modelId": model_id,
        "modelName": model_name.strip(), "prompt": prompt,
        "argumentsJson": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        "quote": None,
    })
    return updated


def parse_arguments(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise FalError("Model input JSON must be under 24 MiB.")
    try:
        decoded = json.loads(raw or "{}")
        # Rerender with allow_nan=False to reject Infinity and NaN.
        json.dumps(decoded, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FalError("Model inputs must be valid finite JSON.") from exc
    if not isinstance(decoded, dict):
        raise FalError("Model input must be a JSON object.")
    return decoded


def request_arguments(media: dict) -> dict:
    """Combine visible prompt with model-specific JSON; explicit JSON wins."""
    args = parse_arguments(media.get("argumentsJson", "{}"))
    prompt = str(media.get("prompt") or "").strip()
    if prompt and not args.get("prompt"):
        args["prompt"] = prompt
    if not args:
        raise FalError("Enter a prompt or model-specific input parameters before generating.")
    return args


def _safe_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        return ""
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        return ""
    return value


def outputs_for(data: Any, mode: str) -> list[dict[str, str]]:
    """Extract HTTPS media URLs, retaining modality for browser playback."""
    result: list[dict[str, str]] = []
    for raw_url in media_urls(data, limit=MAX_RESULTS * 2):
        url = _safe_url(raw_url)
        if not url or any(item["url"] == url for item in result):
            continue
        suffix = urlsplit(url).path.lower()
        if suffix.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")):
            kind = "image"
        elif suffix.endswith((".mp4", ".webm", ".mov", ".m4v")):
            kind = "video"
        elif suffix.endswith((".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a")):
            kind = "audio"
        else:
            kind = mode
        result.append({"url": url, "kind": kind})
        if len(result) >= MAX_RESULTS:
            break
    return result


def completed_media(media: dict, result: Any, *, now: str | None = None) -> dict:
    updated = copy.deepcopy(media)
    outputs = outputs_for(result, updated["mode"])
    updated["status"] = "completed"
    updated["error"] = ""
    updated["queuePosition"] = 0
    updated["quote"] = None
    updated["history"] = [{
        "modelId": updated["modelId"],
        "modelName": updated["modelName"] or updated["modelId"],
        "mode": updated["mode"],
        "prompt": updated["prompt"][:400],
        "requestId": updated["requestId"],
        "createdAt": now or datetime.now(timezone.utc).isoformat(),
        "outputs": outputs,
    }, *updated["history"]][:MAX_HISTORY]
    return updated


def _display_price(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (float, int)):
        return None
    price = float(raw)
    return price if math.isfinite(price) and price >= 0 else None


def model_catalog_page(client, *, mode: str, operation: str, query: str,
                       cursor: str | None = None, limit: int = 40) -> dict:
    """Read an up-to-date Fal catalog page with batch account-level prices."""
    if mode not in MODES or operation not in MODES[mode]:
        raise FalError("Invalid Fal Media generation category.")
    if not isinstance(query, str) or len(query) > 200:
        raise FalError("Search must be 200 characters or less.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise FalError("Catalog page size must be 1-50.")
    search = client.search_models(query=query, category=operation, limit=limit, cursor=cursor)
    models = search.get("models", [])
    if not isinstance(models, list):
        raise FalError("Fal returned an invalid model catalog.")
    normalized = []
    seen = set()
    for item in models[:50]:
        if not isinstance(item, dict):
            continue
        endpoint = item.get("endpoint_id")
        try:
            endpoint = model_path(endpoint)
        except FalError:
            continue
        if endpoint in seen:
            continue
        seen.add(endpoint)
        meta = item.get("metadata")
        meta = meta if isinstance(meta, dict) else {}
        thumbnail = _safe_url(meta.get("thumbnail_url"))
        normalized.append({
            "id": endpoint,
            "name": str(meta.get("display_name") or endpoint)[:120],
            "description": str(meta.get("description") or "")[:400],
            "category": str(meta.get("category") or operation)[:70],
            "thumbnailUrl": thumbnail,
            "tags": [str(v)[:30] for v in (meta.get("tags") or [])[:6]
                     if isinstance(v, str)] if isinstance(meta.get("tags"), list) else [],
            "price": None,
            "unit": "",
            "currency": "",
        })
    pricing_warning = ""
    if normalized:
        try:
            response = client.model_pricing_batch([item["id"] for item in normalized])
            prices = response.get("prices", [])
            for price in prices if isinstance(prices, list) else []:
                if not isinstance(price, dict):
                    continue
                item = next((x for x in normalized if x["id"] == price.get("endpoint_id")), None)
                if item is None:
                    continue
                item["price"] = _display_price(price.get("unit_price"))
                item["unit"] = str(price.get("unit") or "")[:80]
                item["currency"] = str(price.get("currency") or "")[:4]
        except FalError:
            pricing_warning = "Pricing could not be verified. Generation may still incur charges."
    # Sort within identical billing unit/currency cohorts (then by rate).
    # Never imply $0.03 per megapixel is less expensive per image than $0.05
    # per image. Unpriced models are last, rather than silently $0.
    normalized.sort(key=lambda x: (
        x["price"] is None,
        x["unit"].casefold(), x["currency"],
        float(x["price"]) if x["price"] is not None else math.inf,
        x["name"].casefold(), x["id"],
    ))
    return {
        "models": normalized,
        "hasMore": bool(search.get("has_more")) and isinstance(search.get("next_cursor"), str),
        "nextCursor": str(search.get("next_cursor") or "")[:512],
        "operation": operation, "mode": mode, "query": query,
        "pricingWarning": pricing_warning,
    }


def summarize_schema(details: dict) -> dict:
    """Bounded, schema-aware controls with fallback for advanced JSON input."""
    if not isinstance(details, dict):
        return {"fields": [], "required": [], "warning": "Model schema unavailable"}
    openapi = details.get("openapi") or {}
    if not isinstance(openapi, dict):
        return {"fields": [], "required": [], "warning": "Model schema unavailable"}
    components = openapi.get("components", {})
    defs = components.get("schemas", {}) if isinstance(components, dict) else {}
    def deref(value, depth=0):
        if not isinstance(value, dict) or depth >= 5:
            return value if isinstance(value, dict) else {}
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            return deref(defs.get(ref.rsplit("/", 1)[-1], {}), depth + 1)
        if "allOf" in value and isinstance(value["allOf"], list):
            merged = {"type": "object", "properties": {}, "required": []}
            for member in value["allOf"][:8]:
                child = deref(member, depth + 1)
                merged["properties"].update(child.get("properties", {}))
                merged["required"].extend(child.get("required", []))
            return merged
        return value
    found = {}
    for route in (openapi.get("paths") or {}).values():
        if not isinstance(route, dict):
            continue
        post = route.get("post", {})
        if not isinstance(post, dict):
            continue
        content = (post.get("requestBody") or {}).get("content", {})
        if isinstance(content, dict):
            json_body = content.get("application/json", {})
            if isinstance(json_body, dict):
                found = deref(json_body.get("schema", {}))
                if found:
                    break
    if not found:
        for name, schema in (defs.items() if isinstance(defs, dict) else []):
            if isinstance(name, str) and name.endswith("Input") and isinstance(schema, dict):
                found = deref(schema)
                break
    properties = found.get("properties", {}) if isinstance(found, dict) else {}
    required = found.get("required", []) if isinstance(found, dict) else []
    fields: list[dict[str, Any]] = []
    for name, raw in (list(properties.items())[:45] if isinstance(properties, dict) else []):
        if not isinstance(name, str) or len(name) > 80:
            continue
        prop = deref(raw)
        variant = prop.get("anyOf")
        if isinstance(variant, list):
            prop = next((deref(v) for v in variant if deref(v).get("type") != "null"), prop)
        kind = prop.get("type", "string")
        default = prop.get("default")
        if not isinstance(default, (str, int, float, bool, type(None))):
            default = None
        enum = prop.get("enum", [])
        if not isinstance(enum, list):
            enum = []
        fields.append({
            "name": name,
            "type": kind if kind in ("string", "integer", "number", "boolean") else "json",
            "description": str(prop.get("description") or "")[:280],
            "required": name in required,
            "format": str(prop.get("format") or "")[:40],
            "default": default,
            "enum": [v for v in enum if isinstance(v, (str, int, float, bool))][:30],
        })
    return {"fields": fields, "required": [s for s in required if isinstance(s, str)][:50],
            "warning": "" if fields else "No simple parameter schema found. Use Advanced JSON."}
