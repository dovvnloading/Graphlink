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
import sys
import tempfile
from pathlib import Path

import pytest

from graphlink_wire_schema import validate_payload

# The contracts package is not importable as `contracts.*` - the wire
# dataclasses are imported by bare module name, the same path
# backend/tests/test_wire_schema_validation.py already establishes.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "contracts"))

from graphlink_scene_payload import SceneNodeRow  # noqa: E402

import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugins import register_plugins
from backend import session_load as session_load_module
from backend.session_load import restore_chat_into_document
from graphlink_plugins.review_lens import diff_fetch as diff_fetch_module
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


def _fully_populated_review_row():
    """A scene row for a node that has BOTH a fetched PR and a landed
    review - i.e. every nested list non-empty. The test above passes
    empty walkthrough/findings/errors lists, which is precisely why the
    nested-row casing bug below went unnoticed."""
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle(files=[
        {"path": "a/x.py", "status": "modified", "additions": 5, "deletions": 1,
         "patch": "@@ x", "patch_truncated": True},
        {"path": "a/new.py", "status": "renamed", "additions": 2, "deletions": 0,
         "patch": "@@ y", "patch_truncated": False, "previous_path": "a/old.py"},
    ]))
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high",
        walkthrough=[{"group_title": "G", "paths": ["a/x.py"], "explanation": "E"}],
        findings=[{"id": "f1", "severity": "medium", "tier": "yellow",
                   "category": "Maintainability", "path": "a/x.py", "line": 4,
                   "title": "T", "evidence": "E", "impact": "I", "recommendation": "R"}],
        errors=[{"id": "e1", "severity": "high", "tier": "red", "kind": "Runtime",
                 "path": "a/x.py", "line": 9, "title": "T", "evidence": "E", "fix": "F"}],
        scores={"correctness": 80}, quality_score=80, verdict="strong",
        risk="low", quality_summary="S",
    )
    doc.append_code_review_qa(node.id, "q", "a")
    return doc.scene_payload()["nodes"][-1]


def _contract_shaped(row):
    """`row` minus the one key that is deliberately on the wire without
    being declared on SceneNodeRow.

    `contentParts` is a documented, intentional omission (see
    contracts/graphlink_scene_payload.py's own module docstring): a real
    backend-only multimodal round-trip field no frontend cast reaches for.
    It is harmless in production because the GENERATED TypeScript validator
    ignores fields it does not know, while graphlink_wire_schema.py's
    validate_payload is strict about extras - so dropping it here keeps this
    assertion about Review Lens instead of re-litigating that decision. The
    failure mode this file actually needs to catch is the opposite one: a
    MISSING required field, which both validators treat as fatal."""
    return {key: value for key, value in row.items() if key != "contentParts"}


def test_a_fully_populated_review_row_validates_against_the_real_wire_contract():
    # THE test this file was missing. Every other wire assertion here reads
    # individual scalar keys, so nothing ever compared a real scene_payload()
    # row against SceneNodeRow itself - and the nested rows were shipping the
    # engine's snake_case keys (`group_title`, `patch_truncated`,
    # `previous_path`) where the contract, the generated TypeScript validator,
    # and CodeReviewNodeView all read camelCase. The generated validator
    # treats a missing required field as fatal and bindTopic.ts DROPS a
    # snapshot that fails validation, so the real-world symptom was the whole
    # canvas freezing from the first successful PR fetch onward.
    assert validate_payload(_contract_shaped(_fully_populated_review_row()), SceneNodeRow) == []


def test_wire_rows_are_camel_case_and_omit_the_per_file_patch_bodies():
    row = _fully_populated_review_row()
    assert row["codeReviewWalkthrough"] == [
        {"groupTitle": "G", "paths": ["a/x.py"], "explanation": "E"}
    ]
    assert row["codeReviewFiles"][0]["patchTruncated"] is True
    # previousPath is present only for the rename, never as "" on the rest.
    assert "previousPath" not in row["codeReviewFiles"][0]
    assert row["codeReviewFiles"][1]["previousPath"] == "a/old.py"
    # Up to ~600KB of patch text per node otherwise rides every republish,
    # and no frontend code reads it - see _code_review_file_wire's comment.
    assert [f["patch"] for f in row["codeReviewFiles"]] == ["", ""]
    assert row["codeReviewFindings"][0]["recommendation"] == "R"
    assert row["codeReviewErrors"][0]["fix"] == "F"
    assert row["codeReviewQa"] == [{"question": "q", "answer": "a"}]


def test_wire_rows_survive_junk_reaching_the_state_from_an_old_save_file():
    # Rows can reach node.state from a hand-edited or older save file, not
    # only from the engine. The wire builder must still emit a contract-shaped
    # row rather than raising mid-republish and taking the whole scene down.
    doc, node = _doc_with_review()
    doc.complete_code_review_run(
        node.id, title="T", overview="O", confidence="high",
        walkthrough=[{"unexpected": "key"}],
        findings=[{"id": "f1", "line": "not-a-number"}],
        errors=[{}], scores={}, quality_score=0, verdict="none", risk="",
        quality_summary="",
    )
    row = doc.scene_payload()["nodes"][-1]
    assert validate_payload(_contract_shaped(row), SceneNodeRow) == []
    assert row["codeReviewWalkthrough"][0]["groupTitle"] == ""
    assert row["codeReviewFindings"][0]["line"] == 0


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


# -- wrong-kind guards, now uniform ------------------------------------------
# require_node/optional_node (backend/domain/node_access.py) gave three gitlink
# methods and two fail_*_run methods a kind check they did not have. Before,
# passing a wrong-kind node id reached `node.state.<kind>_<field>` on a state
# class without that field and raised AttributeError - which the WS layer does
# not translate, unlike SceneError. These pin the new, uniform behaviour.


def test_a_wrong_kind_node_raises_scene_error_not_attribute_error():
    doc, _ = _doc_with_review()
    chat = doc.add_chat_node(0, 0, "c", is_user=True)
    for call in (
        lambda: doc.store_code_review_diff(chat.id, pr_url="u", bundle=_bundle()),
        lambda: doc.fetch_code_review_diff_text(chat.id),
        lambda: doc.append_code_review_qa(chat.id, "q", "a"),
    ):
        with pytest.raises(SceneError):
            call()


def test_fail_run_is_a_quiet_no_op_for_a_wrong_kind_node():
    """fail_*_run is documented as silent when its node has gone. A node that
    is present but of another kind is the same situation from the run's point
    of view, and used to raise AttributeError instead."""
    doc, _ = _doc_with_review()
    chat = doc.add_chat_node(0, 0, "c", is_user=True)
    assert doc.fail_code_review_run(chat.id, "boom") is None
    assert doc.fail_code_review_run("missing", "boom") is None


# -- audit regression pins ----------------------------------------------------


def _round_trip(doc, mutate):
    """build_chat_data -> mutate the code_review payload -> restore. The
    notes_data/pins_data split matches the round-trip test above."""
    chat_data = build_chat_data(doc)
    mutate(next(p for p in chat_data["nodes"] if p.get("node_type") == "code_review"))
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": chat_data}, notes_data, pins_data)
    return next(n for n in restored.nodes.values() if n.kind == "code_review")


def test_restore_enforces_the_same_caps_every_other_write_path_does():
    """Restore was the one entry point with no caps at all, so a save file
    could put an unbounded walkthrough/findings/errors/qa list straight into
    node state - and from there onto every scene republish."""
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())

    def _oversize(payload):
        payload["files"] = [{"path": f"f{i}.py"} for i in range(400)]
        payload["review"]["walkthrough"] = [{"group_title": f"g{i}"} for i in range(50)]
        payload["review"]["findings"] = [{"id": f"f{i}"} for i in range(50)]
        payload["review"]["errors"] = [{"id": f"e{i}"} for i in range(50)]
        payload["qa"] = [{"question": f"q{i}", "answer": f"a{i}"} for i in range(50)]

    node = _round_trip(doc, _oversize)
    assert len(node.state.code_review_files) == 100
    assert len(node.state.code_review_walkthrough) == 8
    assert len(node.state.code_review_findings) == 12
    assert len(node.state.code_review_errors) == 10
    # The MOST RECENT 20, matching append_code_review_qa - restoring the
    # first 20 would silently reverse which turns survive a reload.
    assert len(node.state.code_review_qa) == 20
    assert node.state.code_review_qa[-1]["question"] == "q49"


def test_restore_drops_non_dict_rows_instead_of_raising():
    doc, node = _doc_with_review()
    doc.store_code_review_diff(node.id, pr_url="u", bundle=_bundle())

    def _junk(payload):
        payload["review"]["findings"] = ["not a dict", None, {"id": "f1"}]

    node = _round_trip(doc, _junk)
    assert node.state.code_review_findings == [{"id": "f1"}]


def test_the_restore_caps_match_the_caps_the_domain_enforces():
    """The two live as literals in different modules on purpose (the domain
    must not import from session_load or vice versa). This is what stops
    them drifting apart unnoticed."""
    doc, node = _doc_with_review()
    doc.complete_code_review_run(
        node.id, title="", overview="", confidence="",
        walkthrough=[{"group_title": f"g{i}"} for i in range(50)],
        findings=[{"id": f"f{i}"} for i in range(50)],
        errors=[{"id": f"e{i}"} for i in range(50)],
        scores={}, quality_score=0, verdict="none", risk="", quality_summary="",
    )
    live = doc.nodes[node.id]
    assert len(live.state.code_review_walkthrough) == session_load_module._CODE_REVIEW_MAX_WALKTHROUGH
    assert len(live.state.code_review_findings) == session_load_module._CODE_REVIEW_MAX_FINDINGS
    assert len(live.state.code_review_errors) == session_load_module._CODE_REVIEW_MAX_ERRORS
    for _ in range(30):
        doc.append_code_review_qa(node.id, "q", "a")
    assert len(doc.nodes[node.id].state.code_review_qa) == session_load_module._CODE_REVIEW_MAX_QA


def test_the_diff_fetch_watchdog_outlasts_the_network_timeouts_it_bounds():
    """A fetch makes up to three 25s REST calls plus one 60s diff download,
    so it can legally take 135s. The watchdog was 120, and reported a merely
    slow fetch as "stopped responding" while the request it gave up on was
    still running."""
    inner_budget = (3 * 25) + diff_fetch_module._DIFF_TIMEOUT_SECONDS
    assert agents_module.CODE_REVIEW_DIFF_TIMEOUT_SECONDS > inner_budget


def test_cancelling_a_fetch_or_ask_says_so_instead_of_doing_nothing_silently():
    """The node offers Cancel for ANY pending request, but only a review RUN
    is registered as cancellable - fetch and Ask claim the busy marker
    through _run_node_blocking_action, which mints a bare uuid4 the registry
    never sees. The intent used to drop that False on the floor."""
    class _UncancellableDispatcher(_StubDispatcher):
        def cancel_code_review(self, request_id):
            super().cancel_code_review(request_id)
            return False

    doc, _node = _doc_with_review()
    bus, notifications = _intent_bus(doc, _UncancellableDispatcher())
    asyncio.run(bus.dispatch_intent("scene", "cancelCodeReviewRequest", ["req-not-a-run"]))
    assert "Only a running review can be cancelled" in notifications.message
