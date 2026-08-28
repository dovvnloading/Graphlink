"""FastAPI app tests (Qt-removal plan R0): health endpoint, WS handshake,
subscribe snapshots, the system/ping acceptance round-trip, and error paths.
Runs the real ASGI app through Starlette's TestClient - no network, no Qt."""

import asyncio
import os
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend import BACKEND_VERSION, crash_recovery
from backend.app import create_app
from backend.session_context import get_session_context
from backend.tests.conftest import chat_slots, code_sandbox_slots

# R7.2: api_provider/graphlink_task_config sit at the repo root, a sibling
# of backend/ - already importable, no ordering constraint.
import api_provider
import graphlink_task_config as config


def make_client(tmp_path: Path | None = None, *, restrict_sessions: bool = True) -> TestClient:
    # Point spa_dir at a guaranteed-missing directory: R0 tests exercise the
    # API surface, not the static build (the acceptance drive covers that).
    spa = tmp_path if tmp_path is not None else Path("__no_such_dir__")
    # R2.5d/e: create_app() now builds a real SettingsManager and a real
    # chats.db path - always point both at a fresh temp dir so tests never
    # read or mutate the developer's actual ~/.graphlink/session.dat or
    # ~/.graphlink/chats.db. TemporaryDirectory (not mkdtemp) so its
    # finalizer removes the dir when the client is garbage collected instead
    # of accumulating litter in %TEMP% run after run; pinned to the client
    # so it lives exactly as long as the app that writes into it.
    state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    state_path = Path(state_dir.name)
    client = TestClient(
        create_app(
            spa_dir=spa,
            settings_state_file=state_path / "session.dat",
            chat_db_path=state_path / "chats.db",
            # ADR-004 stage 4.3: True (the default) matches the real
            # shipped policy - only "default" is ever issuable. A caller
            # here can pass False for the rare test that deliberately
            # exercises EventBus's own generic cross-session isolation
            # THROUGH the real /ws surface (a scenario otherwise
            # unreachable once this restriction is on).
            restrict_sessions=restrict_sessions,
        ),
        # ADR-004 stage 4.2: TrustedHostMiddleware now rejects any Host
        # other than 127.0.0.1 - TestClient's own default ("testserver")
        # would otherwise 400 every request in this file. Matches the real
        # deployment topology (graphlink_desktop.py always binds 127.0.0.1),
        # not a test-only relaxation of the check under test.
        #
        # BOTH kwargs are required, not redundant: base_url governs plain
        # HTTP requests, but Starlette's TestClient.websocket_connect
        # hardcodes Host: testserver independent of base_url (confirmed via
        # a raw-ASGI-scope probe, not assumed) - headers= is what actually
        # reaches the WS upgrade request's own Host header.
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )
    client._state_tmpdir = state_dir  # type: ignore[attr-defined]
    return client


def scene_nodes_from(message: dict, previous: list[dict] | None = None) -> list[dict]:
    """ADR-003 stage 3.4: read a scene publish's node rows off EITHER wire
    shape - the full `kind:"state"` snapshot or a `kind:"patch"` delta, which
    the scene topic now sends whenever it is the smaller of the two. A patch
    carries only the nodes that CHANGED, so `previous` supplies the rest;
    omit it when the caller only cares about the changed ones (the usual case
    for a test asserting "the intent I just fired produced this node")."""
    if message["kind"] == "state":
        return message["payload"]["nodes"]
    by_id = {n["id"]: n for n in (previous or [])}
    for op in message["ops"]:
        if op["op"] == "upsertNode":
            by_id[op["node"]["id"]] = op["node"]
        elif op["op"] == "removeNodes":
            for node_id in op["ids"]:
                by_id.pop(node_id, None)
    return list(by_id.values())


def test_health_reports_ok_and_version():
    client = make_client()
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == BACKEND_VERSION


def test_subscribe_delivers_system_snapshot_with_envelope():
    # ADR-004 stage 4.3: "default" - the only session id the real
    # (restrict_sessions=True by default) policy ever issues.
    client = make_client()
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()
        assert message["kind"] == "state"
        assert message["topic"] == "system"
        assert "id" not in message
        payload = message["payload"]
        assert payload["app"] == "graphlink"
        assert payload["sessionId"] == "default"
        assert payload["schemaVersion"] == 1
        # ADR-003 stage 3.4: this used to assert >= 1, which only held
        # because _Topic.snapshot() itself bumped the revision - so merely
        # SUBSCRIBING advanced the counter, even though a subscribe reaches
        # exactly one connection and is invisible to every other. That is a
        # real bug once `revision` becomes the baseRevision the patch
        # protocol compares against (an unrelated second window subscribing
        # would manufacture a phantom gap and force everyone else to
        # re-snapshot), so bumping now happens only on a real broadcast.
        # A freshly-subscribed session that has published nothing is
        # therefore correctly at 0.
        assert payload["revision"] == 0


def test_explicit_subscribe_id_is_echoed_only_on_its_snapshot():
    client = make_client()
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"], "id": 91})
        message = ws.receive_json()

        assert message["kind"] == "state"
        assert message["topic"] == "system"
        assert message["id"] == 91


def test_scene_subscribe_snapshot_is_pinned_at_schema_version_2():
    # ADR-003 stage 3.5: canvas.py's real register_topic("scene", ...) call -
    # not some test harness's own bus wiring (test_scene_patch_protocol.py's
    # make_scene_bus deliberately stays at the default 1/1, since it tests
    # the patch-protocol MACHINERY generically, decoupled from what any one
    # topic sets it to) - is what the frontend's WsTransport actually talks
    # to. This is what would have silently drifted back to schemaVersion 1
    # with no test noticing, defeating stage 3.5's whole point: the patch
    # protocol (kind:"patch") is a real breaking change for a reader that
    # predates it, and min_compatible=2 is what lets WsTransport.
    # onVersionRejection ever actually fire for a stale frontend build - see
    # backend/canvas.py's own comment on the registration.
    client = make_client()
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["scene"]})
        message = ws.receive_json()
        assert message["kind"] == "state"
        assert message["topic"] == "scene"
        payload = message["payload"]
        assert payload["schemaVersion"] == 2
        assert payload["minCompatibleSchemaVersion"] == 2


def test_subscribe_without_topics_sends_every_registered_topic():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "subscribe"})
        # R2 surface: canvas + View-popover + composer/counter/notification +
        # R2.5 about/plugins/settings/chat-library topics + ADR-005 stage 5.4's
        # execution-limits topic + ADR-016 stage 16.3's diagnostics topic,
        # sorted.
        topics = [ws.receive_json()["topic"] for _ in range(14)]
        assert topics == [
            "app-about",
            "app-chat-library",
            "app-composer",
            "app-plugins",
            "app-settings",
            "diagnostics",
            "drag-speed",
            "execution-limits",
            "font-control",
            "grid-control",
            "notification",
            "scene",
            "system",
            "token-counter",
        ]


def test_ping_round_trip_returns_echo_and_server_time():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"kind": "intent", "topic": "system", "intent": "ping", "args": ["hello"], "id": 1}
        )
        message = ws.receive_json()
        assert message["kind"] == "result"
        assert message["id"] == 1
        assert message["value"]["echo"] == ["hello"]
        assert message["value"]["serverTime"] > 0


def test_unknown_intent_and_topic_return_error_not_disconnect():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "nope", "args": [], "id": 2})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["id"] == 2
        # ADR-003 stage 3.1 review-fix: UnknownIntentError subclasses KeyError,
        # whose __str__ wraps a single-arg message in repr() (literal quotes) -
        # this text now reaches end users via fireIntent()'s notification
        # banner, so it must be the clean sentence, not that raw repr artifact.
        assert message["error"] == "Unknown intent: system/nope."

        ws.send_json({"kind": "intent", "topic": "nope", "intent": "x", "args": [], "id": 3})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["error"] == "Unknown topic: nope."

        # Socket must still be usable after errors.
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": [], "id": 4})
        assert ws.receive_json()["kind"] == "result"


def test_showinfo_rejects_missing_message_before_running_the_handler():
    # ADR-003 stage 3.2: notification/showInfo is one of the 3 intents
    # migrated to args_schema validation in this stage - a malformed call
    # must be rejected BEFORE show_info() runs, not become a bare
    # TypeError caught by the generic handler below it.
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "intent", "topic": "notification", "intent": "showInfo", "args": [], "id": 6})
        message = ws.receive_json()
        assert message["kind"] == "error"
        # Review-fix: validate_payload's own error text carries a JSON-
        # Schema-style "$." path prefix - meaningful internally, but this
        # exact string reaches the end-user notification banner verbatim
        # (fireIntent's showError path from ADR-003 stage 3.1), so
        # _validate_intent_args strips it before app.py ever sends it.
        assert message["error"] == "Invalid arguments: message: missing required field."

        # The rejected call must not have touched notification state - the
        # NEXT subscribe should show visible=False, not a banner from a
        # handler that ran anyway despite the missing arg.
        ws.send_json({"kind": "subscribe", "topics": ["notification"]})
        snapshot = ws.receive_json()
        assert snapshot["payload"]["visible"] is False


def test_showinfo_rejects_wrong_typed_message_before_running_the_handler():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"kind": "intent", "topic": "notification", "intent": "showInfo", "args": [12345], "id": 7}
        )
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["error"] == "Invalid arguments: message: expected string, got int."


def test_showinfo_rejects_a_dict_shaped_args_frame_instead_of_silently_corrupting_the_call():
    # Review-fix (HIGH): the real wire-level version of the attack the
    # adversarial review demonstrated - a client sends a genuine WS frame
    # with "args" as a JSON OBJECT rather than an array. Before the fix,
    # _handle_message's `args = message.get("args") or []` (backend/app.py)
    # passed this dict straight through, zip(fields, a_dict) paired the
    # schema's "message" field with the dict's own KEY STRING (never its
    # value), validation reported zero errors, and show_info(*args) then
    # unpacked the dict's KEYS as positional args - silently displaying the
    # literal field name "message" in the notification banner instead of
    # either the real value or a clean rejection.
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "kind": "intent",
                "topic": "notification",
                "intent": "showInfo",
                "args": {"message": "ATTACKER-CONTROLLED-VALUE"},
                "id": 9,
            }
        )
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["error"] == "Invalid arguments: expected a list of arguments, got dict."

        # The rejected call must not have touched notification state at all
        # - not with the attacker's value, and not with the leaked field name.
        ws.send_json({"kind": "subscribe", "topics": ["notification"]})
        snapshot = ws.receive_json()
        assert snapshot["payload"]["visible"] is False


def test_executeplugin_rejects_wrong_arity_before_running_the_handler():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "kind": "intent",
                "topic": "app-plugins",
                "intent": "executePlugin",
                "args": ["System Prompt", "node-1", "unexpected-extra"],
                "id": 8,
            }
        )
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["error"] == "Invalid arguments: expected at most 2 argument(s), got 3."


def test_junk_args_never_crash_or_disconnect_any_registered_intent():
    """ADR-003 stage 3.2 exit criterion, the fuzz half: 'junk args ->
    structured errors, no tracebacks' - checked empirically against the
    FULL registered intent surface (133 pairs as of this stage, all 8
    topics), not just the 3 intents this stage migrated to args_schema.
    Even an unmigrated handler's own bare TypeError from bad arity is
    already caught by _handle_message's generic except Exception
    (backend/app.py) and turned into a clean {"kind":"error"} reply - this
    proves that property holds for real, rather than being inferred from
    reading the code.

    The (topic, intent) list is discovered LIVE from the real app
    (client.app.state.bus, populated by _configure_session - the same
    function create_app() wires into every real session) rather than a
    hand-maintained list here, so a future new intent is covered
    automatically with no risk of this test silently going stale.

    junk_args is deliberately longer (20 elements) than any real handler's
    param count (addDocumentNode, the largest, takes 10) - this guarantees
    Python's own arity check rejects EVERY call with a TypeError before the
    REAL underlying handler's own body runs, regardless of the handler's own
    parameter types. That is what makes it safe to fire at literally every
    intent, including native-OS-dialog openers (pickGitlinkLocalRoot,
    attachFile, the Llama.cpp/Ollama file/folder pickers) and network/
    subprocess-backed ones: none of THEIR bodies ever execute, so there is
    no risk of this test popping a real dialog or making a real network
    call.

    Review-fix: "the real underlying handler" above is a deliberate
    qualifier, not loose phrasing - app-chat-library's loadChat/saveChat/
    newChat are registered via chat_library.py's _serialize_mutating_intent,
    whose own `wrapped(*args, **kwargs)` IS fully variadic (claims/releases
    a cross-intent mutation guard before calling the real load_chat/
    save_chat/new_chat). Python's arity check can't reject a call at THAT
    outer boundary, so `wrapped`'s own body genuinely runs - claiming, then
    releasing the guard in a `finally` - before the real handler's own
    TypeError fires one level in. This is not unsafe (the guard is always
    released again, and this test's single connection sends everything
    sequentially, so no other call ever observes it mid-claim), but it does
    mean "no handler body ever executes" is not literally true for these 3
    intents specifically - only true of the real, wrapped handler.

    The one exception with no wrapper at all is system/ping, a deliberately
    variadic echo intent (register_intent's own docstring covers why it has
    no args_schema) - it accepts and echoes back all 20 values, which is a
    "result" reply, not a crash, and is asserted for accordingly below.
    """
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        # Force this session to exist and be fully configured before
        # introspecting its registered intents.
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        ws.receive_json()

        session_bus = client.app.state.bus.session()
        all_intents = sorted(session_bus._intents.keys())
        # Sanity guard: if this collapses to a handful of entries, the
        # introspection path itself broke silently (e.g. an empty/wrong
        # session) - the test would then "pass" while covering nothing.
        assert len(all_intents) >= 100, (
            f"expected the full ~133-intent app surface, only found {len(all_intents)} - "
            "the live intent discovery may be broken"
        )

        junk_args = [{"__fuzz__": True}, 12345, None, "??", [1, 2, 3]] * 4  # 20 elements
        next_id = 1000
        for topic, intent in all_intents:
            next_id += 1
            ws.send_json(
                {"kind": "intent", "topic": topic, "intent": intent, "args": junk_args, "id": next_id}
            )
            message = ws.receive_json()
            assert message["kind"] in ("error", "result"), (
                f"{topic}/{intent}: got {message['kind']!r} for junk args - expected error or result, "
                "never a disconnect or an unrecognized reply kind"
            )
            assert message["id"] == next_id, f"{topic}/{intent}: reply id mismatch - socket desynced"

        # The connection must still be fully usable after 130+ consecutive
        # malformed calls - proves no cumulative state corruption in the
        # dispatch loop itself, not just per-call resilience.
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": [], "id": 99999})
        assert ws.receive_json()["kind"] == "result"


def test_args_schema_is_scoped_to_exactly_the_known_17_intents():
    # ADR-003 stage 3.2 review-fix: the fuzz sweep above proves every intent
    # replies safely, but it only checks message["kind"], not the error TEXT
    # - a schema-validation rejection and an unmigrated handler's own generic
    # TypeError rejection both come back as plain {"kind":"error"}, so that
    # sweep alone could not catch a FUTURE mutation that accidentally added
    # (or removed) an args_schema on the wrong intent. This test asserts the
    # real registry's args_schema is not None set directly against the exact
    # intents that carry one - silent scope drift on this security/
    # correctness-adjacent mechanism would fail here even though it wouldn't
    # fail the fuzz sweep. Originally the exact 3 intents ADR-003 stage 3.2's
    # own PR description claimed to have migrated (showInfo/showError/
    # executePlugin); ADR-014 stage 14.4 added 2 more
    # (invokePluginIntent/setPluginGrant); ADR-020 stage 20.2 added 6 more on
    # "app-chat-library" (setGraphFavorite/setGraphArchived/setGraphTags/
    # createWorkspace/renameWorkspace/archiveWorkspace); ADR-020 stage 20.3
    # added 1 more on "app-chat-library" (setWorkspaceDefaultModel); ADR-020
    # stage 20.4 added 2 more (("app-chat-library", "loadGraphAndFocusNode")
    # and ("globalSearch", "search")); stage 20.5 added 1 more
    # (("app-chat-library", "exportWorkspace")); a 2026-08-25 tech-debt sweep
    # added 2 more (("knowledge", "search") and ("scene",
    # "setChatIndexIntoKnowledge")) - each a real, deliberate addition, not
    # drift - a real intentional addition to this set updates it explicitly,
    # the same discipline this test itself exists to enforce.
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        ws.receive_json()

        session_bus = client.app.state.bus.session()
        validated = {
            key for key, registration in session_bus._intents.items()
            if registration.args_schema is not None
        }
        assert validated == {
            ("notification", "showInfo"),
            ("notification", "showError"),
            ("app-plugins", "executePlugin"),
            ("app-plugins", "invokePluginIntent"),
            ("app-plugins", "setPluginGrant"),
            ("app-chat-library", "setGraphFavorite"),
            ("app-chat-library", "setGraphArchived"),
            ("app-chat-library", "setGraphTags"),
            ("app-chat-library", "createWorkspace"),
            ("app-chat-library", "renameWorkspace"),
            ("app-chat-library", "archiveWorkspace"),
            ("app-chat-library", "setWorkspaceDefaultModel"),
            ("app-chat-library", "loadGraphAndFocusNode"),
            ("globalSearch", "search"),
            ("app-chat-library", "exportWorkspace"),
            ("knowledge", "search"),
            ("scene", "setChatIndexIntoKnowledge"),
        }


def test_showerror_intent_round_trips_through_the_real_app_and_updates_the_notification_snapshot():
    # ADR-003 stage 3.1 review-fix (finding K): every other showError test
    # dispatches straight into a bare EventBus (backend/tests/test_backend_
    # composer.py), bypassing this real app.py handler entirely - this is the
    # one test that drives the actual WS frame a browser's WsTransport sends
    # through the real create_app() wiring end to end: a genuine {"kind":
    # "intent","topic":"notification","intent":"showError",...} frame in,
    # a proper {"kind":"result"} reply, and a subsequent notification
    # snapshot reflecting the error - the same round trip fireIntent()'s own
    # error-surfacing path in transport.ts relies on in production.
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "kind": "intent",
                "topic": "notification",
                "intent": "showError",
                "args": ["Something went wrong."],
                "id": 5,
            }
        )
        # bus.publish() broadcasts to every ATTACHED connection unconditionally
        # (backend/events.py's SessionContext.attach/publish - no client-side
        # "subscribe" message is required), and the handler awaits that
        # publish before returning - so the resulting state snapshot arrives
        # on the wire BEFORE the intent's own {"kind":"result"} reply.
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"
        assert snapshot["topic"] == "notification"
        assert snapshot["payload"]["visible"] is True
        assert snapshot["payload"]["message"] == "Something went wrong."
        assert snapshot["payload"]["msgType"] == "error"

        message = ws.receive_json()
        assert message == {"kind": "result", "id": 5, "value": None}


def test_unknown_message_kind_returns_error():
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "bogus", "id": 9})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert "unknown message kind" in message["error"]


def test_a_non_dict_top_level_message_gets_a_graceful_error_not_a_dropped_connection():
    """Regression: websocket.receive_json() is a bare json.loads() with no
    shape check - any syntactically valid top-level JSON value (a bare
    number, a list, null, a string) reached _handle_message unchanged, and
    `kind = message.get("kind")` raised AttributeError straight past the
    only try/except in the receive loop (WebSocketDisconnect only),
    silently killing the connection with zero client-facing feedback -
    unlike every other malformed-input path in this same function, which
    replies with a graceful kind:error frame. Sends raw text (not
    send_json, which only ever encodes a dict-shaped Python value) to put
    a genuinely non-dict JSON value on the wire, exactly as a malformed
    client would."""
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_text("42")
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert "malformed message" in message["error"]

        # The connection must still be alive and working afterward - a
        # graceful reply, not a dropped socket.
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"


def test_a_syntactically_invalid_json_frame_gets_a_graceful_error_not_a_dropped_connection():
    """Regression: websocket.receive_json() is a bare json.loads() with no
    try/except of its own (starlette's own implementation) - a text frame
    that isn't syntactically valid JSON AT ALL (as opposed to
    valid-JSON-but-wrong-shape, which the non-dict test above already
    covers) raised json.JSONDecodeError straight out of that await, past
    the only except clause around the receive loop (WebSocketDisconnect
    only), and killed the connection outright with zero client-facing
    feedback. Sends raw, deliberately unparseable text (not send_json,
    which can only ever encode valid JSON) to put a genuinely malformed
    frame on the wire, exactly as a garbling proxy or a truncated write
    would."""
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_text("{not valid json")
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert "malformed message" in message["error"]

        # The connection must still be alive and working afterward - a
        # graceful reply, not a dropped socket.
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"


def test_a_non_list_topics_field_gets_a_graceful_error_not_a_dropped_connection():
    """Regression: `topics = message.get("topics") or session.topic_names()`
    only falls back for a FALSY topics value - a truthy non-iterable (the
    JSON number 5) bypassed the fallback and raised TypeError straight out
    of `for topic in topics`, escaping uncaught for the same reason as the
    non-dict case above."""
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "subscribe", "topics": 5, "id": 1})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["id"] == 1
        assert "topics" in message["error"]

        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"


def test_a_non_string_topic_element_gets_a_graceful_error_not_a_dropped_connection():
    """SECURITY-FIX: `topics` was checked to be a list, but not that each
    ELEMENT is a string - {"topics": [{}]} passed the list check and
    reached send_snapshot -> self._topics.get(topic), which raised
    TypeError('unhashable type: dict'), not UnknownTopicError, escaping
    uncaught and killing the connection."""
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "subscribe", "topics": [{}], "id": 1})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["id"] == 1
        assert "topic" in message["error"]

        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"


def test_a_non_list_args_field_for_an_unschemad_intent_gets_a_graceful_error_not_mangled_positional_unpacking():
    """SECURITY-FIX: a schema'd intent already rejects non-list args via
    _validate_intent_args (see test_showinfo_rejects_a_dict_shaped_args_
    frame above); an intent with NO schema - "system"/"ping" here, which
    takes `*args` - skipped that check entirely, so `handler(*args)`
    unpacked whatever shape the client sent. A string star-unpacks by
    character instead of raising a validation error."""
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": "abc", "id": 1})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["id"] == 1
        assert message["error"] == "Invalid arguments: expected a list of arguments, got str."

        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"


def test_a_falsey_non_list_args_field_is_not_treated_as_omitted():
    """The wire contract requires ``args`` to be an array when present.

    ``message.get("args") or []`` made a falsey scalar such as JSON ``false``
    indistinguishable from an omitted field, so ``system/ping`` returned a
    successful empty call instead of the same validation error used for a
    truthy scalar or object.  This must be rejected without closing the
    connection.
    """
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": False, "id": 2})
        message = ws.receive_json()
        assert message["kind"] == "error"
        assert message["id"] == 2
        assert message["error"] == "Invalid arguments: expected a list of arguments, got bool."

        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        snapshot = ws.receive_json()
        assert snapshot["kind"] == "state"


def test_sessions_do_not_share_connections():
    # ADR-004 stage 4.3: tests EventBus's own generic cross-session
    # isolation mechanism, a scenario the real shipped app's restrictive
    # policy makes unreachable (only "default" is ever issuable there) -
    # restrict_sessions=False opts this one client out, matching
    # EventBus's own pre-stage-4.3 default behavior.
    client = make_client(restrict_sessions=False)
    with client.websocket_connect("/ws?session=a") as ws_a:
        with client.websocket_connect("/ws?session=b") as ws_b:
            ws_a.send_json({"kind": "subscribe", "topics": ["system"]})
            ws_b.send_json({"kind": "subscribe", "topics": ["system"]})
            assert ws_a.receive_json()["payload"]["sessionId"] == "a"
            assert ws_b.receive_json()["payload"]["sessionId"] == "b"


def test_disconnect_cancels_any_in_flight_chat_request(monkeypatch):
    # R4 concurrency-review finding: a client that sends a message and then
    # closes its tab must not leave the real outbound LLM call running
    # server-side forever with no way to ever cancel it. ws_endpoint's
    # disconnect handler should trip the session's AgentDispatcher cancel
    # event once its last connection drops - this exercises that through the
    # real ASGI app, not just AgentDispatcher in isolation (test_agents.py
    # already covers cancel()/cancel_all() unit-level).
    call_started = threading.Event()

    def fake_chat(task, messages, cancellation_event=None, **kwargs):
        call_started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancellation_event is not None and cancellation_event.is_set():
                raise api_provider.RequestCancelledError("cancelled")
            time.sleep(0.01)
        raise AssertionError("cancel_event was never set after disconnect")

    # make_client() -> create_app() runs bootstrap_provider_state() against
    # a fresh (unconfigured) SettingsManager - monkeypatching BEFORE that
    # would just get overwritten, so the client comes first.
    client = make_client()
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "test-model")
    monkeypatch.setattr(api_provider, "chat", fake_chat)

    # ADR-004 stage 4.3: "default" - the only session id the real
    # (restrict_sessions=True by default) policy ever issues.
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["scene"]})
        ws.receive_json()  # initial scene snapshot
        ws.send_json({"kind": "intent", "topic": "scene", "intent": "sendMessage", "args": ["hello"]})
        ws.receive_json()  # scene republish after the user node is created

        deadline = time.monotonic() + 5
        while not call_started.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert call_started.is_set(), "fake_chat never started - dispatch did not fire"

        session = client.app.state.bus.session("default")
        in_flight = list(chat_slots(get_session_context(session).agent_dispatcher).values())
        assert len(in_flight) == 1
        cancel_event = in_flight[0]["cancel_event"]
        assert not cancel_event.is_set()
    # Exiting the `with` block closes the websocket, running ws_endpoint's
    # finally - this is the disconnect this test exists to exercise.

    deadline = time.monotonic() + 5
    while not cancel_event.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert cancel_event.is_set()


def test_disconnect_auto_denies_any_pending_code_sandbox_approval(monkeypatch):
    # R5.4: an approval pause has NO timeout by design (the whole point is
    # "wait for a human, however long that takes") - so a client that starts
    # an Execution Sandbox run, sees the approval gate, then closes its tab
    # WITHOUT ever approving or denying must not leave that request parked
    # forever, permanently locking node.pending_request_id. ws_endpoint's
    # disconnect handler must resolve the pending approval_future to False
    # (auto-deny) once the session's last connection drops - this exercises
    # that through the real ASGI app end to end, not just AgentDispatcher.
    # cancel_all_pending_approvals in isolation (test_agents.py already
    # covers that unit-level).
    import backend.agents as agents_module

    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    client = make_client()
    # ADR-004 stage 4.3: "default" - the only session id the real
    # (restrict_sessions=True by default) policy ever issues.
    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["scene"]})
        ws.receive_json()  # initial scene snapshot

        # No "id" on either intent below - deliberately, to keep message
        # ordering simple: each add_*/execute_plugin wrapper broadcasts its
        # own scene state DURING the call (before dispatch_intent returns),
        # so with no id there is exactly one message per intent to drain,
        # and the new node's id is read back from that broadcast's own
        # payload rather than from a "result" envelope.
        ws.send_json({"kind": "intent", "topic": "scene", "intent": "addNode", "args": [0, 0, "root"]})
        nodes_after_add = scene_nodes_from(ws.receive_json())
        parent_id = nodes_after_add[0]["id"]

        ws.send_json(
            {
                "kind": "intent", "topic": "app-plugins", "intent": "executePlugin",
                "args": ["Virtual Environment Runner", parent_id],
            }
        )
        nodes_after_plugin = scene_nodes_from(ws.receive_json(), nodes_after_add)
        node_id = next(n["id"] for n in nodes_after_plugin if n["kind"] == "code_sandbox")

        ws.send_json(
            {"kind": "intent", "topic": "scene", "intent": "runCodeSandbox", "args": [node_id, "add 1"]}
        )
        # Exactly two scene republishes land before the pipeline genuinely
        # pauses: (1) run_code_sandbox's own synchronous busy-claim publish,
        # (2) the background task's publish right after it sets
        # code_sandbox_awaiting_approval=True and parks on `await
        # approval_future` - nothing more arrives until approve/deny or
        # disconnect.
        ws.receive_json()  # (1) busy-claim publish
        ws.receive_json()  # (2) awaiting-approval publish

        session = client.app.state.bus.session("default")
        agent_dispatcher = get_session_context(session).agent_dispatcher
        assert code_sandbox_slots(agent_dispatcher), "runCodeSandbox never created a request entry"
        entry = next(iter(code_sandbox_slots(agent_dispatcher).values()))
        approval_future = entry["approval_future"]
        assert not approval_future.done(), "the pipeline must genuinely be parked on the gate here"
    # Exiting the `with` block closes the websocket, running ws_endpoint's
    # finally - the disconnect this test exists to exercise.

    deadline = time.monotonic() + 5
    while not approval_future.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert approval_future.done(), "the pending approval must be auto-denied on disconnect"
    assert approval_future.result() is False


def test_diagnostics_intents_dispatch_through_the_real_bus(tmp_path, monkeypatch):
    # ADR-016 stage 16.4: exportDiagnosticBundle/openLogFolder registered by
    # backend/api/intents_diagnostics.py, wired into the real create_app()
    # via backend/app.py's _configure_session - dispatched here directly
    # against the real SessionBus (not a hand-rolled make_session() bus),
    # same "the real app.py wiring" posture as
    # test_showerror_intent_round_trips_through_the_real_app_and_updates_the_notification_snapshot
    # above.
    #
    # backend.crash_recovery._data_dir() is monkeypatched to a tmp_path so
    # this never writes into the developer's real ~/.graphlink/diagnostics,
    # and os.startfile is monkeypatched so openLogFolder never actually
    # spawns Explorer during the test run.
    monkeypatch.setattr(crash_recovery, "_data_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(os, "name", "nt")
    startfile_calls = []
    monkeypatch.setattr(os, "startfile", lambda path: startfile_calls.append(path), raising=False)

    client = make_client()
    with client.websocket_connect("/ws?session=default") as ws:
        # Force this session to exist and be fully configured before
        # dispatching straight into its bus.
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        ws.receive_json()

        session_bus = client.app.state.bus.session("default")

        bundle_result = asyncio.run(session_bus.dispatch_intent("diagnostics", "exportDiagnosticBundle", []))
        assert set(bundle_result.keys()) == {"bundle", "path"}
        assert bundle_result["bundle"]["bundleSchemaVersion"] == 1
        assert bundle_result["bundle"]["appVersion"]
        written_path = Path(bundle_result["path"])
        assert written_path.exists()
        assert written_path.parent == tmp_path / "diagnostics"

        log_folder_result = asyncio.run(session_bus.dispatch_intent("diagnostics", "openLogFolder", []))
        assert log_folder_result == {"opened": True}
        assert len(startfile_calls) == 1
