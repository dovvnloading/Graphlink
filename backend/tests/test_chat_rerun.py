"""Batch chat re-run: canonical scope, preview guards, versions, cancellation and undo."""

from __future__ import annotations

import asyncio

import pytest

import backend.agents as agents_module  # noqa: F401 - backend.canvas import-order seam
from backend.api import intents_chat_rerun
from backend.api.intents_chat_rerun import _preview_plan, register_chat_rerun_intents
from backend.canvas import SceneDocument
from backend.domain.model import SceneError
from backend.domain.staleness import fingerprint_for_reply, scene_staleness


class _Bus:
    def __init__(self):
        self.intents = {}
        self.published = []

    def register_intent(self, topic, intent, handler):
        self.intents[(topic, intent)] = handler

    async def publish(self, topic):
        self.published.append(topic)


class _Counter:
    def set_context_text(self, _text, *, image_tokens=0):
        pass

    def reset_real_usage(self):
        pass

    def set_output_text(self, _text):
        pass


class _Runs:
    def is_busy(self, kind):
        return False


class _Dispatcher:
    _settings_manager = None

    def __init__(self, *, hold_call=0, fail_call=0):
        self._runs = _Runs()
        self.calls = []
        self.cancelled = set()
        self.hold_call = hold_call
        self.fail_call = fail_call
        self.release = asyncio.Event()

    def active_provider_model(self, model_ref=None):
        return "test-provider", "test-model"

    def _resolve_model_ref_for_dispatch(self, document, parent_id, bus=None):
        return None

    def cancel(self, request_id):
        self.cancelled.add(request_id)
        self.release.set()
        return True

    async def start_chat_reply(self, **kwargs):
        number = len(self.calls) + 1
        self.calls.append(kwargs["conversation_history"])
        request_id = f"request-{number}"

        async def finish():
            kwargs["on_begin"](request_id)
            try:
                if number == self.hold_call:
                    await self.release.wait()
                if request_id not in self.cancelled and number != self.fail_call:
                    await kwargs["on_reply"](f"new reply {number}")
            finally:
                kwargs["on_end"]()

        asyncio.create_task(finish())


def _thread():
    document = SceneDocument()
    user = document.add_chat_node(0, 0, "original prompt", True)
    first = document.add_chat_node(0, 250, "first answer", False, parent_id=user.id)
    next_user = document.add_chat_node(0, 500, "follow-up", True, parent_id=first.id)
    deep = document.add_chat_node(0, 750, "deep answer", False, parent_id=next_user.id)
    branch = document.add_chat_node(300, 250, "branch answer", False, parent_id=user.id)
    separate = document.add_chat_node(900, 0, "other root", True)
    unrelated = document.add_chat_node(900, 250, "unrelated", False, parent_id=separate.id)
    code = document.add_code_node(200, 350, "print(1)", "python", parent_id=first.id)
    # Snapshot request-time fingerprints before the edit.
    for reply, parent in ((first, user), (deep, next_user), (branch, user), (unrelated, separate)):
        reply.state.provider = "test-provider"
        reply.state.model = "test-model"
        reply.state.reply_history_fingerprint = fingerprint_for_reply(
            document, parent.id, "test-provider", "test-model", False,
        )
        reply.state.reply_knowledge_enabled = False
        document.sync_chat_version(reply.id)
    document.last_chat_node_id = deep.id
    document.edit_chat_message(user.id, "edited prompt", "original prompt")
    return document, user, first, deep, branch, unrelated, code


def _register(document, dispatcher):
    bus = _Bus()

    async def publish_scene():
        await bus.publish("scene")

    async def publish_counter():
        return None

    register_chat_rerun_intents(
        bus, document, object(), dispatcher, object(),
        _Counter(), publish_scene, publish_counter,
    )
    return {key[1]: handler for key, handler in bus.intents.items()}, bus


async def _status(intents, job_id):
    for _ in range(200):
        state = await intents["getChatRerunStatus"](job_id)
        if state["status"] != "running":
            return state
        await asyncio.sleep(0.001)
    raise AssertionError("chat rerun did not reach a terminal state")


@pytest.fixture(autouse=True)
def _skip_knowledge_lookup(monkeypatch):
    async def no_knowledge(_document, _parent_id):
        return False

    monkeypatch.setattr(intents_chat_rerun, "_use_knowledge_for_node", no_knowledge)


def test_preview_canonical_scopes_order_models_tokens_and_nonchat_isolation():
    document, user, first, deep, branch, unrelated, code = _thread()
    dispatcher = _Dispatcher()
    this_path = _preview_plan(document, dispatcher, None, user.id, "this_path")
    all_branches = _preview_plan(document, dispatcher, None, user.id, "all_branches_below")
    assert [row["nodeId"] for row in this_path["replies"]] == [first.id, deep.id]
    assert [row["nodeId"] for row in all_branches["replies"]] == [first.id, deep.id, branch.id]
    assert all(row["model"] == "test-model" for row in all_branches["replies"])
    assert all(row["estimatedInputTokens"] > 0 for row in all_branches["replies"])
    assert code.id not in {row["nodeId"] for row in all_branches["replies"]}
    assert unrelated.id not in {row["nodeId"] for row in all_branches["replies"]}
    assert scene_staleness(document)[first.id] == "out_of_date"
    assert scene_staleness(document)[deep.id] == "out_of_date"

    # This path follows the currently active branch, even when the active
    # child is not the first edge. All-branches still includes every fork.
    document.last_chat_node_id = branch.id
    other_path = _preview_plan(document, dispatcher, None, user.id, "this_path")
    assert [row["nodeId"] for row in other_path["replies"]] == [branch.id]


def test_preview_input_cost_reprices_with_current_exact_model_override():
    document, user, *_ = _thread()
    dispatcher = _Dispatcher()

    class Pricing:
        prices = {"test-model": {"input": 2.0, "output": 6.0}}

        def get_pricing_overrides(self):
            return self.prices

    settings = Pricing()
    dispatcher._settings_manager = settings
    first = _preview_plan(document, dispatcher, None, user.id, "this_path")
    row = first["replies"][0]
    assert row["estimatedInputCostUsd"] == round(row["estimatedInputTokens"] * 2.0 / 1_000_000, 6)
    settings.prices = {"test-model": {"input": 300.0, "output": 600.0}}
    second = _preview_plan(document, dispatcher, None, user.id, "this_path")
    assert second["replies"][0]["estimatedInputCostUsd"] > row["estimatedInputCostUsd"]
    assert second["previewToken"] != first["previewToken"]


def test_only_edited_user_or_stale_reply_may_start():
    document = SceneDocument()
    user = document.add_chat_node(0, 0, "unedited", True)
    clean = document.add_chat_node(0, 100, "clean", False, parent_id=user.id)
    dispatcher = _Dispatcher()
    with pytest.raises(SceneError, match="edited user"):
        _preview_plan(document, dispatcher, None, user.id, "this_path")
    with pytest.raises(SceneError, match="out-of-date"):
        _preview_plan(document, dispatcher, None, clean.id, "this_path")
    with pytest.raises(SceneError, match="scope"):
        _preview_plan(document, dispatcher, None, user.id, "arbitrary")


def test_successful_batch_versions_refreshes_fingerprints_with_single_undo():
    async def exercise():
        document, user, first, deep, branch, unrelated, code = _thread()
        dispatcher = _Dispatcher()
        intents, bus = _register(document, dispatcher)
        before = {node.id: node.content for node in (first, deep, branch, unrelated, code)}
        before_versions = {node.id: len(node.state.versions) for node in (first, deep, branch)}
        before_edges = set(document.edges)
        plan = await intents["previewChatRerun"](user.id, "all_branches_below")
        started = await intents["startChatRerun"](
            user.id, "all_branches_below", plan["previewToken"], False,
        )
        ended = await _status(intents, started["jobId"])
        assert (ended["status"], ended["completed"], ended["total"]) == ("completed", 3, 3)
        assert [history[-1]["content"] for history in dispatcher.calls] == [
            "edited prompt", "follow-up", "edited prompt",
        ]
        assert [document.nodes[n.id].content for n in (first, deep, branch)] == [
            "new reply 1", "new reply 2", "new reply 3",
        ]
        assert all(len(document.nodes[n.id].state.versions) == before_versions[n.id] + 1
                   for n in (first, deep, branch))
        assert all(scene_staleness(document).get(n.id) != "out_of_date" for n in (first, deep, branch))
        assert document.nodes[code.id].content == before[code.id]
        assert document.nodes[unrelated.id].content == before[unrelated.id]
        assert set(document.edges) == before_edges
        assert len(document.command_log) == 1
        assert document.command_log[-1].command_type == "rerunFromHere"
        document.undo()
        assert all(document.nodes[n.id].content == before[n.id] for n in (first, deep, branch))
        assert scene_staleness(document).get(first.id) == "out_of_date"
        document.redo()
        assert document.nodes[deep.id].content == "new reply 2"
        assert "scene" in bus.published

    asyncio.run(exercise())


def test_cancel_during_second_reply_preserves_second_answer_and_single_undo():
    async def exercise():
        document, user, first, deep, branch, _, code = _thread()
        dispatcher = _Dispatcher(hold_call=2)
        intents, _ = _register(document, dispatcher)
        plan = await intents["previewChatRerun"](user.id, "all_branches_below")
        started = await intents["startChatRerun"](
            user.id, "all_branches_below", plan["previewToken"], False,
        )
        job_id = started["jobId"]
        for _ in range(200):
            status = await intents["getChatRerunStatus"](job_id)
            if status["completed"] == 1 and status["currentNodeId"] == deep.id:
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("did not reach the second reply")
        assert (await intents["cancelChatRerun"](job_id))["ok"] is True
        ended = await _status(intents, job_id)
        assert ended["status"] == "cancelled"
        assert ended["completed"] == 1
        assert len(dispatcher.calls) == 2
        assert document.nodes[first.id].content == "new reply 1"
        assert document.nodes[deep.id].content == "deep answer"
        assert document.nodes[branch.id].content == "branch answer"
        assert document.nodes[code.id].state.code == "print(1)"
        assert len(document.command_log) == 1
        document.undo()
        assert document.nodes[first.id].content == "first answer"

    asyncio.run(exercise())


def test_rejects_stale_preview_and_requires_explicit_over_ten_confirmation():
    async def exercise():
        document = SceneDocument()
        user = document.add_chat_node(0, 0, "initial", True)
        children = [document.add_chat_node(i * 100, 100, f"answer {i}", False, parent_id=user.id)
                    for i in range(11)]
        document.edit_chat_message(user.id, "edited", "initial")
        dispatcher = _Dispatcher()
        intents, _ = _register(document, dispatcher)
        preview = await intents["previewChatRerun"](user.id, "all_branches_below")
        assert preview["count"] == 11 and preview["requiresConfirmation"]
        with pytest.raises(SceneError, match="Confirm explicitly"):
            await intents["startChatRerun"](
                user.id, "all_branches_below", preview["previewToken"], False,
            )
        assert not dispatcher.calls
        document.edit_chat_message(user.id, "edited again", "edited")
        with pytest.raises(SceneError, match="changed since"):
            await intents["startChatRerun"](
                user.id, "all_branches_below", preview["previewToken"], True,
            )
        assert [child.content for child in children] == [f"answer {i}" for i in range(11)]

    asyncio.run(exercise())


def test_provider_interruption_keeps_original_reply_and_no_undo_entry():
    async def exercise():
        document, user, first, deep, _, _, _ = _thread()
        dispatcher = _Dispatcher(fail_call=1)
        previous_fingerprint = document.nodes[first.id].state.reply_history_fingerprint
        intents, _ = _register(document, dispatcher)
        preview = await intents["previewChatRerun"](user.id, "this_path")
        started = await intents["startChatRerun"](
            user.id, "this_path", preview["previewToken"], False,
        )
        final = await _status(intents, started["jobId"])
        assert final["status"] == "failed"
        assert final["completed"] == 0
        assert document.nodes[first.id].content == "first answer"
        assert document.nodes[first.id].state.reply_history_fingerprint == previous_fingerprint
        assert document.nodes[deep.id].content == "deep answer"
        assert len(document.command_log) == 0

    asyncio.run(exercise())

def test_reply_re_run_respects_twenty_version_cap_without_touching_other_kinds():
    from backend.api.intents_chat_rerun import _apply_reply
    from backend.domain.node_states import MAX_CHAT_VERSIONS

    document, user, first, _, _, _, code = _thread()
    for i in range(24):
        document.update_chat_node_content(first.id, f"alternative {i}")
    prior = document.nodes[first.id].content
    _apply_reply(document, first.id, "fresh answer", "provider", "model", "v1:proof", False)
    result = document.nodes[first.id]
    assert len(result.state.versions) == MAX_CHAT_VERSIONS == 20
    assert result.state.versions[-2].content == prior
    assert result.state.versions[-1].content == "fresh answer"
    assert result.state.version_index == 19
    assert result.state.reply_history_fingerprint == "v1:proof"
    assert document.nodes[code.id].state.code == "print(1)"


def test_side_effects_and_explicit_bindings_are_skipped_even_without_structural_edges():
    """Only ordinary chat enters a batch; no tool, agent, plugin or runner step dispatches."""
    async def exercise():
        document = SceneDocument()
        user = document.add_chat_node(0, 0, "original", True)
        ordinary = document.add_chat_node(0, 150, "ordinary answer", False, parent_id=user.id)
        tool = document.add_chat_node(200, 150, "tool output", False, parent_id=user.id)
        tool.state.tool_invocations = [{"name": "write_workspace_file", "result": "done"}]
        after_tool = document.add_chat_node(200, 300, "uses tool output", False, parent_id=tool.id)
        agent = document.add_harness_node(400, 150, "modify workspace")
        builder = document.add_plan_node(600, 150, "build project")
        document.connect(user.id, agent.id)
        document.connect(user.id, builder.id)
        sandbox = document.add_code_sandbox_node(800, 150, user.id)
        plugin = document.add_plugin_node(
            "example.execute_action", 1000, 150, user.id, title="Post to service",
        )
        # Data dependencies have no structural edges: they must still be
        # listed as skipped when their original source message was edited.
        model = document.add_model_node(user.id, "Ollama", "qwen3:8b")
        note = document.add_note(1200, 150)
        document.bind_note_text_input(user.id, note.id, "word_count")
        model_note = document.add_note(1400, 150)
        document.bind_note_model_input(user.id, model_note.id, "Ollama", "qwen3:8b")
        source_table = document.add_table(1600, 150, "Group,Value\na,1\na,2")
        document.connect(user.id, source_table.id)
        table_output = document.create_table_output(source_table.id, "table", "Group", "Value")
        document.last_chat_node_id = ordinary.id
        document.edit_chat_message(user.id, "changed", "original")

        dispatcher = _Dispatcher()
        intents, _ = _register(document, dispatcher)
        before = {node.id: node.content for node in document.nodes.values()}
        plan = await intents["previewChatRerun"](user.id, "all_branches_below")
        assert [row["nodeId"] for row in plan["replies"]] == [ordinary.id]
        skipped = {row["nodeId"]: row for row in plan["skipped"]}
        for node in (tool, after_tool, agent, builder, sandbox, plugin, source_table):
            assert skipped[node.id]["category"] == "manual"
            assert skipped[node.id]["reason"]
        assert "executed tools" in skipped[tool.id]["reason"]
        for node in (model, note, model_note, table_output):
            assert skipped[node.id]["category"] == "bound"
            assert "Graph Runner" in skipped[node.id]["reason"]
        assert plan["requiresConfirmation"] is False
        started = await intents["startChatRerun"](
            user.id, "all_branches_below", plan["previewToken"], False,
        )
        done = await _status(intents, started["jobId"])
        assert (done["status"], done["completed"], done["total"]) == ("completed", 1, 1)
        assert len(dispatcher.calls) == 1
        assert ordinary.content == "new reply 1"
        for node_id, content in before.items():
            if node_id != ordinary.id:
                assert document.nodes[node_id].content == content
        assert len(document.command_log) == 1
        # Structured provenance survives the batch for every skipped
        # side-effecting kind; the backend publishes a manual warning.
        staleness = scene_staleness(document)
        for node in (agent, builder, sandbox, plugin, source_table):
            assert staleness[node.id] == "upstream_changed"

    asyncio.run(exercise())


def test_tool_calls_added_after_preview_are_rejected_and_never_dispatched():
    async def exercise():
        document = SceneDocument()
        user = document.add_chat_node(0, 0, "before", True)
        reply = document.add_chat_node(0, 150, "reply", False, parent_id=user.id)
        document.edit_chat_message(user.id, "after", "before")
        dispatcher = _Dispatcher()
        intents, _ = _register(document, dispatcher)
        preview = await intents["previewChatRerun"](user.id, "all_branches_below")
        reply.state.tool_invocations = [{"name": "shell", "result": "executed"}]
        with pytest.raises(SceneError, match="changed since"):
            await intents["startChatRerun"](
                user.id, "all_branches_below", preview["previewToken"], False,
            )
        assert dispatcher.calls == []
        assert reply.content == "reply"

    asyncio.run(exercise())


def test_tool_metadata_only_is_not_executed_but_citation_marker_remains_safe():
    from backend.context_engine.citations import CITATION_RECORD_NAME

    document = SceneDocument()
    user = document.add_chat_node(0, 0, "prompt", True)
    cited = document.add_chat_node(0, 150, "cited text", False, parent_id=user.id)
    cited.state.tool_invocations = [{"name": CITATION_RECORD_NAME}]
    document.edit_chat_message(user.id, "edited", "prompt")
    plan = _preview_plan(document, _Dispatcher(), None, user.id, "all_branches_below")
    assert [row["nodeId"] for row in plan["replies"]] == [cited.id]
    assert plan["skipped"] == []


def test_execution_rechecks_tool_boundary_when_a_running_job_mutates():
    async def exercise():
        document = SceneDocument()
        user = document.add_chat_node(0, 0, "prompt", True)
        first = document.add_chat_node(0, 150, "first", False, parent_id=user.id)
        second = document.add_chat_node(0, 300, "second", False, parent_id=first.id)
        document.edit_chat_message(user.id, "edited", "prompt")

        class ChangingDispatcher(_Dispatcher):
            async def start_chat_reply(self, **kwargs):
                await super().start_chat_reply(**kwargs)
                if len(self.calls) == 1:
                    second.state.tool_invocations = [{"name": "execute_plugin_action"}]

        dispatcher = ChangingDispatcher()
        intents, _ = _register(document, dispatcher)
        plan = await intents["previewChatRerun"](user.id, "all_branches_below")
        assert len(plan["replies"]) == 2
        started = await intents["startChatRerun"](
            user.id, "all_branches_below", plan["previewToken"], False,
        )
        done = await _status(intents, started["jobId"])
        assert done["status"] == "failed"
        assert done["completed"] == 1
        assert len(dispatcher.calls) == 1
        assert second.content == "second"

    asyncio.run(exercise())

def test_preview_hashes_a_branch_once_not_once_per_reply():
    from unittest.mock import patch

    import backend.domain.staleness as staleness

    document, user, *_ = _thread()
    with patch.object(staleness, "_message_input", wraps=staleness._message_input) as hashed:
        preview = _preview_plan(document, _Dispatcher(), None, user.id, "all_branches_below")
    assert preview["count"] == 3
    # One pass to determine stale targets and one to snapshot ALL history
    # digests. A per-reply lineage walk makes this four passes instead.
    assert hashed.call_count == 2 * len(document.nodes)


def test_large_preview_and_start_validation_yield_the_event_loop():
    async def exercise():
        document, user, *_ = _thread()
        intents, _ = _register(document, _Dispatcher())
        tick = asyncio.create_task(asyncio.sleep(0))
        preview = await intents["previewChatRerun"](user.id, "all_branches_below")
        assert tick.done(), "preview tokenization blocked the event loop"
        assert preview["count"] == 3

        tick = asyncio.create_task(asyncio.sleep(0))
        with pytest.raises(SceneError, match="changed since the preview"):
            await intents["startChatRerun"](user.id, "this_path", "old-token", False)
        assert tick.done(), "start-time preview validation blocked the event loop"

    asyncio.run(exercise())

