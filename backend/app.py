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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from graphlink_settings_store import SettingsManager

from backend import BACKEND_VERSION
from backend.about import register_about
from backend.agents import bootstrap_provider_state, register_agents
from backend.assets import register_assets
from backend.auth import (
    AUTH_HEADER,
    AUTH_QUERY_PARAM,
    extract_presented_token,
    is_guarded_path,
    resolve_configured_token,
    token_matches,
)
from backend.canvas import register_canvas
from backend.chat_library import register_chat_library
from backend.composer import register_composer
from backend.crash_recovery import maybe_show_crash_notice
from backend.events import EventBus, SessionBus, UnknownIntentError, UnknownTopicError
from backend.notifications import register_notifications
from backend.plugins import register_plugins
from backend.session_context import SessionContext, attach_session_context, get_session_context
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


def _configure_session(
    bus: SessionBus,
    settings_manager: SettingsManager,
    chat_db_path: Path | None,
    previous_run_crashed: bool = False,
) -> None:
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
    notifications_state = register_notifications(bus, settings_manager)
    # R6.7: a no-op unless graphlink_desktop.py's own running.lock sentinel
    # found the prior run didn't reach a clean shutdown - see
    # backend/crash_recovery.py's module docstring for the full mechanism.
    maybe_show_crash_notice(notifications_state, previous_run_crashed)

    # R2: composer draft/reasoning, token counter. Moved ahead of canvas (R4):
    # sendMessage's real agent dispatch needs a real ComposerDocument to flip
    # into/out of "generating" state, and a real AgentDispatcher to hand off
    # to - both must exist before register_canvas builds the sendMessage
    # intent that calls them.
    token_counter = register_token_counter(bus)
    composer_document = register_composer(bus, token_counter, settings_manager, notifications_state)

    # R4 (doc/QT_REMOVAL_PLAN.md): the agent-dispatch service - one
    # AgentDispatcher per session (never a module-level singleton). Reachable
    # via SessionContext (backend/session_context.py) so ws_endpoint's
    # disconnect handler can reach it and cancel any in-flight request when
    # this session's last connection drops - see AgentDispatcher.cancel_all's
    # own docstring for why that matters.
    agent_dispatcher = register_agents(bus, composer_document, notifications_state, settings_manager)

    # R1 (doc/QT_REMOVAL_PLAN.md): scene document + grid topics.
    # R3.21: the document is reachable via SessionContext so backend/assets.py's
    # GET /api/assets/{id} route (registered once, globally, on the app) can
    # reach the SAME per-session SceneDocument register_canvas() builds here -
    # there was previously no way to get from a session id back to its
    # canvas document outside this closure.
    canvas_document = register_canvas(
        bus, notifications_state, agent_dispatcher, composer_document, token_counter
    )

    # ADR-002 stage 2.1d: ONE typed reference from here on
    # (backend/session_context.py), replacing what used to be two loose
    # dynamic bus attributes (bus.agent_dispatcher/bus.canvas_document) that
    # any OTHER module reading them had no way to know might not exist on a
    # SessionBus built outside this function.
    attach_session_context(
        bus, SessionContext(agent_dispatcher=agent_dispatcher, canvas_document=canvas_document)
    )

    # R2.5: about, plugins, settings, chat library.
    register_about(bus)
    # R5.1: register_plugins needs the same session's canvas_document (built
    # just above) so "Web Research" can create a real node - this ordering
    # (canvas_document exists before register_plugins runs) is load-bearing.
    register_plugins(bus, notifications_state, canvas_document)
    # R7.4a: register_settings now takes notifications_state too, so the
    # API-provider page's save-validation/init-failure paths can surface a
    # real banner (same load-bearing ordering precedent as register_plugins/
    # register_chat_library above - notifications_state already exists by
    # this point in every case).
    register_settings(bus, settings_manager, notifications_state)
    # R6.4: register_chat_library needs the same session's canvas_document
    # (built above) so loadChat can actually restore a session into it, and
    # notifications_state so a failed/empty load can surface a real banner -
    # same load-bearing ordering precedent as register_plugins above.
    register_chat_library(bus, chat_db_path, canvas_document, notifications_state)


def create_app(
    spa_dir: Path | None = None,
    settings_state_file: Path | None = None,
    chat_db_path: Path | None = None,
    previous_run_crashed: bool = False,
    auth_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Graphlink backend", version=BACKEND_VERSION)
    # ADR-004 stage 4.1: the per-launch capability token gating /api/* and
    # /ws - see backend/auth.py's own docstring for the threat (audit C5) and
    # why a token rather than accounts. None means "auth disabled", which is
    # what a bare create_app() in a test gets; the real launch path always
    # passes one (asserted by tests/test_graphlink_desktop.py, so shipping
    # with auth off is a test failure rather than a silent regression).
    resolved_auth_token = resolve_configured_token(auth_token)
    app.state.auth_token = resolved_auth_token
    if resolved_auth_token is None:
        logger.warning(
            "no capability token configured - /api and /ws are UNAUTHENTICATED "
            "(expected only in tests; the desktop shell always mints one)"
        )
    # ONE SettingsManager for the whole app (it owns a single shared
    # ~/.graphlink/session.dat file), shared across every session rather
    # than reconstructed per-session - see backend/settings.py's docstring.
    settings_manager = SettingsManager(settings_state_file)
    # R4: bootstrap api_provider's module-level provider state from that same
    # SettingsManager exactly ONCE per process - process-global state, not
    # session state (see backend/agents.py's docstring).
    bootstrap_provider_state(settings_manager)
    bus = EventBus(
        configure_session=lambda session_bus: _configure_session(
            session_bus, settings_manager, chat_db_path, previous_run_crashed
        )
    )
    app.state.bus = bus

    @app.middleware("http")
    async def require_capability_token(request: Request, call_next):
        """ADR-004 stage 4.1: gate every /api/* request on the capability
        token. Registered as middleware rather than a per-route dependency
        so a future route added under /api/ is covered by construction -
        forgetting a decorator is exactly how this kind of guard rots.

        Deliberately does NOT gate the SPA bootstrap (GET /, /assets/*, the
        client-side-route catch-all): the initial page load cannot carry a
        header, and those routes only serve the public build output. See
        backend/auth.py's docstring.

        Note this never sees WebSocket connections - Starlette's HTTP
        middleware stack only runs for scope["type"] == "http", so /ws is
        guarded separately inside ws_endpoint below. That is a real
        constraint of the framework, not a stylistic split, and it is why
        the two checks cannot be collapsed into one place.
        """
        if resolved_auth_token is not None and is_guarded_path(request.url.path):
            presented = extract_presented_token(
                request.headers.get(AUTH_HEADER),
                request.query_params.get(AUTH_QUERY_PARAM),
            )
            if not token_matches(resolved_auth_token, presented):
                # Uniform 401 with no detail: never distinguish "no token"
                # from "wrong token" from "malformed header", so this is not
                # an oracle for probing the token's shape.
                logger.warning("rejected unauthenticated request: %s", request.url.path)
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        # Gated like every other /api route (ADR-004 §1 says "every /api/*
        # request", and this is deliberately not carved out): the only real
        # caller is graphlink_desktop.py's own startup poll, which minted the
        # token and passes it. Leaving it open would hand any local process a
        # free "is Graphlink running, and what version" fingerprinting oracle
        # for no benefit the shell needs.
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
        # ADR-004 stage 4.1: the capability token, checked IN ADDITION to the
        # origin check above - defense in depth, and the two defend genuinely
        # different threats. The origin check stops a malicious PAGE in the
        # user's browser (which cannot forge Origin); it cannot stop a local
        # PROCESS, which can send any Origin it likes or omit it entirely
        # (the deliberately-allowed "absent Origin" branch in
        # _is_allowed_ws_origin). The token is what closes that second hole -
        # see backend/auth.py's docstring on audit finding C5.
        if resolved_auth_token is not None:
            presented = extract_presented_token(
                websocket.headers.get(AUTH_HEADER),
                websocket.query_params.get(AUTH_QUERY_PARAM),
            )
            if not token_matches(resolved_auth_token, presented):
                logger.warning("rejected unauthenticated WS handshake: origin=%r", origin)
                # 1008 (policy violation), matching the origin rejection just
                # above - closed before accept(), so no session is created and
                # an unauthenticated caller cannot reach the C6 session-growth
                # vector either.
                await websocket.close(code=1008)
                return
        session_id = websocket.query_params.get("session", "default")
        try:
            session = bus.session(session_id)
        except Exception:
            # R6.7 adversarial-review finding: a bug in one of
            # _configure_session's register_X calls used to be swallowed
            # entirely - it never reaches here as a Python-level unhandled
            # exception (uvicorn's own ASGI machinery catches it first and
            # logs it via "uvicorn.error", a logger that does NOT propagate
            # to the root logger - see backend/crash_recovery.py's own
            # RotatingFileHandler, attached to root), so it landed neither
            # in graphlink.log nor in sys.excepthook, contradicting this
            # increment's own point. Logging it via THIS module's own
            # logger (which does propagate to root, exactly like the
            # existing dispatch_intent failure path just below does) is
            # what actually gets it into the log file; closing with 1011
            # (server error) mirrors the origin-rejection branch above -
            # reject cleanly before accept() rather than leaving the
            # client to uvicorn's own default handling.
            logger.exception("session setup failed for session_id=%r", session_id)
            await websocket.close(code=1011)
            return
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
                # ADR-002 stage 2.1d adversarial review finding: guarded to
                # match the exact R6.7 precedent above (bus.session()'s own
                # try/except) - an uncaught exception inside this finally
                # block would otherwise vanish into uvicorn's own
                # "uvicorn.error" logger, which does not propagate to root
                # and never reaches graphlink.log. get_session_context()
                # cannot actually raise here today (session only ever
                # reaches this point via a successful bus.session() call
                # above, which guarantees a SessionContext is already
                # attached), but the cost of guarding it is one log line
                # against a failure mode that is otherwise silent forever.
                try:
                    agent_dispatcher = get_session_context(session).agent_dispatcher
                except Exception:
                    logger.exception("post-disconnect cleanup failed for session_id=%r", session.session_id)
                else:
                    agent_dispatcher.cancel_all()
                    # R5.4: a DELIBERATE, SCOPED extension of this disconnect
                    # contract, applied ONLY to the Py-Coder/Execution Sandbox
                    # approval-pause slots - not retrofitted onto the
                    # pre-existing web_research/artifact/gitlink slots (a real,
                    # separate, out-of-scope gap: every one of those already
                    # self-terminates via asyncio.wait_for(..., timeout=...), so
                    # cancel_all() alone is enough for them). An approval pause
                    # has NO timeout by design (the whole point is "wait for a
                    # human, however long that takes"), so without this
                    # extension an abandoned tab's in-flight approval would hang
                    # forever, permanently locking node.pending_request_id.
                    agent_dispatcher.cancel_all_pending_approvals()

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
