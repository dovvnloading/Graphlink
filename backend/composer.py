"""Composer domain state for the new architecture (Qt-removal plan R2).

Unlike GridViewSettings/NavigationPinStore (R1), the legacy composer domain
model is NOT reusable here: graphlink_composer.py and graphlink_styles.py
both import PySide6 at module scope, so importing either into backend/ would
silently reintroduce a hard Qt dependency - invisible to
test_no_qt_anywhere.py's zero-tolerance rule, which only scans the importing
file's own source, not its transitive import graph. This module is therefore
an independent, Qt-free reimplementation of the WIRE shape
ComposerStatePayload already documents, not a port of the Qt controller.

Scope for R2 (chrome consolidation - "port the React code, wire @Slot
intents to backend handlers"): draft text editing and reasoning-level
selection are genuinely real here, since neither needs an LLM call. Message
SEND, attachments, and model/provider selection are explicitly deferred -
send needs the agent layer (R4), attachments need the file-staging pipeline
(also naturally an R4 concern, since attachments only matter once something
can consume them), and provider/model selection needs real provider wiring
(R4). Every deferred capability is surfaced as false in `capabilities`, not
silently faked - the same explicit-defer pattern the R2.2 app bar already
established for Save/provider-select.

Theme is deliberately NOT part of this payload, unlike the legacy island:
the old per-island QWebEngineView each needed its own live-pushed
`:root { --gl-*: ... }` block. The SPA is one document that already loads
real generated token CSS globally (web_ui/src/lib/tokens/gl-vars-dev.css,
carved out for src/app in test_gl_vars_dev_css.py) - shipping the same
values a second time through this payload would just be redundant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import api_provider

from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.settings import apply_llama_cpp_reasoning_mode, apply_ollama_reasoning_mode
from backend.token_counter import TokenCounterState
from graphlink_licensing import SettingsManager

REASONING_OPTIONS = [
    {"id": "thinking", "label": "Thinking Mode (Enable CoT)", "description": "Slower, higher-quality reasoning."},
    {"id": "quick", "label": "Quick Mode (No CoT)", "description": "Faster, direct answers."},
]
DEFAULT_REASONING_LEVEL = "quick"

SEND_MODES = ("enter_to_send", "ctrl_enter_to_send")


class ComposerError(ValueError):
    """A composer intent referenced an invalid value."""


@dataclass
class ComposerDraft:
    id: str = field(default_factory=lambda: uuid4().hex)
    text: str = ""
    context_mode: str = "branch"
    send_mode: str = "enter_to_send"
    restored: bool = False


@dataclass
class ComposerDocument:
    """The composer's state for one session."""

    draft: ComposerDraft = field(default_factory=ComposerDraft)
    reasoning_level: str = DEFAULT_REASONING_LEVEL
    # R4: the in-flight agent-dispatch request, if any - set by
    # backend/agents.py's AgentDispatcher around a real chat call so the
    # composer UI can reflect generating/idle state and offer cancellation.
    request_id: str | None = None
    request_state: str = "idle"

    def update_draft_text(self, text: str) -> None:
        self.draft.text = str(text)

    def set_reasoning_level(self, level: str) -> None:
        valid_ids = {option["id"] for option in REASONING_OPTIONS}
        if level not in valid_ids:
            raise ComposerError(f"unknown reasoning level: {level}")
        self.reasoning_level = level

    def begin_request(self, request_id: str) -> None:
        self.request_id = request_id
        self.request_state = "generating"

    def end_request(self) -> None:
        self.request_id = None
        self.request_state = "idle"

    def _reasoning_label(self) -> str:
        return next(o["label"] for o in REASONING_OPTIONS if o["id"] == self.reasoning_level)

    def payload(self) -> dict[str, Any]:
        return {
            "draft": {
                "id": self.draft.id,
                "text": self.draft.text,
                "contextMode": self.draft.context_mode,
                "sendMode": self.draft.send_mode,
                "restored": self.draft.restored,
            },
            "context": {
                "anchor": None,
                "items": [],
                "totalTokens": 0,
                "reviewAvailable": False,
            },
            "route": {
                "mode": "ollama",
                "provider": "Ollama (Local)",
                "modelId": "",
                "modelLabel": "",
                "modelOptions": [],
                "reasoning": {
                    "level": self.reasoning_level,
                    "label": self._reasoning_label(),
                    "options": list(REASONING_OPTIONS),
                },
                "label": "Ollama (Local)",
                "available": True,
                "canChange": False,
            },
            "request": {
                "id": self.request_id,
                "state": self.request_state,
                "message": "",
                "canSend": self.request_state == "idle",
                "canCancel": self.request_state == "generating",
                "canRetry": False,
            },
            "capabilities": {
                "attachments": False,
                "contextReview": False,
                "routeSelection": False,
                "modelSelection": False,
                "reasoningSelection": api_provider.is_local_ollama_mode() or api_provider.is_local_llama_cpp_mode(),
                "settingsShortcut": True,
                "cancellation": True,
            },
        }


def register_composer(
    bus: SessionBus,
    token_counter: TokenCounterState,
    settings_manager: SettingsManager | None = None,
    notifications: NotificationState | None = None,
) -> ComposerDocument:
    # settings_manager/notifications are optional only so the 2-positional-
    # arg call in backend/tests/test_backend_composer.py's make_bus()
    # (register_composer(bus, counter)) keeps working unchanged - the real
    # (and only) production call site, backend/app.py's _configure_session,
    # always passes both (mirroring register_settings's own `notifications`
    # optional-param precedent exactly).
    document = ComposerDocument()
    bus.register_topic("app-composer", document.payload)

    async def publish():
        await bus.publish("app-composer")

    async def update_draft(text):
        document.update_draft_text(text)
        # The only real input that exists pre-R3/R4: keep the counter live.
        token_counter.set_input_text(text)
        await publish()
        await bus.publish("token-counter")

    async def set_reasoning_level(level):
        # Busy-guard, matching legacy's setReasoningLevel exactly
        # (graphlink_composer_bridge.py, ~line 249): a bare no-op - no
        # exception, no publish - while a request is in flight. This
        # document only ever has two request_state values
        # ("idle"/"generating"), already exposed on the wire as
        # request.canSend == (request_state == "idle") - reuse that existing
        # fact rather than adding a second field for it.
        if document.request_state != "idle":
            return

        # Unchanged existing behavior: raises ComposerError for an
        # unrecognized id (see
        # test_set_reasoning_level_intent_updates_and_rejects_unknown).
        document.set_reasoning_level(level)

        # Same Title-Case normalization backend/settings.py's own
        # _OLLAMA_REASONING_MODES/_LLAMA_CPP_REASONING_MODES validate
        # against, and legacy's own bridge applied. This ternary is TOTAL -
        # it always produces "Thinking" or "Quick" - so the shared apply
        # functions below never need to re-validate whatever this handler
        # passes them.
        normalized_mode = "Thinking" if str(level).strip().lower() == "thinking" else "Quick"

        if settings_manager is not None:
            # Mutually exclusive by construction (api_provider.py) - order
            # between the two branches is arbitrary; Ollama-first here
            # purely to match backend/settings.py's own file ordering
            # (Ollama defined before Llama.cpp throughout that file).
            if api_provider.is_local_ollama_mode():
                await apply_ollama_reasoning_mode(settings_manager, normalized_mode)
            elif api_provider.is_local_llama_cpp_mode():
                failure = await apply_llama_cpp_reasoning_mode(settings_manager, normalized_mode)
                if failure is not None and notifications is not None:
                    notifications.show(failure, "error")
                    await bus.publish("notification")
            # else: cloud/API mode - reasoningSelection is already False
            # there (see payload() above), so the UI disables this control
            # entirely; skip the apply step, never raise.

        await publish()

    bus.register_intent("app-composer", "updateDraft", update_draft)
    bus.register_intent("app-composer", "setReasoningLevel", set_reasoning_level)

    return document
