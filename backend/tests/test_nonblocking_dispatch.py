"""ADR-006 stage 6.2 exit criteria, each pinned as a test:

1. the 7-minute-freeze class is gone - a chart generation (formerly awaited
   inline by the WS read loop for up to the 420 s watchdog) no longer blocks
   the socket: a ping sent DURING a held generation round-trips immediately;
2. cancel frees the slot immediately (well under the 2 s budget) - the slot
   empties the moment the cancel intent lands, not when the worker thread
   eventually observes it;
3. cancel_all covers every run kind - statically, every claim() call site
   passes a cancellation mechanism, so the pre-6.2 "chart/note/branch_*/
   image/gitlink_apply are silently skipped" gap cannot re-open unnoticed.

The WS-level tests drive the REAL ASGI app end to end (the same harness
test_app_ws.py established), with only the provider network call faked.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import api_provider
import graphlink_task_config as config
from backend.app import get_session_context
from backend.tests.conftest import chat_slots
from backend.tests.test_app_ws import make_client

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every file whose claim() call sites the two static scans below must see:
# AgentDispatcher's dispatch methods live in the backend/agent_dispatch/
# mixin files since the god-file decomposition split them out of
# backend/agents.py (which keeps only the composed class plus module-level
# helpers) - globbed rather than listed, so a future mixin joins the scan
# automatically instead of silently escaping it.
_CLAIM_SCAN_FILES = tuple(
    ["backend/agents.py", "backend/run_lifecycle.py"]
    + sorted(
        str(p.relative_to(REPO_ROOT)).replace("\\", "/")
        for p in (REPO_ROOT / "backend" / "agent_dispatch").glob("*.py")
    )
)


def _recv_until(ws, predicate, max_frames=50):
    for _ in range(max_frames):
        frame = ws.receive_json()
        if predicate(frame):
            return frame
    raise AssertionError(f"no matching frame within {max_frames} frames")


def test_a_held_chart_generation_no_longer_blocks_the_ws_read_loop(monkeypatch):
    """EXIT CRITERION (the C2 freeze class): before 6.2, generateChart was
    awaited inline at app.py's receive loop - THIS exact sequence (hold the
    generation open, then ping on the same socket) would deadlock until the
    watchdog. Now the ping round-trips while the generation is still held."""
    started, release = threading.Event(), threading.Event()

    def fake_chat(task, messages, **kwargs):
        started.set()
        assert release.wait(10), "test never released the held chart call"
        return {"message": {"content": '{"type": "bar", "title": "T", "labels": ["A"], "values": [1.0]}'}}

    client = make_client()
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "test-model")
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHART, "test-model")
    monkeypatch.setattr(api_provider, "chat", fake_chat)

    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["scene"]})
        ws.receive_json()  # initial scene snapshot

        session = client.app.state.bus.session("default")
        context = get_session_context(session)
        # The chart's source node, created directly on the document - node
        # creation is not what this test is about.
        node = context.canvas_document.add_chat_node(0, 0, "some data: 1, 2, 3", False, parent_id=None)

        ws.send_json({"kind": "intent", "topic": "scene", "intent": "generateChart", "args": [node.id, "bar"]})

        deadline = time.monotonic() + 5
        while not started.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.is_set(), "chart generation never started"
        assert context.agent_dispatcher._runs.is_busy("chart")

        # The exit criterion itself: with the generation STILL HELD, a ping on
        # the same socket must round-trip. Pre-6.2 this receive would hang
        # until the 420 s watchdog fired.
        t0 = time.monotonic()
        # The WS protocol only sends a {"kind": "result"} frame back when the
        # intent message carries an "id", and the payload field is "value"
        # (backend/app.py's intent branch) - without both, this receive would
        # wait forever regardless of whether the read loop is alive.
        ws.send_json({
            "kind": "intent", "topic": "system", "intent": "ping",
            "args": ["loop-alive"], "id": "ping-loop-alive",
        })
        frame = _recv_until(
            ws, lambda f: f.get("kind") == "result" and "loop-alive" in str(f.get("value", ""))
        )
        assert time.monotonic() - t0 < 5
        assert frame["kind"] == "result"

        release.set()
        deadline = time.monotonic() + 5
        while context.agent_dispatcher.has_in_flight_runs() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not context.agent_dispatcher.has_in_flight_runs()


def test_cancel_frees_the_chat_slot_immediately_not_when_the_worker_returns(monkeypatch):
    """EXIT CRITERION (cancel frees slot < 2 s): the slot empties the moment
    cancelChatRequest lands. The fake worker deliberately IGNORES the cancel
    event for a while (simulating a provider blocked in a network call, the
    exact case where the old release-in-finally kept the slot busy) - the
    slot must be free while the worker is still running."""
    started, release = threading.Event(), threading.Event()

    def fake_chat(task, messages, cancellation_event=None, **kwargs):
        started.set()
        assert release.wait(10), "test never released the held chat call"
        raise api_provider.RequestCancelledError("cancelled")

    client = make_client()
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "test-model")
    monkeypatch.setattr(api_provider, "chat", fake_chat)

    with client.websocket_connect("/ws?session=default") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["scene"]})
        ws.receive_json()
        ws.send_json({"kind": "intent", "topic": "scene", "intent": "sendMessage", "args": ["hello"]})
        ws.receive_json()  # scene republish after the user node is created

        deadline = time.monotonic() + 5
        while not started.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.is_set()

        session = client.app.state.bus.session("default")
        dispatcher = get_session_context(session).agent_dispatcher
        in_flight = list(chat_slots(dispatcher).values())
        assert len(in_flight) == 1
        request_id = next(iter(chat_slots(dispatcher).keys()))

        ws.send_json({"kind": "intent", "topic": "scene", "intent": "cancelChatRequest", "args": [request_id]})

        # The budget is 2 s; the slot should actually free within
        # milliseconds of the cancel intent being dispatched - and crucially
        # while the worker is STILL held open (release has not been set).
        deadline = time.monotonic() + 2
        while chat_slots(dispatcher) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not chat_slots(dispatcher), "cancel did not free the slot within the 2 s budget"
        assert not release.is_set()  # the worker really is still running
        # ...but the cancelled work is still tracked as live until it ends
        # (session eviction must keep waiting for it - see has_any_live_work).
        assert dispatcher.has_in_flight_runs()

        release.set()
        deadline = time.monotonic() + 5
        while dispatcher.has_in_flight_runs() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not dispatcher.has_in_flight_runs()


def test_every_claim_site_passes_a_cancellation_mechanism():
    """EXIT CRITERION (cancel_all covers all runs), enforced statically the
    same way test_dispatch_claim_ordering pins its own invariant: every
    registry claim() call under backend/ must pass cancel_event= or
    on_cancel=. Before 6.2, six kinds (chart, note, branch_comparison,
    branch_synthesis, image, gitlink_apply) passed neither and were silently
    skipped by cancel_all() - a new kind reintroducing that gap fails here."""
    offenders = []
    for rel in _CLAIM_SCAN_FILES:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "claim"
            ):
                continue
            keywords = {kw.arg for kw in node.keywords}
            if "cancel_event" not in keywords and "on_cancel" not in keywords:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "these claim() call sites pass neither cancel_event= nor on_cancel= - "
        "the run kind they create would be invisible to cancel()/cancel_all(), "
        "reopening the pre-6.2 uncancellable-kind gap:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_the_claim_scan_finds_the_real_population():
    # Guards the guard: the scan above must actually see the known claim
    # sites (9 as of stage 6.2), not silently match nothing.
    count = 0
    for rel in _CLAIM_SCAN_FILES:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"), filename=rel)
        count += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "claim"
        )
    assert count >= 9, f"expected at least 9 claim() call sites, found {count}"
