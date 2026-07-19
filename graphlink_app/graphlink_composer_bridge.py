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
from graphlink_config import (
    get_current_palette,
    get_graph_node_colors,
    get_neutral_button_colors,
    get_semantic_color,
)
from graphlink_island_bridge import IslandBridge
from graphlink_styles import THEME_TOKENS, css_custom_properties


_MAX_DRAFT_CHARS = 100_000
_ACTIVE_STATES = frozenset({"preparing", "uploading", "waiting", "generating", "finalizing"})
COMPOSER_MIN_HEIGHT = 92
COMPOSER_MAX_HEIGHT = 420


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


def _model_option(
    model_id: Any,
    *,
    provider: str,
    source: str,
    active: bool = False,
    ready: bool = True,
    available: bool = True,
    label: Any = None,
    capabilities: Any = None,
) -> dict[str, Any]:
    normalized_id = str(model_id or "").strip()
    return {
        "id": normalized_id,
        "label": _clean_label(label or normalized_id, normalized_id or "Model", 100),
        "provider": str(provider or ""),
        "source": str(source or "configured"),
        "active": bool(active),
        "ready": bool(ready),
        "available": bool(available),
        "capabilities": sorted({str(item).strip() for item in (capabilities or []) if str(item).strip()}),
    }


class ComposerBridge(IslandBridge, QObject):
    """QWebChannel object with a stable, versioned state contract.

    State/lifecycle (publish/dispose, schemaVersion, revision) come from
    IslandBridge and are transport-agnostic; this class supplies the composer's
    own state payload plus the Qt-specific wiring QWebChannel requires
    (Signals for outbound state, Slots for inbound intents).

    IslandBridge only abstracts the outbound *state* channel (publish() ->
    _transport_send()). It intentionally does not cover: inbound intent
    dispatch (the @Slot methods below are 100% QWebChannel-shaped - a
    non-Qt transport would expose the same plain methods with no decorator,
    which is harmless, since the decorators are additive Qt metadata) or any
    Qt Signal emitted directly from inside an intent handler rather than
    through publish(). heightRequested is the one case of the latter with a
    real consumer (ComposerWebHost.resize -> _apply_requested_height); it is
    a second, Python-to-Python (not Python-to-JS) Qt-only channel a future
    non-Qt host will need its own solution for (e.g. CSS/ResizeObserver-based
    auto-sizing), not something IslandBridge should grow to cover. draftChanged
    and contextReviewChanged below are unconsumed today (nothing connects to
    them) but would have the same problem the moment something does.
    Also out of scope here: ComposerController (self.controller) is itself a
    QObject emitting Qt Signals - the republish *trigger*, not just the
    publish path, still assumes Qt underneath this refactor.
    """

    stateChanged = Signal(str)
    draftChanged = Signal(str)  # unconsumed; see class docstring
    contextReviewChanged = Signal(str)
    streamDelta = Signal(str)
    requestCompleted = Signal(str)
    requestFailed = Signal(str)
    routeChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see class docstring

    def __init__(self, window, controller: ComposerController | None = None, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self.window = window
        self.controller = controller or getattr(window, "composer_controller", None)
        if self.controller is None:
            self.controller = ComposerController(self)
        self._attachment_paths: dict[str, str] = {}
        self._last_height = 0
        self.controller.draftChanged.connect(self._on_draft_changed)
        self.controller.stateChanged.connect(self._on_controller_state_changed)

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _on_dispose(self) -> None:
        try:
            self.controller.draftChanged.disconnect(self._on_draft_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self.controller.stateChanged.disconnect(self._on_controller_state_changed)
        except (RuntimeError, TypeError):
            pass

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def updateDraft(self, text: str):
        normalized = str(text or "")[:_MAX_DRAFT_CHARS]
        self.controller.update_text(normalized)
        self.draftChanged.emit(normalized)
        self.publish()

    @Slot()
    def send(self):
        state = self._build_state_payload()
        if state["request"]["state"] in _ACTIVE_STATES:
            return
        if not state["request"]["canSend"]:
            return
        send_message = getattr(self.window, "send_message", None)
        if callable(send_message):
            send_message()
            self.publish()

    @Slot()
    @Slot(str)
    def cancel(self, request_id: str = ""):
        if request_id and request_id != (self.controller.active_request_id or ""):
            return
        callback = getattr(self.window, "_main_request_cancel_callback", None)
        if callable(callback):
            callback()
            self.publish()
            return
        self.controller.cancel(request_id or None)
        self.publish()

    @Slot()
    def reviewContext(self):
        context = self._build_state_payload()["context"]
        open_context = getattr(self.window, "open_composer_context_popup", None)
        if callable(open_context):
            open_context(context)
            return
        self.contextReviewChanged.emit(json.dumps(context, sort_keys=True))

    @Slot()
    def requestAttachment(self):
        attach_file = getattr(self.window, "attach_file", None)
        if callable(attach_file):
            attach_file()

    @Slot(str)
    def stageTextAttachment(self, text: str):
        """Turn a large pasted text payload into a native context attachment."""
        stage_paste = getattr(self.window, "_handle_large_paste_from_input", None)
        if callable(stage_paste):
            stage_paste(str(text or ""))
            self.publish()

    @Slot(str)
    def removeContextItem(self, item_id: str):
        path = self._attachment_paths.get(str(item_id or ""))
        remove = getattr(self.window, "_handle_attachment_pill_removed", None)
        if path and callable(remove):
            remove(path)
            self.publish()

    @Slot(str)
    def selectModel(self, model_id: str):
        """Persist and activate the chat model selected in the composer."""
        if self._build_state_payload()["request"]["state"] in _ACTIVE_STATES:
            return
        model_id = str(model_id or "").strip()
        if not model_id:
            return

        settings = getattr(self.window, "settings_manager", None)
        mode = str(_safe_call(settings, "get_current_mode", config.MODE_OLLAMA_LOCAL) or "")
        try:
            import api_provider

            if mode == config.MODE_API_ENDPOINT:
                provider = str(_safe_call(settings, "get_api_provider", "") or "")
                models = dict(_safe_call(settings, "get_api_models", {}, provider) or {})
                models[config.TASK_CHAT] = model_id
                _safe_call(settings, "set_api_models", None, models, provider)
                api_provider.set_task_model(config.TASK_CHAT, model_id)
            elif mode == config.MODE_LLAMACPP_LOCAL:
                _safe_call(settings, "set_llama_cpp_chat_model_path", None, model_id)
                api_provider.initialize_local_provider(
                    config.LOCAL_PROVIDER_LLAMACPP,
                    _safe_call(settings, "get_llama_cpp_settings", {}),
                    preload_model=False,
                )
            else:
                _safe_call(settings, "set_ollama_chat_model", None, model_id)
                config.sync_ollama_task_models(settings)
                api_provider.set_ollama_reasoning_mode(
                    _safe_call(settings, "get_ollama_reasoning_mode", "Thinking")
                )

            self._notify_settings_changed()
            self.publish()
        except Exception as exc:
            self._show_configuration_error(f"Model selection failed: {exc}")

    @Slot(str)
    def setReasoningLevel(self, level: str):
        """Persist and activate the composer reasoning level."""
        if self._build_state_payload()["request"]["state"] in _ACTIVE_STATES:
            return

        normalized = "Thinking" if str(level or "").strip().lower() == "thinking" else "Quick"
        settings = getattr(self.window, "settings_manager", None)
        mode = str(_safe_call(settings, "get_current_mode", config.MODE_OLLAMA_LOCAL) or "")
        try:
            import api_provider

            if mode == config.MODE_LLAMACPP_LOCAL:
                _safe_call(settings, "set_llama_cpp_reasoning_mode", None, normalized)
                api_provider.initialize_local_provider(
                    config.LOCAL_PROVIDER_LLAMACPP,
                    _safe_call(settings, "get_llama_cpp_settings", {}),
                    preload_model=False,
                )
            else:
                _safe_call(settings, "set_ollama_reasoning_mode", None, normalized)
                api_provider.set_ollama_reasoning_mode(normalized)

            self._notify_settings_changed()
            self.publish()
        except Exception as exc:
            self._show_configuration_error(f"Reasoning setting failed: {exc}")

    @Slot()
    def openSettings(self):
        show_settings = getattr(self.window, "show_settings", None)
        if callable(show_settings):
            show_settings()

    @Slot()
    def openModelSelector(self):
        """Open the native picker outside the QWebEngine viewport."""
        show_picker = getattr(self.window, "open_composer_model_picker", None)
        if callable(show_picker):
            show_picker("model")

    @Slot()
    def openReasoningSelector(self):
        """Open the native reasoning picker outside the QWebEngine viewport."""
        show_picker = getattr(self.window, "open_composer_model_picker", None)
        if callable(show_picker):
            show_picker("reasoning")

    def route_snapshot(self) -> dict[str, Any]:
        """Return the current route for native UI owned by the desktop window."""
        return self._route()

    def _notify_settings_changed(self):
        callback = getattr(self.window, "on_settings_changed", None)
        if callable(callback):
            callback()

    def _show_configuration_error(self, message: str):
        banner = getattr(self.window, "notification_banner", None)
        notify = getattr(banner, "show_message", None)
        if callable(notify):
            notify(str(message), 7000, "error")

    @Slot(int)
    def resize(self, height: int):
        bounded = max(COMPOSER_MIN_HEIGHT, min(COMPOSER_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)

    def _on_draft_changed(self, draft):
        self.publish()

    def _on_controller_state_changed(self, state, message):
        self.publish()

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

    def _cloud_model_options(self, settings, provider: str, active_model: str) -> list[dict[str, Any]]:
        options: dict[str, dict[str, Any]] = {}

        def add(model_id, *, source="saved", ready=True, available=True, capabilities=None):
            normalized = str(model_id or "").strip()
            if not normalized:
                return
            key = normalized.lower()
            if key not in options:
                options[key] = _model_option(
                    normalized,
                    provider=provider,
                    source=source,
                    active=normalized == active_model,
                    ready=ready,
                    available=available,
                    capabilities=capabilities,
                )
            elif normalized == active_model:
                options[key]["active"] = True

        for descriptor in _safe_call(settings, "get_api_model_catalog", [], provider) or []:
            if isinstance(descriptor, dict):
                add(
                    descriptor.get("model_id") or descriptor.get("id"),
                    source="catalog",
                    ready=descriptor.get("ready", True),
                    available=descriptor.get("available", True),
                    capabilities=descriptor.get("capabilities", []),
                )
            else:
                add(descriptor, source="catalog")

        saved_models = _safe_call(settings, "get_api_models", {}, provider) or {}
        if isinstance(saved_models, dict):
            for model_id in saved_models.values():
                add(model_id, source="saved")

        if provider == config.API_PROVIDER_GEMINI and not options:
            try:
                import api_provider

                for model_id in api_provider.GEMINI_MODELS_STATIC:
                    add(model_id, source="catalog")
            except (AttributeError, ImportError):
                pass

        add(active_model, source="configured")
        return sorted(
            options.values(),
            key=lambda item: (not item["active"], not item["ready"], item["label"].lower()),
        )

    def _local_model_options(self, settings, provider: str, active_model: str) -> list[dict[str, Any]]:
        options: dict[str, dict[str, Any]] = {}
        if provider == "Ollama":
            scanned_models = _safe_call(settings, "get_ollama_scanned_models", []) or []
            for model_id in scanned_models:
                normalized = str(model_id or "").strip()
                if normalized:
                    options[normalized.lower()] = _model_option(
                        normalized,
                        provider=provider,
                        source="installed",
                        active=normalized == active_model,
                    )
        else:
            scanned_models = _safe_call(settings, "get_llama_cpp_scanned_models", []) or []
            for model_path in scanned_models:
                normalized = str(model_path or "").strip()
                if normalized:
                    options[normalized.lower()] = _model_option(
                        normalized,
                        provider=provider,
                        source="installed",
                        active=normalized == active_model,
                        label=os.path.basename(normalized),
                    )

        if active_model and active_model.lower() not in options:
            options[active_model.lower()] = _model_option(
                active_model,
                provider=provider,
                source="configured",
                active=True,
                ready=False,
                available=True,
                label=os.path.basename(active_model) if provider != "Ollama" else active_model,
            )
        return sorted(
            options.values(),
            key=lambda item: (not item["active"], not item["ready"], item["label"].lower()),
        )

    def _reasoning(self, settings, mode: str) -> dict[str, Any]:
        if mode == config.MODE_API_ENDPOINT:
            return {
                "level": "Provider",
                "label": "Provider managed",
                "options": [],
            }
        if mode == config.MODE_LLAMACPP_LOCAL:
            level = _safe_call(settings, "get_llama_cpp_reasoning_mode", "Thinking")
        else:
            level = _safe_call(settings, "get_ollama_reasoning_mode", "Thinking")
        level = "Thinking" if str(level or "").strip().lower() == "thinking" else "Quick"
        return {
            "level": level,
            "label": level,
            "options": [
                {"id": "Quick", "label": "Quick", "description": "Direct responses with less deliberation."},
                {"id": "Thinking", "label": "Thinking", "description": "More deliberate reasoning for complex requests."},
            ],
        }

    def _route(self) -> dict[str, Any]:
        settings = getattr(self.window, "settings_manager", None)
        mode = str(_safe_call(settings, "get_current_mode", config.MODE_OLLAMA_LOCAL) or "")
        if mode == config.MODE_API_ENDPOINT:
            provider = str(_safe_call(settings, "get_api_provider", "Cloud API") or "Cloud API")
            models = _safe_call(settings, "get_api_models", {}, provider) or {}
            model_id = str(models.get(config.TASK_CHAT) or "") if isinstance(models, dict) else ""
            if not model_id:
                import api_provider

                task_models = _safe_call(api_provider, "get_task_models", {}) or {}
                model_id = str(task_models.get(config.TASK_CHAT) or "") if isinstance(task_models, dict) else ""
            model_options = self._cloud_model_options(settings, provider, model_id)
            model_label = next(
                (item["label"] for item in model_options if item["active"]),
                model_id or "Select a model",
            )
            return {
                "mode": "cloud",
                "provider": provider,
                "modelId": model_id,
                "modelLabel": model_label,
                "modelOptions": model_options,
                "label": f"Cloud · {provider}",
                "available": bool(provider and model_id),
                "canChange": True,
                "reasoning": self._reasoning(settings, mode),
            }
        if mode == config.MODE_LLAMACPP_LOCAL:
            model_path = str(_safe_call(settings, "get_llama_cpp_chat_model_path", "") or "")
            model_options = self._local_model_options(settings, "llama.cpp", model_path)
            model_label = next(
                (item["label"] for item in model_options if item["active"]),
                os.path.basename(model_path) if model_path else "Select a model",
            )
            return {
                "mode": "llamacpp",
                "provider": "llama.cpp",
                "modelId": os.path.basename(model_path) if model_path else "",
                "modelValue": model_path,
                "modelLabel": model_label,
                "modelOptions": model_options,
                "label": "Local · llama.cpp",
                "available": bool(model_path),
                "canChange": True,
                "reasoning": self._reasoning(settings, mode),
            }

        model_id = str(config.OLLAMA_MODELS.get(config.TASK_CHAT) or "")
        if not model_id:
            model_id = str(_safe_call(settings, "get_ollama_chat_model", "") or "")
        if not model_id:
            scanned = _safe_call(settings, "get_ollama_scanned_models", []) or []
            model_id = str(scanned[0]) if scanned else ""
        model_options = self._local_model_options(settings, "Ollama", model_id)
        model_label = next(
            (item["label"] for item in model_options if item["active"]),
            model_id or "Select a model",
        )
        return {
            "mode": "ollama",
            "provider": "Ollama",
            "modelId": model_id,
            "modelLabel": model_label,
            "modelOptions": model_options,
            "label": "Local · Ollama",
            "available": bool(model_id),
            "canChange": True,
            "reasoning": self._reasoning(settings, mode),
        }

    def _theme(self) -> dict[str, Any]:
        """Serialize the full current-theme color set.

        Goes through the same public lookup functions every other color
        consumer in the app uses (get_current_palette/get_semantic_color/
        get_neutral_button_colors/get_graph_node_colors) rather than reading
        graphlink_styles.THEME_TOKENS directly, for two reasons: every value
        comes back through QColor.name(), which guarantees consistent
        lowercase hex regardless of how a theme's literal happened to be
        cased in the source table; and get_graph_node_colors() derives most
        of its keys live from get_neutral_button_colors() rather than storing
        them as independent literals, so reading the table directly here
        would have silently missed that relationship. THEME_TOKENS is still
        used for one thing only: the semantic "default" fallback value, which
        is a table-only concept get_semantic_color() doesn't expose as a
        queryable role by name.

        Replaces the old {mode, accent, surface} shape, whose "surface" value
        was a hardcoded literal never actually derived from the active theme.
        `cssVariables` (added when composer's own CSS first started consuming
        `var(--gl-*)`) is the one field here nothing on the JS side ignores
        anymore - see ComposerApp.tsx's theme-application effect.

        KNOWN, UNADDRESSED: `palette`/`semantic`/`neutralButton`/`graphNode`
        below remain genuinely dead on the JS side (confirmed by adversarial
        review: grepping all of web_ui/src for `state.theme.` finds only the
        two `cssVariables` reads) and predate this whole retrofit. Not
        removed here - out of scope for this change, and removal would also
        need touching test_theme_tokens.py's coverage of this shape - but
        flagged explicitly rather than left as silent dead weight, since this
        docstring is the one place that would otherwise let a future reader
        assume all four fields are load-bearing. They also carry a real,
        if-currently-dormant lossiness risk `cssVariables` was deliberately
        built to avoid: every value here round-trips through `QColor.name()`,
        which drops alpha - harmless today only because none of these four
        groups happen to hold an alpha value, not because anything prevents
        one from being added.
        """
        palette = get_current_palette()
        neutral_button = get_neutral_button_colors()
        graph_node = get_graph_node_colors()
        # Same trust-CURRENT_THEME convention get_current_palette() above already
        # has (used by 40+ files, never defensive against an invalid theme name) -
        # apply_theme() is the one place that guarantees CURRENT_THEME is valid.
        default_semantic = THEME_TOKENS[config.CURRENT_THEME]["semantic"]["default"]
        return {
            # Every --gl-* custom property name/value pair for the active
            # theme, straight from css_custom_properties() - the exact
            # function graphlink_web_island_host.py's _inline_bundle() also
            # calls for the build-time :root block, so the runtime and
            # first-paint values can never disagree with each other.
            #
            # Deliberately NOT built from palette/neutral_button/graph_node
            # above: those go through QColor.name(), which silently drops
            # alpha (QColor(r,g,b,a).name() == "#rrggbb", no "aa"), and
            # QColor.name(HexArgb) returns "#AARRGGBB", not CSS's
            # "#RRGGBBAA" - either path would corrupt every composer_alpha
            # rgba() value on its way to JS. css_custom_properties() reads
            # THEME_TOKENS directly as strings and never touches QColor, so
            # this sidesteps that trap structurally rather than by care.
            "cssVariables": css_custom_properties(config.CURRENT_THEME),
            # All three themes are dark-mode variants today; kept as an
            # explicit field for a future light theme, not computed from
            # anything yet.
            "mode": "dark",
            "name": config.CURRENT_THEME,
            "palette": {
                "userNode": palette.USER_NODE.name(),
                "aiNode": palette.AI_NODE.name(),
                "selection": palette.SELECTION.name(),
                "navHighlight": palette.NAV_HIGHLIGHT.name(),
            },
            "semantic": {
                "searchHighlight": get_semantic_color("search_highlight").name(),
                "statusInfo": get_semantic_color("status_info").name(),
                "statusSuccess": get_semantic_color("status_success").name(),
                "statusError": get_semantic_color("status_error").name(),
                "statusWarning": get_semantic_color("status_warning").name(),
                "artifact": get_semantic_color("artifact").name(),
                "conversationUserBubble": get_semantic_color("conversation_user_bubble").name(),
                "conversationAiBubble": get_semantic_color("conversation_ai_bubble").name(),
                "default": default_semantic.lower(),
            },
            "neutralButton": {
                "background": neutral_button["background"].name(),
                "hover": neutral_button["hover"].name(),
                "pressed": neutral_button["pressed"].name(),
                "border": neutral_button["border"].name(),
                "icon": neutral_button["icon"].name(),
                "mutedIcon": neutral_button["muted_icon"].name(),
            },
            "graphNode": {
                "border": graph_node["border"].name(),
                "header": graph_node["header"].name(),
                "dot": graph_node["dot"].name(),
                "hoverDot": graph_node["hover_dot"].name(),
                "hoverOutline": graph_node["hover_outline"].name(),
                "selectedOutline": graph_node["selected_outline"].name(),
                "bodyStart": graph_node["body_start"].name(),
                "bodyEnd": graph_node["body_end"].name(),
                "headerStart": graph_node["header_start"].name(),
                "headerEnd": graph_node["header_end"].name(),
                "badgeFill": graph_node["badge_fill"].name(),
                "panelFill": graph_node["panel_fill"].name(),
                "panelBorder": graph_node["panel_border"].name(),
            },
        }

    def _build_state_payload(self) -> dict[str, Any]:
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
        route = self._route()
        # The desktop request path rejects an empty prompt with only a graph
        # anchor. Attachments are valid input; an anchor alone is reviewable
        # context, not a sendable request.
        has_input = bool(str(draft.text or "").strip() or items)
        # schemaVersion and revision are added by IslandBridge.publish().
        return {
            "draft": {
                "id": draft.draft_id,
                "text": draft.text,
                "contextMode": draft.context_mode,
                "sendMode": draft.send_mode,
                "restored": bool(draft.restored),
            },
            "context": context,
            "route": route,
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
                "routeSelection": True,
                "modelSelection": True,
                "reasoningSelection": route["mode"] != "cloud",
                "settingsShortcut": True,
                "cancellation": True,
            },
            "theme": self._theme(),
        }
