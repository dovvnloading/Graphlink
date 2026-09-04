"""Backend integration tests for Review Lens.

Covers what test_review_lens_domain.py deliberately skips (pure domain
logic): the SceneDocument graph methods, the scene wire row, the
save/load round trip, the AgentDispatcher dispatch surfaces, the scene
intents (through a real SessionBus with a stub dispatcher, mirroring
test_canvas.py's own Gitlink intent tests), and picker creation + undo
through the real discovered plugin registry.

Mocking follows the suite's own conventions: backend.agents module
helpers are monkeypatched (the agent_dispatch mixin resolves them
late-bound), never the mixin methods themselves.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugins import register_plugins
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from graphlink_settings_store import SettingsManager


def _doc_with_review():
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "parent", is_user=True)
    node = doc.add_code_review_node(10, 10, parent.id)
    return doc, node


def _bundle(**overrides):
    base = {
        "repo": "o/r", "pr_number": 3, "pr_title": "T", "pr_state": "open",
        "html_url": "https://github.com/o/r/pull/3", "base_ref": "main",
        "head_ref": "feature", "additions": 5, "deletions": 1,
        "changed_files": 1,
        "files": [{"path": "x.py", "status": "modified", "additions": 5,
                   "deletions": 1, "patch": "@@ x", "patch_truncated": False}],
        "files_truncated": False, "diff_text": "diff --git x", "diff_truncated": False,
        "diff_chars": 11,
    }
    base.update(overrides)
    return base


def _result(**overrides):
    base = {
        "title": "T", "overview": "O", "confidence": "high",
        "walkthrough": [{"group_title": "G", "paths": ["x.py"], "explanation": "E"}],
        "review_findings": [{"id": "f1", "severity": "medium", "tier": "yellow",
                             "category": "Testing", "path": "x.py", "line": 4,
                             "title": "T", "evidence": "E", "impact": "I",
                             "recommendation": "R"}],
        "errors_found": [], "category_scores": {"correctness": 80},
        "quality_score": 80, "verdict": "strong", "risk_level": "low",
        "quality_summary": "S",
    }
    base.update(overrides)
    return base


# -- graph methods -----------------------------------------------------------


def test_add_code_review_node_titles_kinds_and_edges_parent():
    doc, node = _doc_with_review()
    assert node.title == "Review Lens"
    assert node.kind == "code_review"
    assert any(e.target == node.id for e in doc.edges.values())
    standalone = doc.add_code_review_node(5, 5, None)
    assert standalone.id != node.id


def test_add_code_review_node_rejects_unknown_parent():
    doc = SceneDocument()
    with pytest.raises(SceneError):
        doc.add_code_review_node(0, 0, "missing")


def test_set_pr_url_and_wrong_kind_guard():
    doc, node = _doc_with_review()
    doc.set_code_review_pr_url(node.id, "https://github.com/o/r/pull/3")
    assert doc.nodes[node.id].state.code_review_pr_url == "https://github.com/o/r/pull/3"
    chat = doc.add_chat_node(0, 0, "c", is_user=True)
    with pytest.raises(SceneError):
        doc.set_code_review_pr_url(chat.id, "x")


def test_store_diff_lands_fields_bumps_version_and_resets_review():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    live = doc.nodes[node.id]
    assert live.state.code_review_repo == "o/r"
    assert live.state.code_review_pr_number == 3
    assert live.state.code_review_files[0]["path"] == "x.py"
    assert live.state.code_review_diff_version == 1
    assert live.state.code_review_state == "fetched"
    assert live.state.code_review_error == ""
    # A re-fetch supersedes the old review rather than merging into it.
    doc.complete_code_review_run(node.id, **{
        "title": "T", "overview": "O", "confidence": "high", "walkthrough": [],
        "findings": live.state.code_review_findings, "errors": [], "scores": {},
        "quality_score": 1, "verdict": "strong", "risk": "low", "quality_summary": "S",
    })
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    live = doc.nodes[node.id]
    assert live.state.code_review_diff_version == 2
    assert live.state.code_review_verdict == "none"
    assert live.state.code_review_quality_score == 0


def test_fetch_diff_text_is_wrong_kind_guarded():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    assert doc.fetch_code_review_diff_text(node.id) == "diff --git x"


def test_complete_run_caps_and_resets_dismissals():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high",
        walkthrough=[{"group_title": f"g{i}", "paths": ["x"], "explanation": "e"} for i in range(20)],
        findings=[{"id": f"f{i}"} for i in range(30)],
        errors=[{"id": f"e{i}"} for i in range(30)],
        scores={"correctness": 90}, quality_score=90,
        verdict="strong", risk="low", quality_summary="S",
    )
    live = doc.nodes[node.id]
    assert len(live.state.code_review_walkthrough) == 8
    assert len(live.state.code_review_findings) == 12
    assert len(live.state.code_review_errors) == 10
    assert live.state.code_review_state == "reviewed"


def test_fail_run_is_silent_for_missing_nodes_and_keeps_prior_review():
    doc, node = _doc_with_review()
    assert doc.fail_code_review_run("missing", "boom") is None
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high", walkthrough=[],
        findings=[], errors=[], scores={}, quality_score=80,
        verdict="strong", risk="low", quality_summary="S",
    )
    doc.fail_code_review_run(node.id, "model timed out")
    live = doc.nodes[node.id]
    assert live.state.code_review_error == "model timed out"
    assert live.state.code_review_verdict == "strong"  # prior review survives


def test_dismiss_finding_is_idempotent_and_quiet_on_unknown_ids():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high", walkthrough=[],
        findings=[{"id": "f1"}], errors=[{"id": "e1"}], scores={},
        quality_score=80, verdict="strong", risk="low", quality_summary="S",
    )
    doc.dismiss_code_review_finding(node.id, "f1")
    doc.dismiss_code_review_finding(node.id, "f1")  # repeat: no duplicate
    doc.dismiss_code_review_finding(node.id, "nope")  # unknown: quiet no-op
    assert doc.nodes[node.id].state.code_review_dismissed_ids == ["f1"]


def test_append_qa_caps_at_twenty_entries():
    doc, node = _doc_with_review()
    for i in range(25):
        doc.append_code_review_qa(node.id, f"q{i}", f"a{i}")
    qa = doc.nodes[node.id].state.code_review_qa
    assert len(qa) == 20
    assert qa[0]["question"] == "q5"
    assert qa[-1]["question"] == "q24"


# -- wire --------------------------------------------------------------------


def test_wire_row_carries_review_fields_but_not_the_diff_text():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high",
        walkthrough=[], findings=[], errors=[], scores={"correctness": 80},
        quality_score=80, verdict="strong", risk="low", quality_summary="S",
    )
    row = doc.scene_payload()["nodes"][-1]
    assert row["codeReviewRepo"] == "o/r"
    assert row["codeReviewPrNumber"] == 3
    assert row["codeReviewDiffVersion"] == 1
    assert row["codeReviewScores"] == {"correctness": "80"}
    assert row["codeReviewVerdict"] == "strong"
    assert "codeReviewDiffText" not in row


# -- save/load round trip ------------------------------------------------------


def test_save_load_round_trip_preserves_review_state():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="https://github.com/o/r/pull/3", bundle=_bundle())
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high",
        walkthrough=[{"group_title": "G", "paths": ["x.py"], "explanation": "E"}],
        findings=[{"id": "f1", "severity": "medium", "tier": "yellow",
                   "category": "Testing", "path": "x.py", "line": 4,
                   "title": "T", "evidence": "E", "impact": "I", "recommendation": "R"}],
        errors=[], scores={"correctness": 80}, quality_score=80,
        verdict="strong", risk="low", quality_summary="S",
    )
    doc.dismiss_code_review_finding(node.id, "f1")
    doc.append_code_review_qa(node.id, "why?", "because.")
    chat_data = build_chat_data(doc)
    payload = next(n for n in chat_data["nodes"] if n.get("node_type") == "code_review")
    assert payload["pr_state"]["repo"] == "o/r"
    assert payload["diff_text"] == "diff --git x"
    assert payload["review"]["verdict"] == "strong"

    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    doc2 = SceneDocument()
    restore_chat_into_document(doc2, {"data": chat_data}, notes_data, pins_data)
    restored = next(n for n in doc2.nodes.values() if n.kind == "code_review")
    assert restored.title == "Review Lens"
    assert restored.state.code_review_repo == "o/r"
    assert restored.state.code_review_diff_text == "diff --git x"
    assert restored.state.code_review_verdict == "strong"
    assert restored.state.code_review_dismissed_ids == ["f1"]
    assert restored.state.code_review_qa == [{"question": "why?", "answer": "because."}]
    assert restored.state.code_review_findings[0]["id"] == "f1"


# -- dispatch ------------------------------------------------------------------


class _FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, topic):
        self.published.append(topic)


def _dispatcher():
    return AgentDispatcher(SettingsManager(Path(tempfile.mkdtemp()) / "session.dat"))


def test_dispatch_fetch_returns_bundle_and_releases_busy_slot(monkeypatch):
    monkeypatch.setattr(
        agents_module, "_fetch_code_review_bundle", lambda settings_manager, pr_url: _bundle(),
    )
    dispatcher = _dispatcher()
    doc, node = _doc_with_review()

    async def run():
        bus = _FakeBus()
        result = await dispatcher.fetch_code_review_diff(
            bus=bus, notifications_state=NotificationState(), node=node, pr_url="u",
        )
        return result, bus

    result, bus = asyncio.run(run())
    assert result["repo"] == "o/r"
    assert node.pending_request_id is None
    assert "scene" in bus.published


def test_dispatch_run_lands_result_through_callbacks(monkeypatch):
    monkeypatch.setattr(
        agents_module, "_call_review_lens_agent", lambda bundle: _result(),
    )
    dispatcher = _dispatcher()
    doc, node = _doc_with_review()
    landed = {}

    async def run():
        bus = _FakeBus()
        await dispatcher.start_code_review_run(
            bus=bus, notifications_state=NotificationState(), node=node,
            node_id=node.id, bundle=_bundle(),
            on_success=lambda result: landed.update(result),
            on_failure=lambda message: None,
        )
        # Let the scheduled background task finish.
        for _ in range(100):
            if "scene" in bus.published and node.pending_request_id is None:
                break
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert landed["verdict"] == "strong"
    assert node.pending_request_id is None


def test_dispatch_run_rejects_a_busy_node_without_claiming():
    async def run():
        dispatcher = _dispatcher()
        doc, node = _doc_with_review()
        node.pending_request_id = "someone-else"
        notifications = NotificationState()
        await dispatcher.start_code_review_run(
            bus=_FakeBus(), notifications_state=notifications, node=node,
            node_id=node.id, bundle={}, on_success=lambda result: None,
            on_failure=lambda message: None,
        )
        return notifications, node

    notifications, node = asyncio.run(run())
    assert node.pending_request_id == "someone-else"
    assert notifications.visible is True


def test_dispatch_ask_returns_answer_text(monkeypatch):
    monkeypatch.setattr(
        agents_module, "_ask_review_lens_agent",
        lambda diff_text, question, review_summary: "It adds x.",
    )
    dispatcher = _dispatcher()
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())

    async def run():
        return await dispatcher.ask_code_review_question(
            bus=_FakeBus(), notifications_state=NotificationState(), node=node,
            question="what?", review_summary="",
        )

    assert asyncio.run(run()) == "It adds x."
    assert node.pending_request_id is None


def test_dispatch_cancel_resolves_a_claimed_run():
    import threading

    dispatcher = _dispatcher()
    handle = dispatcher._runs.claim(
        "code_review_run", node_id="n1", cancel_event=threading.Event(),
    )
    assert dispatcher.cancel_code_review(handle.request_id) is True


# -- intents -------------------------------------------------------------------


class _StubDispatcher:
    def __init__(self):
        self.calls = []

    async def fetch_code_review_diff(self, **kwargs):
        self.calls.append(("fetch", kwargs))
        return _bundle()

    async def start_code_review_run(self, **kwargs):
        self.calls.append(("run", kwargs))

    async def ask_code_review_question(self, **kwargs):
        self.calls.append(("ask", kwargs))
        return "answer-text"

    def cancel_code_review(self, request_id):
        self.calls.append(("cancel", request_id))


def _intent_bus(document, dispatcher):
    bus = SessionBus("code-review-intent-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", document.scene_payload)
    from backend.api.intents_code_review import register_code_review_intents
    register_code_review_intents(bus, document, notifications, dispatcher)
    return bus, notifications


def test_intent_fetch_stores_bundle_and_returns_node_id():
    doc, node = _doc_with_review()
    dispatcher = _StubDispatcher()
    bus, _notifications = _intent_bus(doc, dispatcher)

    async def run():
        return await bus.dispatch_intent("scene", "fetchCodeReviewDiff", [node.id, "u"])

    assert asyncio.run(run()) == node.id
    assert doc.nodes[node.id].state.code_review_repo == "o/r"
    assert doc.nodes[node.id].state.code_review_diff_version == 1


def test_intent_fetch_busy_guard_skips_dispatcher():
    doc, node = _doc_with_review()
    node.pending_request_id = "busy"
    dispatcher = _StubDispatcher()
    bus, _notifications = _intent_bus(doc, dispatcher)

    async def run():
        return await bus.dispatch_intent("scene", "fetchCodeReviewDiff", [node.id, "u"])

    assert asyncio.run(run()) is None
    assert dispatcher.calls == []


def test_intent_run_requires_a_fetched_diff():
    doc, node = _doc_with_review()
    dispatcher = _StubDispatcher()
    bus, notifications = _intent_bus(doc, dispatcher)

    async def run():
        return await bus.dispatch_intent("scene", "runCodeReview", [node.id])

    assert asyncio.run(run()) is None
    assert notifications.visible is True
    assert dispatcher.calls == []


def test_intent_ask_appends_qa():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    dispatcher = _StubDispatcher()
    bus, _notifications = _intent_bus(doc, dispatcher)

    async def run():
        return await bus.dispatch_intent("scene", "askCodeReviewQuestion", [node.id, "why?"])

    assert asyncio.run(run()) == node.id
    assert doc.nodes[node.id].state.code_review_qa == [{"question": "why?", "answer": "answer-text"}]


def test_intent_dismiss_is_undoable():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high", walkthrough=[],
        findings=[{"id": "f1"}], errors=[], scores={}, quality_score=80,
        verdict="strong", risk="low", quality_summary="S",
    )
    dispatcher = _StubDispatcher()
    bus, _notifications = _intent_bus(doc, dispatcher)

    async def run():
        await bus.dispatch_intent("scene", "dismissCodeReviewFinding", [node.id, "f1"])
        assert doc.nodes[node.id].state.code_review_dismissed_ids == ["f1"]
        doc.undo()
        assert doc.nodes[node.id].state.code_review_dismissed_ids == []

    asyncio.run(run())


# -- plugin creation -------------------------------------------------------------


def test_picker_creates_review_lens_node_and_undo_removes_it():
    bus = SessionBus("code-review-plugin-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    document = SceneDocument()
    bus.register_topic("scene", document.scene_payload)
    settings_manager = SettingsManager(Path(tempfile.mkdtemp()) / "session.dat")
    register_plugins(bus, notifications, document, settings_manager)

    async def run():
        parent = document.add_chat_node(0, 0, "p", is_user=True)
        node_id = await bus.dispatch_intent(
            "app-plugins", "executePlugin", ["Review Lens", parent.id, 0, 0],
        )
        assert document.nodes[node_id].kind == "code_review"
        document.undo()
        assert node_id not in document.nodes

    asyncio.run(run())
