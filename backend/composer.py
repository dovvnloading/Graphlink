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

from backend.events import SessionBus
from backend.token_counter import TokenCounterState

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

    def update_draft_text(self, text: str) -> None:
        self.draft.text = str(text)

    def set_reasoning_level(self, level: str) -> None:
        valid_ids = {option["id"] for option in REASONING_OPTIONS}
        if level not in valid_ids:
            raise ComposerError(f"unknown reasoning level: {level}")
        self.reasoning_level = level

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
                "id": None,
                "state": "idle",
                "message": "",
                "canSend": False,
                "canCancel": False,
                "canRetry": False,
            },
            "capabilities": {
                "attachments": False,
                "contextReview": False,
                "routeSelection": False,
                "modelSelection": False,
                "reasoningSelection": True,
                "settingsShortcut": True,
                "cancellation": False,
            },
        }


def register_composer(bus: SessionBus, token_counter: TokenCounterState) -> ComposerDocument:
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
        document.set_reasoning_level(level)
        await publish()

    bus.register_intent("app-composer", "updateDraft", update_draft)
    bus.register_intent("app-composer", "setReasoningLevel", set_reasoning_level)

    return document
