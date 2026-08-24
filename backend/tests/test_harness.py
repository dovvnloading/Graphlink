"""PLAN-2026-08-24 H1: the harness workspace, transcript, fs tools, and
turn loop.

The loop's provider seam is scripted per test (the test_builder pattern
exactly); everything else is real - real SceneDocument, real ToolRegistry
with the real fs handlers, a real RunRegistry handle, the real
run_harness coroutine, and a real on-disk workspace under tmp_path."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

import api_provider
from backend.domain.graph import SceneDocument
from backend.harness import context as context_module
from backend.harness import transcript as transcript_module
from backend.harness import workspace as workspace_module
from backend.harness.loop import HarnessRunContext, run_harness
from backend.harness.tools_fs import register_harness_fs_tools
from backend.harness.transcript import append_message, load_messages, transcript_path
from backend.harness.workspace import WorkspaceError, ensure_workspace, resolve_in_workspace
from backend.providers.base import ToolCall
from backend.run_lifecycle import RunRegistry
from backend.session_load import _restore_harness_payload
from backend.session_save import _serialize_harness_node
from backend.tools import FS_READ, KNOWLEDGE_READ, ToolRegistry


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "harness_root"
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", root)
    return root


class FakeBus:
    def __init__(self):
        self.published: list[str] = []

    async def publish(self, topic: str):
        self.published.append(topic)


class LoopDispatcher:
    def __init__(self):
        self._runs = RunRegistry()


def scripted_turns(monkeypatch, turns: list[dict]):
    remaining = list(turns)

    def fake_turn(task, messages, tools=(), **kwargs):
        if not remaining:
            raise AssertionError("script exhausted - the loop asked for one more turn than scripted")
        entry = remaining.pop(0)
        return {
            "message": {"content": entry.get("content", ""), "role": "assistant"},
            "tool_calls": entry.get("tool_calls", []),
            "usage": entry.get("usage"),
        }

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", fake_turn)


def call(cid, name, **arguments):
    return ToolCall(id=cid, name=name, arguments=arguments)


def make_ctx(workspace_id: str) -> HarnessRunContext:
    async def deny(_call):
        return False

    return HarnessRunContext(
        granted_scopes=frozenset({FS_READ, KNOWLEDGE_READ}),
        request_approval=deny,
        harness_workspace_id=workspace_id,
    )


async def drive(document, dispatcher, registry, bus, node, user_text):
    cancel_event = threading.Event()
    handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=cancel_event)
    try:
        await run_harness(
            document=document, dispatcher=dispatcher, registry=registry,
            bus=bus, notifications=None, harness_node_id=node.id,
            user_text=user_text, request_id=handle.request_id, handle=handle,
            cancel_event=cancel_event,
        )
    finally:
        dispatcher._runs.release(handle.request_id)
    return handle.request_id


class TestWorkspace:
    def test_relative_paths_resolve_inside(self, workspace_root):
        ensure_workspace("ws1")
        resolved = resolve_in_workspace("ws1", "sub/file.txt")
        assert str(resolved).startswith(str((workspace_root / "ws1").resolve()))

    def test_traversal_is_refused(self, workspace_root):
        ensure_workspace("ws1")
        with pytest.raises(WorkspaceError):
            resolve_in_workspace("ws1", "../other")

    def test_absolute_outside_is_refused(self, workspace_root, tmp_path):
        ensure_workspace("ws1")
        with pytest.raises(WorkspaceError):
            resolve_in_workspace("ws1", str(tmp_path / "elsewhere.txt"))

    def test_blank_workspace_id_is_refused(self, workspace_root):
        with pytest.raises(WorkspaceError):
            resolve_in_workspace("", "file.txt")


class TestTranscript:
    def test_roundtrip_and_meta_line(self, workspace_root):
        ws = ensure_workspace("ws1")
        append_message(ws, {"role": "user", "content": "hello"})
        append_message(ws, {"role": "assistant", "content": "hi"})
        lines = transcript_path(ws).read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["t"] == "meta"
        assert load_messages(ws) == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_corrupt_lines_are_skipped_not_fatal(self, workspace_root):
        ws = ensure_workspace("ws1")
        append_message(ws, {"role": "user", "content": "kept"})
        with transcript_path(ws).open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write(json.dumps({"t": "msg", "role": "bogus-role", "content": "dropped"}) + "\n")
        assert [m["content"] for m in load_messages(ws)] == ["kept"]

    def test_reload_tail_is_bounded_and_never_starts_on_a_tool_message(self, workspace_root, monkeypatch):
        monkeypatch.setattr(transcript_module, "MAX_RELOADED_MESSAGES", 2)
        ws = ensure_workspace("ws1")
        append_message(ws, {"role": "user", "content": "u1"})
        append_message(ws, {"role": "assistant", "content": "a1", "tool_calls": [{"id": "t1"}]})
        append_message(ws, {"role": "tool", "tool_call_id": "t1", "content": "r1"})
        append_message(ws, {"role": "assistant", "content": "a2"})
        loaded = load_messages(ws)
        # The 2-message tail starts on the orphaned tool result (its
        # assistant tool_calls turn fell outside the window) - it must be
        # dropped so history never opens mid tool-sequence.
        assert [m["role"] for m in loaded] == ["assistant"]
        assert loaded[0]["content"] == "a2"


class TestFsTools:
    def _registry(self):
        registry = ToolRegistry()
        register_harness_fs_tools(registry)
        return registry

    def test_read_list_grep_roundtrip(self, workspace_root):
        ws = ensure_workspace("ws1")
        (ws / "notes").mkdir()
        (ws / "notes" / "a.txt").write_text("alpha\nbeta target line\n", encoding="utf-8")
        registry = self._registry()
        ctx = make_ctx("ws1")

        async def go():
            listing = await registry.invoke(call("1", "fs.list", pattern="**/*.txt"), ctx)
            read = await registry.invoke(call("2", "fs.read", path="notes/a.txt"), ctx)
            grep = await registry.invoke(call("3", "fs.grep", pattern="target"), ctx)
            return listing, read, grep

        listing, read, grep = asyncio.run(go())
        assert not listing.is_error and "notes/a.txt" in listing.content
        assert not read.is_error and "beta target line" in read.content
        assert not grep.is_error and "notes/a.txt:2:" in grep.content

    def test_escape_attempts_come_back_as_tool_errors(self, workspace_root):
        ensure_workspace("ws1")
        registry = self._registry()
        ctx = make_ctx("ws1")

        async def go():
            return await registry.invoke(call("1", "fs.read", path="../secret.txt"), ctx)

        result = asyncio.run(go())
        assert result.is_error and "outside" in result.content

    def test_read_output_is_capped(self, workspace_root, monkeypatch):
        from backend.harness import tools_fs as tools_fs_module

        monkeypatch.setattr(tools_fs_module, "_READ_CAP_CHARS", 50)
        ws = ensure_workspace("ws1")
        (ws / "big.txt").write_text("x" * 500, encoding="utf-8")
        registry = self._registry()

        async def go():
            return await registry.invoke(call("1", "fs.read", path="big.txt"), make_ctx("ws1"))

        result = asyncio.run(go())
        assert not result.is_error and "truncated" in result.content
        assert len(result.content) < 300

    def test_invalid_regex_is_a_tool_error(self, workspace_root):
        ensure_workspace("ws1")
        registry = self._registry()

        async def go():
            return await registry.invoke(call("1", "fs.grep", pattern="("), make_ctx("ws1"))

        result = asyncio.run(go())
        assert result.is_error and "regular expression" in result.content

    def test_missing_workspace_binding_is_a_tool_error(self, workspace_root):
        registry = self._registry()

        async def go():
            ctx = make_ctx("ws1")
            ctx.harness_workspace_id = None
            return await registry.invoke(call("1", "fs.list"), ctx)

        result = asyncio.run(go())
        assert result.is_error


class TestLoop:
    def _make(self, workspace_root):
        document = SceneDocument()
        node = document.add_harness_node(0, 0, "inspect the workspace")
        registry = ToolRegistry()
        register_harness_fs_tools(registry)
        return document, LoopDispatcher(), registry, FakeBus(), node

    def test_tool_using_task_lands_done_with_reply_and_transcript(self, workspace_root, monkeypatch):
        document, dispatcher, registry, bus, node = self._make(workspace_root)
        ws = ensure_workspace(node.state.harness_workspace_id)
        (ws / "data.txt").write_text("payload", encoding="utf-8")
        scripted_turns(monkeypatch, [
            {"content": "looking", "tool_calls": [call("t1", "fs.read", path="data.txt")],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
            {"content": "The file contains: payload", "usage": {"prompt_tokens": 20, "completion_tokens": 7}},
        ])
        asyncio.run(drive(document, dispatcher, registry, bus, node, "what's in data.txt?"))
        assert node.state.harness_status == "done"
        assert node.state.harness_reply == "The file contains: payload"
        assert node.state.harness_spent_turns == 2
        assert node.state.harness_spent_tokens == 42
        assert [row["tool"] for row in node.state.harness_activity] == ["fs.read"]
        assert node.pending_request_id is None
        roles = [m["role"] for m in load_messages(ws)]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_follow_up_reloads_history_from_the_transcript(self, workspace_root, monkeypatch):
        document, dispatcher, registry, bus, node = self._make(workspace_root)
        scripted_turns(monkeypatch, [{"content": "first answer"}])
        asyncio.run(drive(document, dispatcher, registry, bus, node, "first question"))

        seen: list[list] = []

        def recording_turn(task, messages, tools=(), **kwargs):
            seen.append(list(messages))
            return {"message": {"content": "second answer", "role": "assistant"}, "tool_calls": [], "usage": None}

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", recording_turn)
        asyncio.run(drive(document, dispatcher, registry, bus, node, "second question"))
        contents = [m.get("content") for m in seen[0]]
        assert "first question" in contents and "first answer" in contents
        assert node.state.harness_reply == "second answer"

    def test_turn_cap_lands_failed_and_resumable(self, workspace_root, monkeypatch):
        document, dispatcher, registry, bus, node = self._make(workspace_root)
        node.state.harness_max_turns = 2
        endless = {"content": "", "tool_calls": [call("t", "fs.list")]}
        scripted_turns(monkeypatch, [dict(endless, tool_calls=[call(f"t{i}", "fs.list")]) for i in range(2)])
        asyncio.run(drive(document, dispatcher, registry, bus, node, "loop forever"))
        assert node.state.harness_status == "failed"
        assert "2 model turns" in node.state.harness_status_detail
        assert node.pending_request_id is None

    def test_provider_fault_lands_failed_not_crashed(self, workspace_root, monkeypatch):
        document, dispatcher, registry, bus, node = self._make(workspace_root)

        def exploding_turn(task, messages, tools=(), **kwargs):
            raise RuntimeError("rate limited")

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", exploding_turn)
        asyncio.run(drive(document, dispatcher, registry, bus, node, "hello"))
        assert node.state.harness_status == "failed"
        assert "rate limited" in node.state.harness_status_detail

    def test_offered_specs_stay_inside_the_grant_set(self, workspace_root, monkeypatch):
        """A net.fetch-scoped tool in the shared registry must never reach
        the harness model's schema list."""
        from backend.providers.base import ToolSpec
        from backend.tools import NET_FETCH, ToolResult

        document, dispatcher, registry, bus, node = self._make(workspace_root)

        async def fetch(call_, ctx):  # pragma: no cover - never invoked
            return ToolResult(content="")

        registry.register(
            ToolSpec(name="net.fetch_page", description="x", input_schema={"type": "object", "properties": {}}),
            fetch, scopes={NET_FETCH}, approval="always",
        )
        offered: list = []

        def recording_turn(task, messages, tools=(), **kwargs):
            offered.append([t.name for t in tools])
            return {"message": {"content": "done", "role": "assistant"}, "tool_calls": [], "usage": None}

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", recording_turn)
        asyncio.run(drive(document, dispatcher, registry, bus, node, "hi"))
        assert offered and "net.fetch_page" not in offered[0]
        assert "fs.read" in offered[0]


class TestWriteTools:
    def _registry(self):
        registry = ToolRegistry()
        register_harness_fs_tools(registry)
        return registry

    def _write_ctx(self, workspace_id="ws1", approve=True):
        from backend.tools import FS_WRITE

        decisions = []

        async def approver(call_):
            decisions.append(call_.name)
            return approve

        ctx = HarnessRunContext(
            granted_scopes=frozenset({FS_READ, FS_WRITE}),
            request_approval=approver,
            harness_workspace_id=workspace_id,
        )
        return ctx, decisions

    def test_write_then_edit_roundtrip_prompts_once_each(self, workspace_root):
        ws = ensure_workspace("ws1")
        registry = self._registry()
        ctx, decisions = self._write_ctx()

        async def go():
            wrote = await registry.invoke(call("1", "fs.write", path="out/a.txt", content="alpha beta"), ctx)
            edited = await registry.invoke(
                call("2", "fs.edit", path="out/a.txt", old_string="beta", new_string="gamma"), ctx,
            )
            return wrote, edited

        wrote, edited = asyncio.run(go())
        assert not wrote.is_error and not edited.is_error
        assert (ws / "out" / "a.txt").read_text(encoding="utf-8") == "alpha gamma"
        assert decisions == ["fs.write", "fs.edit"]

    def test_denied_write_leaves_disk_untouched(self, workspace_root):
        ws = ensure_workspace("ws1")
        registry = self._registry()
        ctx, _ = self._write_ctx(approve=False)

        async def go():
            return await registry.invoke(call("1", "fs.write", path="a.txt", content="x"), ctx)

        result = asyncio.run(go())
        assert result.is_error and "denied" in result.content
        assert not (ws / "a.txt").exists()

    def test_edit_refuses_zero_and_ambiguous_matches(self, workspace_root):
        ws = ensure_workspace("ws1")
        (ws / "a.txt").write_text("dup dup", encoding="utf-8")
        registry = self._registry()
        ctx, _ = self._write_ctx()

        async def go():
            missing = await registry.invoke(
                call("1", "fs.edit", path="a.txt", old_string="absent", new_string="x"), ctx,
            )
            ambiguous = await registry.invoke(
                call("2", "fs.edit", path="a.txt", old_string="dup", new_string="x"), ctx,
            )
            return missing, ambiguous

        missing, ambiguous = asyncio.run(go())
        assert missing.is_error and "not found" in missing.content
        assert ambiguous.is_error and "2 times" in ambiguous.content
        assert (ws / "a.txt").read_text(encoding="utf-8") == "dup dup"

    def test_write_escape_is_refused(self, workspace_root):
        ensure_workspace("ws1")
        registry = self._registry()
        ctx, _ = self._write_ctx()

        async def go():
            return await registry.invoke(call("1", "fs.write", path="../evil.txt", content="x"), ctx)

        result = asyncio.run(go())
        assert result.is_error and "outside" in result.content


class TestShellTool:
    def _setup(self, workspace_id="ws1", approve=True):
        from backend.harness.tools_shell import register_harness_shell_tool
        from backend.tools import CODE_EXECUTE

        registry = ToolRegistry()
        register_harness_shell_tool(registry)

        async def approver(call_):
            return approve

        ctx = HarnessRunContext(
            granted_scopes=frozenset({CODE_EXECUTE}),
            request_approval=approver,
            harness_workspace_id=workspace_id,
        )
        return registry, ctx

    def test_command_runs_in_the_workspace_and_reports_exit_code(self, workspace_root):
        ws = ensure_workspace("ws1")
        (ws / "hello.txt").write_text("shell target", encoding="utf-8")
        registry, ctx = self._setup()

        async def go():
            return await registry.invoke(
                call("1", "shell.exec", command="python -c \"print(open('hello.txt').read())\""), ctx,
            )

        result = asyncio.run(go())
        assert not result.is_error, result.content
        assert "exit code 0" in result.content and "shell target" in result.content

    def test_env_is_allowlisted_not_inherited(self, workspace_root, monkeypatch):
        monkeypatch.setenv("GRAPHLINK_FAKE_API_KEY", "sk-secret")
        ensure_workspace("ws1")
        registry, ctx = self._setup()

        async def go():
            return await registry.invoke(
                call("1", "shell.exec",
                     command="python -c \"import os; print(os.environ.get('GRAPHLINK_FAKE_API_KEY', 'ABSENT'))\""),
                ctx,
            )

        result = asyncio.run(go())
        assert "ABSENT" in result.content and "sk-secret" not in result.content

    def test_nonzero_exit_is_an_error_result_with_output(self, workspace_root):
        ensure_workspace("ws1")
        registry, ctx = self._setup()

        async def go():
            return await registry.invoke(
                call("1", "shell.exec", command="python -c \"import sys; print('boom'); sys.exit(3)\""), ctx,
            )

        result = asyncio.run(go())
        assert result.is_error and "exit code 3" in result.content and "boom" in result.content

    def test_denied_command_never_spawns(self, workspace_root):
        ws = ensure_workspace("ws1")
        registry, ctx = self._setup(approve=False)

        async def go():
            return await registry.invoke(
                call("1", "shell.exec", command="python -c \"open('proof.txt','w').write('ran')\""), ctx,
            )

        result = asyncio.run(go())
        assert result.is_error and "denied" in result.content
        assert not (ws / "proof.txt").exists()


class TestApprovalFlow:
    def test_loop_parks_on_the_node_fields_and_approve_resumes(self, workspace_root, monkeypatch):
        from backend.harness.tools_shell import register_harness_shell_tool

        document = SceneDocument()
        node = document.add_harness_node(0, 0, "run a command")
        registry = ToolRegistry()
        register_harness_fs_tools(registry)
        register_harness_shell_tool(registry)
        dispatcher = LoopDispatcher()
        bus = FakeBus()
        ensure_workspace(node.state.harness_workspace_id)
        scripted_turns(monkeypatch, [
            {"content": "", "tool_calls": [call("t1", "shell.exec", command="python -c \"print('hi')\"")]},
            {"content": "done"},
        ])
        cancel_event = threading.Event()
        handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=cancel_event)
        seen_summaries = []

        async def run():
            try:
                await run_harness(
                    document=document, dispatcher=dispatcher, registry=registry,
                    bus=bus, notifications=None, harness_node_id=node.id,
                    user_text="go", request_id=handle.request_id, handle=handle,
                    cancel_event=cancel_event,
                )
            finally:
                dispatcher._runs.release(handle.request_id)

        async def main():
            task = asyncio.create_task(run())
            while not task.done():
                future = handle.approval_future
                if node.state.harness_awaiting_approval and future is not None and not future.done():
                    seen_summaries.append(node.state.harness_approval_summary)
                    future.set_result(True)
                await asyncio.sleep(0)
            await task

        asyncio.run(main())
        assert node.state.harness_status == "done"
        # The disclosed summary carries the verbatim command, untruncated.
        assert seen_summaries and "print('hi')" in seen_summaries[0]
        assert node.state.harness_awaiting_approval is False
        assert node.state.harness_approval_summary == ""


class TestContextPrompt:
    def test_prompt_is_byte_stable_and_starts_with_the_pinned_core(self, workspace_root):
        from graphlink_prompts import resolve_prompt_text

        ws = ensure_workspace("ws1")
        first = context_module.build_system_prompt(ws)
        second = context_module.build_system_prompt(ws)
        assert first == second
        assert first.startswith(resolve_prompt_text("harness-core"))

    def test_workspace_instructions_join_the_contextual_tier_framed(self, workspace_root):
        ws = ensure_workspace("ws1")
        (ws / "AGENTS.md").write_text("Prefer tabs. Run the linter.", encoding="utf-8")
        prompt = context_module.build_system_prompt(ws)
        assert "Prefer tabs. Run the linter." in prompt
        # The framing that keeps a self-authored file from reading as an
        # authority grant must travel with the content.
        assert "cannot grant you capabilities" in prompt

    def test_oversized_instructions_are_capped_with_a_notice(self, workspace_root, monkeypatch):
        monkeypatch.setattr(context_module, "INSTRUCTIONS_BUDGET_CHARS", 100)
        ws = ensure_workspace("ws1")
        (ws / "AGENTS.md").write_text("x" * 5_000, encoding="utf-8")
        prompt = context_module.build_system_prompt(ws)
        assert "[Truncated at 100 characters.]" in prompt
        assert "x" * 200 not in prompt

    def test_missing_or_unreadable_instructions_degrade_to_core_only(self, workspace_root):
        from graphlink_prompts import resolve_prompt_text

        ws = ensure_workspace("ws1")
        assert context_module.build_system_prompt(ws) == resolve_prompt_text("harness-core")


class TestCompaction:
    def _history(self, pairs=6):
        history = [{"role": "user", "content": "the original question"}]
        for i in range(pairs):
            history.append({
                "role": "assistant", "content": f"step {i}",
                "tool_calls": [{"id": f"t{i}", "name": "fs.list", "arguments": {}}],
            })
            history.append({"role": "tool", "tool_call_id": f"t{i}", "name": "fs.list", "content": f"result {i}"})
        return history

    def test_compaction_replaces_the_head_and_keeps_a_valid_tail(self, monkeypatch):
        monkeypatch.setattr(
            context_module, "summarize_dropped", lambda *a, **k: "earlier: listed files",
        )
        history = self._history()
        compacted, summary = context_module.compact_history(
            history, goal="do the thing", budget_tokens=200,
        )
        assert summary == "earlier: listed files"
        assert len(compacted) < len(history)
        # The replacement is one user message carrying the framing, the
        # original task, and the summary.
        assert compacted[0]["role"] == "user"
        assert "Historical reference only" in compacted[0]["content"]
        assert "do the thing" in compacted[0]["content"]
        assert "earlier: listed files" in compacted[0]["content"]
        # The tail opens on an assistant turn: never a second user message
        # in a row, never an orphaned tool result.
        assert compacted[1]["role"] == "assistant"
        assert compacted[-1] == history[-1]

    def test_compaction_is_skipped_when_the_tail_already_covers_everything(self, monkeypatch):
        called = {"n": 0}

        def spy(*a, **k):
            called["n"] += 1
            return "unused"

        monkeypatch.setattr(context_module, "summarize_dropped", spy)
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        assert context_module.compact_history(history, goal="g", budget_tokens=100_000) is None
        assert called["n"] == 0

    def test_an_empty_summary_is_treated_as_no_compaction(self, monkeypatch):
        monkeypatch.setattr(context_module, "summarize_dropped", lambda *a, **k: "   ".strip())
        assert context_module.compact_history(self._history(), goal="g", budget_tokens=200) is None

    def test_loop_compacts_over_budget_and_records_it_in_the_transcript(self, workspace_root, monkeypatch):
        document = SceneDocument()
        node = document.add_harness_node(0, 0, "long running task")
        node.state.harness_max_context_tokens = 1_000
        ws = ensure_workspace(node.state.harness_workspace_id)
        # Seed a transcript far past the budget.
        for i in range(30):
            append_message(ws, {"role": "user", "content": "filler " * 200})
            append_message(ws, {"role": "assistant", "content": "reply " * 200})
        monkeypatch.setattr(
            context_module, "summarize_dropped", lambda *a, **k: "the earlier work, summarized",
        )
        registry = ToolRegistry()
        register_harness_fs_tools(registry)
        seen: list[list] = []

        def recording_turn(task, messages, tools=(), **kwargs):
            seen.append(list(messages))
            return {"message": {"content": "final answer", "role": "assistant"}, "tool_calls": [], "usage": None}

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", recording_turn)
        asyncio.run(drive(document, LoopDispatcher(), registry, FakeBus(), node, "continue"))

        assert node.state.harness_compactions == 1
        assert node.state.harness_status == "done"
        # The turn that actually went out carried the compacted history.
        sent = seen[0]
        assert sent[0]["role"] == "system"
        assert any("the earlier work, summarized" in str(m.get("content")) for m in sent)
        assert len(sent) < 61
        # And a reload reconstructs that same post-compaction state rather
        # than replaying the dropped turns.
        reloaded = load_messages(ws)
        assert "the earlier work, summarized" in reloaded[0]["content"]
        assert not any("filler" in str(m.get("content")) for m in reloaded)

    def test_a_failed_summarizer_does_not_kill_the_run(self, workspace_root, monkeypatch):
        document = SceneDocument()
        node = document.add_harness_node(0, 0, "task")
        node.state.harness_max_context_tokens = 1_000
        ws = ensure_workspace(node.state.harness_workspace_id)
        for i in range(20):
            append_message(ws, {"role": "user", "content": "filler " * 200})
            append_message(ws, {"role": "assistant", "content": "reply " * 200})

        def exploding_summary(*a, **k):
            raise RuntimeError("summarizer down")

        monkeypatch.setattr(context_module, "summarize_dropped", exploding_summary)
        scripted_turns(monkeypatch, [{"content": "answered anyway"}])
        asyncio.run(drive(document, LoopDispatcher(), ToolRegistry(), FakeBus(), node, "go"))
        assert node.state.harness_status == "done"
        assert node.state.harness_reply == "answered anyway"
        assert node.state.harness_compactions == 0


class TestPersistence:
    def test_non_terminal_status_normalizes_to_interrupted(self):
        document = SceneDocument()
        node = document.add_harness_node(0, 0, "goal")
        node.state.harness_status = "running"
        restored = _restore_harness_payload(_serialize_harness_node(node))
        assert restored.state.harness_status == "interrupted"
        assert "Interrupted" in restored.state.harness_status_detail

    def test_blank_workspace_id_is_self_healed_on_load(self):
        document = SceneDocument()
        node = document.add_harness_node(0, 0, "goal")
        payload = _serialize_harness_node(node)
        payload["workspace_id"] = ""
        restored = _restore_harness_payload(payload)
        assert restored.state.harness_workspace_id
