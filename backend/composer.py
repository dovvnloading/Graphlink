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

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import api_provider

from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.attachments import AttachmentError, StagedAttachment, stage_file
from backend.settings import (
    _apply,
    apply_llama_cpp_reasoning_mode,
    apply_ollama_chat_model,
    apply_ollama_reasoning_mode,
)
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
    # The FALLBACK reasoning level, used only when no persisted-settings
    # reader has been wired (a bare ComposerDocument() in a unit test).
    # Whenever register_composer got a real SettingsManager, the persisted
    # value wins - see reasoning_level_reader below.
    reasoning_level: str = DEFAULT_REASONING_LEVEL
    # R7.5d follow-up: reads the ACTIVE provider's persisted reasoning mode
    # ("Thinking"/"Quick"). Legacy's composer never stored a reasoning level
    # of its own either - graphlink_composer_bridge.py's _reasoning() derives
    # it from the settings manager on every payload build, which is why the
    # Qt composer and the Settings dialog could never disagree.
    #
    # The first pass of R7.5d wired only the WRITE half of that (the toggle
    # began genuinely persisting + re-applying to api_provider) and left this
    # document's own private reasoning_level as the display source. That made
    # the two halves diverge in a way that was worse than the original bug:
    # get_ollama_reasoning_mode() defaults to "Thinking", so a user who had
    # never touched the setting saw the composer confidently report "Quick
    # Mode (No CoT)" while every chat call really ran with think=True, and
    # selecting the already-active "Quick" was the only way to reach the
    # state the UI already claimed. Deriving instead of mirroring removes the
    # second source of truth rather than trying to keep two in sync.
    reasoning_level_reader: Callable[[], str] | None = field(default=None, repr=False)
    route_reader: Callable[[], dict[str, Any]] | None = field(default=None, repr=False)
    # R4: the in-flight agent-dispatch request, if any - set by
    # backend/agents.py's AgentDispatcher around a real chat call so the
    # composer UI can reflect generating/idle state and offer cancellation.
    request_id: str | None = None
    request_state: str = "idle"
    # R8a: attachments staged for the NEXT send. Lives only in backend
    # memory - the frontend only ever sees StagedAttachment.to_wire()'s
    # metadata (see payload() below), never the raw bytes/extracted text.
    # Cleared by take_staged_attachments() at Send time (backend/canvas.py's
    # send_message intent - see register_canvas's own docstring on why
    # composer_document is threaded there).
    staged_attachments: list[StagedAttachment] = field(default_factory=list)

    def take_staged_attachments(self) -> list[StagedAttachment]:
        """Pop and clear every staged attachment - called exactly once per
        Send. A read-then-clear helper rather than two separate calls so
        canvas.py's send_message can't read the list, get interrupted, and
        clear a DIFFERENT (newer) list than the one it just read."""
        items, self.staged_attachments = self.staged_attachments, []
        return items

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

    def effective_reasoning_level(self) -> str:
        """The reasoning level to DISPLAY: the active provider's persisted
        setting when one is reachable, else this document's own fallback.

        Normalizes the settings manager's Title-Case vocabulary
        ("Thinking"/"Quick") to this payload's lowercase option ids, which
        are the two the frontend renders and sends back.
        """
        if self.reasoning_level_reader is None:
            return self.reasoning_level
        raw = str(self.reasoning_level_reader() or "").strip().lower()
        return "thinking" if raw == "thinking" else "quick"

    def _reasoning_label(self, level: str) -> str:
        return next(o["label"] for o in REASONING_OPTIONS if o["id"] == level)

    def route(self) -> dict[str, Any]:
        """The REAL provider/model route (R8a).

        This used to be five hardcoded literals - provider "Ollama (Local)",
        empty modelId/modelLabel, an empty modelOptions list, and
        canChange/modelSelection False. The composer therefore rendered a
        control that looked like a model dropdown, was permanently greyed out,
        and had nothing behind it. The user reported it as "does not work at
        all and is locked up", which is exactly right: a visible dead control
        reads as a bug, not as a documented deferral.

        It now reports what the app is actually configured to do, resolved
        through the same path api_provider uses at call time
        (graphlink_task_config.sync_ollama_task_models ->
         graphlink_model_catalog.resolve_task_model), so what the composer
        shows and what a send actually uses cannot drift apart.
        """
        if self.route_reader is None:
            # No settings manager wired (the 2-positional-arg test shape).
            # Report honestly rather than claiming a provider we cannot check.
            return {
                "mode": "ollama",
                "provider": "Ollama (Local)",
                "modelId": "",
                "modelLabel": "",
                "modelOptions": [],
                "label": "Ollama (Local)",
                "available": True,
                "canChange": False,
            }
        return self.route_reader()

    def payload(self) -> dict[str, Any]:
        reasoning_level = self.effective_reasoning_level()
        route = self.route()
        return {
            "draft": {
                "id": self.draft.id,
                "text": self.draft.text,
                "contextMode": self.draft.context_mode,
                "sendMode": self.draft.send_mode,
                "restored": self.draft.restored,
            },
            "context": {
                # R8a: was 4 hardcoded literals, unconditionally, on every
                # publish - now the REAL staged-attachment list. "anchor"
                # stays None: it names a branch-context anchor node, a
                # separate and still-deferred concept from attachments.
                "anchor": None,
                "items": [item.to_wire() for item in self.staged_attachments],
                "totalTokens": sum(item.token_count for item in self.staged_attachments),
                "reviewAvailable": bool(self.staged_attachments),
            },
            "route": {
                **route,
                "reasoning": {
                    "level": reasoning_level,
                    "label": self._reasoning_label(reasoning_level),
                    "options": list(REASONING_OPTIONS),
                },
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
                # R8a: real now - staging goes through native_dialogs.pick_file,
                # which gracefully returns None with no window (bare uvicorn/
                # pytest), so this stays True unconditionally rather than
                # trying to detect window availability up front.
                "attachments": True,
                "contextReview": False,
                "routeSelection": False,
                # Real, and derived - not a constant. The composer's model
                # control enables exactly when there is something to choose.
                "modelSelection": bool(route.get("modelOptions")),
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
    if settings_manager is not None:
        # Derive the displayed level from the SAME persisted setting the
        # Settings dialog edits and api_provider actually obeys, exactly as
        # legacy's _reasoning() did (graphlink_composer_bridge.py:464-474),
        # branching on the live provider for the same reason it did.
        def _persisted_reasoning_mode() -> str:
            if api_provider.is_local_llama_cpp_mode():
                return settings_manager.get_llama_cpp_reasoning_mode()
            return settings_manager.get_ollama_reasoning_mode()

        document.reasoning_level_reader = _persisted_reasoning_mode

        def _live_route() -> dict[str, Any]:
            """Resolve the route the way a real send resolves it.

            Deliberately goes through graphlink_task_config.sync_ollama_task_models
            rather than reading assignments directly: that is the function
            api_provider itself calls, so the model this reports is by
            construction the model a send would use. Reading the raw
            assignment dict instead would re-implement Auto/inherit
            resolution here and could disagree with the real call path -
            which is precisely the class of bug that made the composer
            display "Quick Mode" while running CoT.
            """
            import graphlink_task_config as task_config

            if api_provider.is_api_mode():
                provider = settings_manager.get_api_provider() or "API Endpoint"
                assigned = settings_manager.get_api_models() or {}
                model_id = assigned.get(task_config.TASK_CHAT, "") or ""
                catalog = settings_manager.get_api_model_catalog(provider) or []
                options = [
                    {"id": str(m.get("id") or m), "label": str(m.get("label") or m.get("id") or m)}
                    for m in catalog
                ]
                return {
                    "mode": "api",
                    "provider": provider,
                    "modelId": model_id,
                    "modelLabel": model_id or "Select a model",
                    "modelOptions": options,
                    "label": provider,
                    "available": True,
                    "canChange": bool(options),
                }

            if api_provider.is_local_llama_cpp_mode():
                path = settings_manager.get_llama_cpp_chat_model_path() or ""
                name = path.replace("\\", "/").rsplit("/", 1)[-1]
                return {
                    "mode": "llama_cpp",
                    "provider": "Llama.cpp (Local)",
                    "modelId": path,
                    "modelLabel": name or "Select a model",
                    # A GGUF is chosen by file path in Settings, not from a
                    # short list - offering a dropdown here would be a worse
                    # affordance than the real file picker that already exists.
                    "modelOptions": [],
                    "label": "Llama.cpp (Local)",
                    "available": bool(path),
                    "canChange": False,
                }

            task_config.sync_ollama_task_models(settings_manager)
            model_id = task_config.OLLAMA_MODELS.get(task_config.TASK_CHAT, "") or ""
            scanned = settings_manager.get_ollama_scanned_models() or []
            return {
                "mode": "ollama",
                "provider": "Ollama (Local)",
                "modelId": model_id,
                "modelLabel": model_id or "Select a model",
                "modelOptions": [{"id": m, "label": m} for m in scanned],
                "label": "Ollama (Local)",
                "available": bool(model_id),
                "canChange": bool(scanned),
            }

        document.route_reader = _live_route
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

            # The Settings dialog renders the same persisted value on its
            # Ollama/Llama.cpp page. Without this it keeps showing the old
            # one until something else republishes, which is the mirror
            # image of the stale-composer bug this follow-up exists to fix.
            if bus.has_topic("app-settings"):
                await bus.publish("app-settings")

        await publish()

    async def select_model(model_id):
        """Assign the chat-task model from the composer (R8a).

        Writes through the SAME per-task assignment the Settings > Ollama page
        edits, so the two surfaces cannot disagree, and republishes both.
        """
        if settings_manager is None:
            return
        chosen = str(model_id or "").strip()
        if not chosen:
            return
        import graphlink_task_config as task_config

        if api_provider.is_api_mode():
            models = dict(settings_manager.get_api_models() or {})
            models[task_config.TASK_CHAT] = chosen
            _apply(settings_manager.set_api_models, models)
        elif api_provider.is_local_ollama_mode():
            # Shared with the Settings > Ollama page - one implementation,
            # so the two surfaces cannot disagree about what is assigned.
            await apply_ollama_chat_model(settings_manager, chosen)
        else:
            return

        if bus.has_topic("app-settings"):
            await bus.publish("app-settings")
        await publish()

    async def attach_file():
        """Open a native OPEN file dialog and stage whatever is picked (R8a).

        The one genuinely real capability gap: staging needs an actual
        on-disk path (backend/attachments.py reads real bytes/extracts real
        text), so this is a NATIVE dialog - the same native_dialogs.pick_file
        used by Settings > Llama.cpp's GGUF picker (R7.4c), not a browser
        <input type="file"> (which never gives a path, only bytes already in
        the browser - useless here since classification/extraction runs
        server-side). Cancelling (path is None) is a quiet no-op, matching
        every other picker in this codebase.
        """
        from backend import native_dialogs

        path = await native_dialogs.pick_file(
            file_types=(
                "Common Attachments (*.png *.jpg *.jpeg *.webp *.mp3 *.wav *.m4a "
                "*.flac *.ogg *.opus *.aac *.mp4 *.mpeg *.mpga *.oga *.webm *.pdf "
                "*.docx *.txt *.md *.py *.js *.ts *.json *.csv *.log)",
                "Image Files (*.png *.jpg *.jpeg *.webp)",
                "All Files (*.*)",
            )
        )
        if not path:
            return
        try:
            staged = stage_file(path)
        except AttachmentError as exc:
            if notifications is not None:
                notifications.show(str(exc), "warning")
                await bus.publish("notification")
            return
        document.staged_attachments.append(staged)
        await publish()

    async def remove_attachment(attachment_id):
        attachment_id = str(attachment_id)
        document.staged_attachments = [
            item for item in document.staged_attachments if item.id != attachment_id
        ]
        await publish()

    bus.register_intent("app-composer", "attachFile", attach_file)
    bus.register_intent("app-composer", "removeAttachment", remove_attachment)
    bus.register_intent("app-composer", "selectModel", select_model)
    bus.register_intent("app-composer", "updateDraft", update_draft)
    bus.register_intent("app-composer", "setReasoningLevel", set_reasoning_level)

    return document
