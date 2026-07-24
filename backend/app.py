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
import os
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from graphlink_licensing import SettingsManager

from backend import BACKEND_VERSION
from backend.about import register_about
from backend.agents import bootstrap_provider_state, register_agents
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

# Loopback host Graphlink always binds to (graphlink_desktop.py hardcodes
# host="127.0.0.1"). Required, not just preferred, for the same-origin check
# below: comparing Origin's host against the request's own Host header alone
# is a self-consistency check on two client-supplied values, not a check
# against anything the server independently knows to be correct - a DNS-
# rebinding attacker's page has both its Origin AND the Host header it sends
# echo the SAME attacker-controlled hostname, so an Origin==Host comparison
# alone would accept it. Requiring the host part to literally be 127.0.0.1
# closes that: no attacker-controlled hostname is ever literally this string.
_LOOPBACK_HOST = "127.0.0.1"


def _is_allowed_ws_origin(origin: str | None, host_header: str | None, dev_proxy_origin: str | None = None) -> bool:
    """Handshake-time allowlist for the /ws WebSocket Origin header.

    Defends against cross-site WebSocket hijacking ("localhost service
    takeover"): browsers do not enforce same-origin policy on WebSocket
    connects the way they do fetch/XHR, so any page open in the user's
    regular browser - malicious or compromised, or a bad ad iframe - can
    already open a socket to ws://127.0.0.1:<port>/ws. The Origin header is
    the standard mitigation because page JS cannot set or suppress it (it is
    a forbidden header name); this function is the exact accept/reject
    decision made from it, once, at handshake time.

    Policy (exact string equality only - never substring/startswith, which
    would reopen bypasses like "http://127.0.0.1:5173.evil.com"):

    - origin is None or "" -> True. A real browser always sends Origin for a
      page-script-initiated connect, so an absent Origin cannot be that
      attack; it can only be a non-browser caller (tests, curl, local
      tooling) - a different threat model, since anything already able to
      speak raw WebSocket to this loopback-only port could set Origin to
      any string it likes anyway (only browsers are forbidden from spoofing
      it). Rejecting "absent" would stop zero real attacks.
    - origin == "null" -> False. Only produced by opaque-origin contexts
      (sandboxed iframe without allow-same-origin, data:/file: pages) - all
      attacker-constructed, never a legitimate caller of this app.
    - origin == f"http://{host_header}" AND host_header's host part is
      literally "127.0.0.1" -> True. The normal packaged-app case (pywebview
      window and its backend are same-origin), computed per-request (never
      hardcoded - the port is a dynamically OS-assigned free port; see
      graphlink_desktop.py's _free_port()) - the added 127.0.0.1 requirement
      is what actually defeats DNS rebinding, see _LOOPBACK_HOST's comment.
    - dev_proxy_origin is not None AND origin == dev_proxy_origin -> True.
      Deliberately NOT a hardcoded constant this function trusts on its own:
      the real desktop app (graphlink_desktop.py) never passes one, so this
      branch is dead in the shipped product by construction, not just by
      convention - only ws_endpoint's own caller, reading an opt-in env var
      that is unset in normal operation, can ever supply a non-None value
      here (see GRAPHLINK_DEV_WS_ORIGIN at the call site). Without this, the
      previous version of this function hardcoded Vite's default dev port
      (5173) as an always-trusted origin - correct for the real vite-proxy
      dev workflow, but wrong to trust unconditionally in the shipped app,
      since 5173 is an extremely common default port for unrelated Vite
      projects a user could have running in the same browser.
    - anything else present -> False.
    """
    if origin is None or origin == "":
        return True
    if origin == "null":
        return False
    if host_header:
        host_only = host_header.rsplit(":", 1)[0]
        if host_only == _LOOPBACK_HOST and origin == f"http://{host_header}":
            return True
    if dev_proxy_origin and origin == dev_proxy_origin:
        return True
    return False


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
    # needs a real NotificationState to give an honest agent-dispatch notice.
    notifications_state = register_notifications(bus)

    # R2: composer draft/reasoning, token counter. Moved ahead of canvas (R4):
    # sendMessage's real agent dispatch needs a real ComposerDocument to flip
    # into/out of "generating" state, and a real AgentDispatcher to hand off
    # to - both must exist before register_canvas builds the sendMessage
    # intent that calls them.
    token_counter = register_token_counter(bus)
    composer_document = register_composer(bus, token_counter)

    # R4 (doc/QT_REMOVAL_PLAN.md): the agent-dispatch service - one
    # AgentDispatcher per session (never a module-level singleton). Bolted
    # onto the bus (same pattern as canvas_document below) so ws_endpoint's
    # disconnect handler can reach it and cancel any in-flight request when
    # this session's last connection drops - see AgentDispatcher.cancel_all's
    # own docstring for why that matters.
    agent_dispatcher = register_agents(bus, composer_document, notifications_state, settings_manager)
    bus.agent_dispatcher = agent_dispatcher

    # R1 (doc/QT_REMOVAL_PLAN.md): scene document + grid topics.
    # R3.21: stash the document on its own SessionBus so backend/assets.py's
    # GET /api/assets/{id} route (registered once, globally, on the app) can
    # reach the SAME per-session SceneDocument register_canvas() built here -
    # there was previously no way to get from a session id back to its
    # canvas document outside this closure. SessionBus has no fixed attribute
    # set (no __slots__), so this is a plain, minimal bolt-on attribute, not
    # a SessionBus API change.
    bus.canvas_document = register_canvas(bus, notifications_state, agent_dispatcher, composer_document)

    # R2.5: about, plugins, settings, chat library.
    register_about(bus)
    # R5.1: register_plugins needs the same session's canvas_document (built
    # just above) so "Web Research" can create a real node - this ordering
    # (canvas_document exists before register_plugins runs) is load-bearing.
    register_plugins(bus, notifications_state, bus.canvas_document)
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
    # R4: bootstrap api_provider's module-level provider state from that same
    # SettingsManager exactly ONCE per process - process-global state, not
    # session state (see backend/agents.py's docstring).
    bootstrap_provider_state(settings_manager)
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
        origin = websocket.headers.get("origin")
        host_header = websocket.headers.get("host")
        # Unset in every real launch (graphlink_desktop.py never sets this) -
        # a developer running `npm run dev` (web_ui/vite.config.ts's
        # GRAPHLINK_ISLAND=app target) against a separately-run backend must
        # opt in explicitly by setting this to their vite dev server's real
        # origin (e.g. "http://127.0.0.1:5173"), rather than that origin
        # being trusted unconditionally in the shipped app.
        dev_proxy_origin = os.environ.get("GRAPHLINK_DEV_WS_ORIGIN")
        if not _is_allowed_ws_origin(origin, host_header, dev_proxy_origin):
            logger.warning("rejected WS handshake: origin=%r host=%r", origin, host_header)
            await websocket.close(code=1008)
            return
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
            # Concurrency/security review finding (R4): a client that sends
            # a message then immediately disconnects would otherwise leave
            # the real outbound LLM call running server-side, untethered,
            # for up to WATCHDOG_TIMEOUT_SECONDS with no way to ever cancel
            # it - cancelChatRequest needs a live socket to arrive over.
            # Only cancel once the LAST connection for this session drops:
            # another tab/window on the same session should not lose its
            # in-flight request just because a different tab closed.
            if session.connection_count == 0:
                session.agent_dispatcher.cancel_all()

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
