"""FastAPI application: HTTP surface + the /ws WebSocket endpoint
(Qt-removal plan R0).

Serves three things:
- /api/health - liveness + version (the desktop shell polls this at startup)
- /ws?session=<id> - the event-bus WebSocket (state snapshots out, intents in)
- / - the built SPA (static files), when a build directory exists

Client -> server message kinds over /ws:
  {"kind": "subscribe", "topics": ["system", ...]}      -> current snapshots
  {"kind": "intent", "topic": t, "intent": name,
   "args": [...], "id": optional}                        -> optional result
Server -> client:
  {"kind": "state", "topic": t, "payload": {...envelope...}}
  {"kind": "result", "id": ..., "value": ...}            (only when id sent)
  {"kind": "error", "id": ..., "error": "..."}           (bad topic/intent)

R0 registers only the `system` topic (backend identity) and its `ping`
intent - the acceptance round-trip. Real domain topics arrive per-phase
(R1 canvas, R2 chrome, ...), each registering here exactly like system does.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from graphlink_licensing import SettingsManager

from backend import BACKEND_VERSION
from backend.about import register_about
from backend.assets import register_assets
from backend.canvas import register_canvas
from backend.chat_library import register_chat_library
from backend.composer import register_composer
from backend.events import EventBus, SessionBus, UnknownIntentError, UnknownTopicError
from backend.notifications import register_notifications
from backend.plugins import register_plugins
from backend.settings import register_settings
from backend.token_counter import register_token_counter

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SPA_DIST_DIR = REPO_ROOT / "web_ui" / "dist" / "app"


def _configure_session(bus: SessionBus, settings_manager: SettingsManager, chat_db_path: Path | None) -> None:
    """Give every session the R0 topic surface. Later phases extend this
    with canvas/chrome/node topics - one registrar, one place to read the
    whole API surface."""

    bus.register_topic(
        "system",
        lambda: {"app": "graphlink", "backendVersion": BACKEND_VERSION, "sessionId": bus.session_id},
    )

    def ping(*args):
        # The R0 acceptance round-trip: echo + a server-side timestamp so the
        # UI can prove the reply crossed the process boundary.
        return {"echo": list(args), "serverTime": time.time()}

    bus.register_intent("system", "ping", ping)

    # R2: notifications, moved ahead of canvas - R3.3's sendMessage intent
    # needs a real NotificationState to give an honest "lands in R4" notice.
    notifications_state = register_notifications(bus)

    # R1 (doc/QT_REMOVAL_PLAN.md): scene document + grid topics.
    # R3.21: stash the document on its own SessionBus so backend/assets.py's
    # GET /api/assets/{id} route (registered once, globally, on the app) can
    # reach the SAME per-session SceneDocument register_canvas() built here -
    # there was previously no way to get from a session id back to its
    # canvas document outside this closure. SessionBus has no fixed attribute
    # set (no __slots__), so this is a plain, minimal bolt-on attribute, not
    # a SessionBus API change.
    bus.canvas_document = register_canvas(bus, notifications_state)

    # R2: composer draft/reasoning, token counter.
    token_counter = register_token_counter(bus)
    register_composer(bus, token_counter)

    # R2.5: about, plugins, settings, chat library.
    register_about(bus)
    register_plugins(bus, notifications_state)
    register_settings(bus, settings_manager)
    register_chat_library(bus, chat_db_path)


def create_app(
    spa_dir: Path | None = None,
    settings_state_file: Path | None = None,
    chat_db_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Graphlink backend", version=BACKEND_VERSION)
    # ONE SettingsManager for the whole app (it owns a single shared
    # ~/.graphlink/session.dat file), shared across every session rather
    # than reconstructed per-session - see backend/settings.py's docstring.
    settings_manager = SettingsManager(settings_state_file)
    bus = EventBus(
        configure_session=lambda session_bus: _configure_session(session_bus, settings_manager, chat_db_path)
    )
    app.state.bus = bus

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "app": "graphlink", "version": BACKEND_VERSION})

    # R3.21: GET /api/assets/{id} - the image-node byte-serving route (see
    # backend/assets.py's docstring for the transport decision behind it).
    register_assets(app, bus)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        session_id = websocket.query_params.get("session", "default")
        session = bus.session(session_id)
        await websocket.accept()
        session.attach(websocket)
        try:
            while True:
                message = await websocket.receive_json()
                await _handle_message(session, websocket, message)
        except WebSocketDisconnect:
            pass
        finally:
            session.detach(websocket)

    resolved_spa = SPA_DIST_DIR if spa_dir is None else spa_dir
    if resolved_spa.is_dir():
        # html=True serves index.html at / ; the explicit fallback below keeps
        # deep links (client-side routes) working instead of 404ing.
        app.mount("/assets", StaticFiles(directory=resolved_spa / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str) -> FileResponse:
            candidate = (resolved_spa / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(resolved_spa.resolve()):
                return FileResponse(candidate)
            return FileResponse(resolved_spa / "index.html")
    else:
        logger.warning("SPA build not found at %s - only /api and /ws are served", resolved_spa)

    return app


async def _handle_message(session: SessionBus, websocket: WebSocket, message: dict) -> None:
    kind = message.get("kind")
    msg_id = message.get("id")

    if kind == "subscribe":
        topics = message.get("topics") or session.topic_names()
        for topic in topics:
            try:
                await session.send_snapshot(topic, websocket)
            except UnknownTopicError:
                await websocket.send_json(
                    {"kind": "error", "id": msg_id, "error": f"unknown topic: {topic}"}
                )
        return

    if kind == "intent":
        topic = message.get("topic", "")
        intent = message.get("intent", "")
        args = message.get("args") or []
        try:
            result = await session.dispatch_intent(topic, intent, args)
        except (UnknownTopicError, UnknownIntentError) as exc:
            await websocket.send_json({"kind": "error", "id": msg_id, "error": str(exc)})
            return
        except Exception:
            # Handler bugs surface as errors to the caller, never as a dropped
            # socket - and always land in the log.
            logger.exception("intent %s/%s failed", topic, intent)
            await websocket.send_json(
                {"kind": "error", "id": msg_id, "error": f"intent failed: {topic}/{intent}"}
            )
            return
        if msg_id is not None:
            await websocket.send_json({"kind": "result", "id": msg_id, "value": result})
        return

    await websocket.send_json(
        {"kind": "error", "id": msg_id, "error": f"unknown message kind: {kind!r}"}
    )
