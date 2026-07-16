"""Typed, JSON-only boundary between the React composer and the desktop app.

The bridge deliberately exposes view state rather than Qt widgets or filesystem
paths.  Python remains the authority for provider routing, context preparation,
request lifecycle, and attachment ownership.
"""

from __future__ import annotations

import json
import os
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

import graphlink_config as config
from graphlink_composer import ComposerController
from graphlink_config import get_current_palette


_MAX_DRAFT_CHARS = 100_000
_ACTIVE_STATES = frozenset({"preparing", "uploading", "waiting", "generating", "finalizing"})


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_call(target: Any, name: str, default: Any = None, *args: Any) -> Any:
    try:
        method = getattr(target, name)
        return method(*args) if callable(method) else method
    except (AttributeError, TypeError, ValueError, OSError):
        return default


def _clean_label(value: Any, fallback: str, limit: int = 80) -> str:
    label = " ".join(str(value or "").split()).strip()
    if not label:
        return fallback
    return label if len(label) <= limit else label[: limit - 1].rstrip() + "…"


class ComposerBridge(QObject):
    """QWebChannel object with a stable, versioned state contract."""

    stateChanged = Signal(str)
    draftChanged = Signal(str)
    contextReviewChanged = Signal(str)
    streamDelta = Signal(str)
    requestCompleted = Signal(str)
    requestFailed = Signal(str)
    routeChanged = Signal(str)
    themeChanged = Signal(str)
    heightRequested = Signal(int)

    def __init__(self, window, controller: ComposerController | None = None, parent=None):
        super().__init__(parent)
        self.window = window
        self.controller = controller or getattr(window, "composer_controller", None)
        if self.controller is None:
            self.controller = ComposerController(self)
        self._revision = 0
        self._attachment_paths: dict[str, str] = {}
        self._last_height = 0
        self.controller.draftChanged.connect(self._on_draft_changed)
        self.controller.stateChanged.connect(self._on_controller_state_changed)

    @Slot()
    def ready(self):
        self._publish()

    @Slot(str)
    def updateDraft(self, text: str):
        normalized = str(text or "")[:_MAX_DRAFT_CHARS]
        self.controller.update_text(normalized)
        self.draftChanged.emit(normalized)
        self._publish()

    @Slot()
    def send(self):
        state = self._state_payload()
        if state["request"]["state"] in _ACTIVE_STATES:
            return
        if not state["request"]["canSend"]:
            return
        send_message = getattr(self.window, "send_message", None)
        if callable(send_message):
            send_message()
            self._publish()

    @Slot()
    @Slot(str)
    def cancel(self, request_id: str = ""):
        if request_id and request_id != (self.controller.active_request_id or ""):
            return
        callback = getattr(self.window, "_main_request_cancel_callback", None)
        if callable(callback):
            callback()
            self._publish()
            return
        self.controller.cancel(request_id or None)
        self._publish()

    @Slot()
    def reviewContext(self):
        context = self._state_payload()["context"]
        self.contextReviewChanged.emit(json.dumps(context, sort_keys=True))

    @Slot()
    def requestAttachment(self):
        attach_file = getattr(self.window, "attach_file", None)
        if callable(attach_file):
            attach_file()

    @Slot(str)
    def removeContextItem(self, item_id: str):
        path = self._attachment_paths.get(str(item_id or ""))
        remove = getattr(self.window, "_handle_attachment_pill_removed", None)
        if path and callable(remove):
            remove(path)
            self._publish()

    @Slot(int)
    def resize(self, height: int):
        bounded = max(220, min(520, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)

    def _on_draft_changed(self, draft):
        self._publish()

    def _on_controller_state_changed(self, state, message):
        self._publish()

    def _context_anchor(self) -> dict[str, str] | None:
        node = getattr(self.window, "current_node", None)
        if node is None:
            return None
        node_id = (
            getattr(node, "persistent_id", None)
            or getattr(node, "node_id", None)
            or getattr(node, "id", None)
            or f"node-{id(node)}"
        )
        label = (
            getattr(node, "title", None)
            or getattr(node, "text", None)
            or getattr(node, "name", None)
            or type(node).__name__
        )
        return {
            "id": str(node_id),
            "label": _clean_label(label, type(node).__name__),
            "type": type(node).__name__,
        }

    def _context_items(self) -> list[dict[str, Any]]:
        raw_items = getattr(self.window, "pending_attachments", []) or []
        items: list[dict[str, Any]] = []
        self._attachment_paths = {}
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("attachment_id") or f"attachment-{index}")
            path = str(raw.get("path") or "")
            if path:
                self._attachment_paths[item_id] = path
            items.append(
                {
                    "id": item_id,
                    "name": _clean_label(raw.get("name"), "Attachment", 120),
                    "kind": _clean_label(raw.get("kind"), "document", 24),
                    "tokenCount": _safe_int(raw.get("token_count")),
                    "preparationState": _clean_label(
                        raw.get("preparation_state"), "ready", 24
                    ),
                    "contextLabel": _clean_label(raw.get("context_label"), "", 120),
                }
            )
        return items

    def _route(self) -> dict[str, Any]:
        settings = getattr(self.window, "settings_manager", None)
        mode = str(_safe_call(settings, "get_current_mode", config.MODE_OLLAMA_LOCAL) or "")
        if mode == config.MODE_API_ENDPOINT:
            provider = str(_safe_call(settings, "get_api_provider", "Cloud API") or "Cloud API")
            models = _safe_call(settings, "get_api_models", {}, provider) or {}
            model_id = str(models.get(config.TASK_CHAT) or "") if isinstance(models, dict) else ""
            return {
                "mode": "cloud",
                "provider": provider,
                "modelId": model_id,
                "label": f"Cloud · {provider}",
                "available": bool(provider),
                "canChange": False,
            }
        if mode == config.MODE_LLAMACPP_LOCAL:
            model_path = str(_safe_call(settings, "get_llama_cpp_chat_model_path", "") or "")
            model_id = os.path.basename(model_path) if model_path else ""
            return {
                "mode": "llamacpp",
                "provider": "llama.cpp",
                "modelId": model_id,
                "label": "Local · llama.cpp",
                "available": bool(model_path),
                "canChange": False,
            }

        model_id = str(_safe_call(settings, "get_ollama_chat_model", "") or "")
        if not model_id:
            scanned = _safe_call(settings, "get_ollama_scanned_models", []) or []
            model_id = str(scanned[0]) if scanned else ""
        return {
            "mode": "ollama",
            "provider": "Ollama",
            "modelId": model_id,
            "label": "Local · Ollama",
            "available": True,
            "canChange": False,
        }

    def _theme(self) -> dict[str, str]:
        try:
            palette = get_current_palette()
            accent = palette.SELECTION.name()
        except (AttributeError, TypeError):
            accent = "#83a7ff"
        return {"mode": "dark", "accent": accent, "surface": "#1b1f25"}

    def _state_payload(self) -> dict[str, Any]:
        draft = self.controller.draft
        anchor = self._context_anchor()
        items = self._context_items()
        context = {
            "anchor": anchor,
            "items": items,
            "totalTokens": sum(item["tokenCount"] for item in items),
            "reviewAvailable": bool(anchor or items),
        }
        request_state = getattr(self.controller.state, "value", str(self.controller.state))
        request_state = str(request_state)
        # The desktop request path rejects an empty prompt with only a graph
        # anchor. Attachments are valid input; an anchor alone is reviewable
        # context, not a sendable request.
        has_input = bool(str(draft.text or "").strip() or items)
        return {
            "schemaVersion": 1,
            "revision": self._revision,
            "draft": {
                "id": draft.draft_id,
                "text": draft.text,
                "contextMode": draft.context_mode,
                "sendMode": draft.send_mode,
                "restored": bool(draft.restored),
            },
            "context": context,
            "route": self._route(),
            "request": {
                "id": self.controller.active_request_id,
                "state": request_state,
                "message": self.controller.state_message,
                "canSend": request_state not in _ACTIVE_STATES and has_input,
                "canCancel": request_state in _ACTIVE_STATES,
                "canRetry": request_state == "failed",
            },
            "capabilities": {
                "attachments": True,
                "contextReview": True,
                "routeSelection": False,
                "cancellation": True,
            },
            "theme": self._theme(),
        }

    def _publish(self):
        self._revision += 1
        payload = self._state_payload()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.stateChanged.emit(serialized)
        self.themeChanged.emit(json.dumps(payload["theme"], sort_keys=True))
