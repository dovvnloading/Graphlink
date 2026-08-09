"""ADR-008 stage 8.1: the graph-as-tool-surface family + the tool-turn primitive.

Covers the three layers the stage adds, bottom-up:

- backend/tools_graph.py handlers driven directly (a real SceneDocument,
  real record_command underneath - the "same write path a user intent
  takes" invariant is asserted via the undo stack, not just node presence);
- ToolRegistry.invoke() gating in front of them (scope check before any
  prompt, denial as error ToolResult, run_id stamping via the ctx);
- api_provider.chat_turn_with_tools() consuming a scripted FakeProvider at
  the provider seam - the stage's exit criterion test drives a real "agent
  turn" through the REAL primitive and the REAL registry: the model
  proposes creates, the harness invokes them approval-gated, feeds the
  resulting node ids back, and the connect lands - two nodes, one edge,
  every mutation undoable as one run.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

import api_provider
from backend.domain.graph import SceneDocument
from backend.providers.base import (
    ProviderCapabilities,
    ProviderEvent,
    ToolCall,
    ToolSpec,
)
from backend.providers.fake import FakeProvider
from backend.tools import GRAPH_MUTATE, GRAPH_READ, RunContext, ToolRegistry, ToolResult
from backend.tools_graph import register_graph_tools


def make_registry(document: SceneDocument) -> ToolRegistry:
    registry = ToolRegistry()
    register_graph_tools(registry, document)
    return registry


def make_ctx(
    *,
    scopes: frozenset[str] = frozenset({GRAPH_READ, GRAPH_MUTATE}),
    approve: bool = True,
    run_id: str | None = None,
) -> tuple[RunContext, list[ToolCall]]:
    """A RunContext whose approval router records every prompt it receives -
    tests assert against `prompts` to prove scope denials never cost a
    prompt and auto tools never ask."""
    prompts: list[ToolCall] = []

    async def request_approval(call: ToolCall) -> bool:
        prompts.append(call)
        return approve

    ctx = RunContext(granted_scopes=scopes, request_approval=request_approval)
    if run_id is not None:
        # The builder's own context subclass carries run_id (backend/builder.py,
        # stage 8.3); until it exists, tests attach the attribute the same way
        # _run_id_of() reads it - duck-typed, deliberately.
        ctx.run_id = run_id
    return ctx, prompts


def invoke(registry: ToolRegistry, ctx: RunContext, name: str, arguments: dict) -> ToolResult:
    call = ToolCall(id=f"call_{name}", name=name, arguments=arguments)
    return asyncio.run(registry.invoke(call, ctx))


def seed_parent(document: SceneDocument):
    return document.add_chat_node(100, 100, "the root message", True)


class TestCreateNode:
    def test_creates_a_chat_node_connected_to_its_parent(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {
            "kind": "chat", "parent_id": parent.id, "content": "an assistant reply",
        })

        assert not result.is_error
        payload = json.loads(result.content)
        node = document.nodes[payload["node_id"]]
        assert node.kind == "chat"
        assert node.content == "an assistant reply"
        assert any(
            e.source == parent.id and e.target == node.id for e in document.edges.values()
        ), "the parent edge must exist - creation and connection are one atomic write"

    def test_creates_a_free_floating_note_with_content(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {"kind": "note", "content": "remember this"})

        payload = json.loads(result.content)
        node = document.nodes[payload["node_id"]]
        assert node.kind == "note"
        assert node.content == "remember this"
        assert not document.edges, "notes are free-floating - no parent edge"

    def test_creates_a_pycoder_node_with_initial_code(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {
            "kind": "pycoder", "parent_id": parent.id, "content": "print('hi')",
        })

        payload = json.loads(result.content)
        node = document.nodes[payload["node_id"]]
        assert node.kind == "pycoder"
        assert node.state.pycoder_code == "print('hi')"

    def test_parent_required_kinds_error_without_one(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        for kind in ("pycoder", "web_research", "document"):
            result = invoke(registry, ctx, "graph.create_node", {"kind": kind, "title": "t"})
            assert result.is_error, kind
            assert "parent_id" in result.content

    def test_unknown_kind_and_unknown_parent_are_errors_not_exceptions(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        assert invoke(registry, ctx, "graph.create_node", {"kind": "frame"}).is_error
        assert invoke(
            registry, ctx, "graph.create_node", {"kind": "chat", "parent_id": "n999"},
        ).is_error
        assert not document.nodes, "a rejected create must leave the document untouched"

    def test_children_are_placed_relative_to_the_parent_not_at_the_origin(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        first = json.loads(invoke(registry, ctx, "graph.create_node", {
            "kind": "chat", "parent_id": parent.id, "content": "a",
        }).content)
        second = json.loads(invoke(registry, ctx, "graph.create_node", {
            "kind": "chat", "parent_id": parent.id, "content": "b",
        }).content)

        a, b = document.nodes[first["node_id"]], document.nodes[second["node_id"]]
        assert a.y > parent.y and b.y > parent.y
        assert a.x != b.x, "siblings fan out horizontally instead of stacking"


class TestConnect:
    def test_connects_two_existing_nodes(self):
        document = SceneDocument()
        a = document.add_chat_node(0, 0, "a", True)
        b = document.add_chat_node(0, 200, "b", False)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.connect", {"source_id": a.id, "target_id": b.id})

        assert not result.is_error
        assert any(e.source == a.id and e.target == b.id for e in document.edges.values())

    def test_unknown_endpoint_is_an_error(self):
        document = SceneDocument()
        a = document.add_chat_node(0, 0, "a", True)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.connect", {"source_id": a.id, "target_id": "n404"})
        assert result.is_error


class TestSetNodeContent:
    def test_updates_chat_note_and_pycoder_content(self):
        document = SceneDocument()
        chat = document.add_chat_node(0, 0, "old", False)
        note = document.add_note(50, 50)
        parent = document.add_chat_node(0, 200, "p", True)
        pycoder = document.add_pycoder_node(0, 400, parent.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        assert not invoke(registry, ctx, "graph.set_node_content", {
            "node_id": chat.id, "content": "new text",
        }).is_error
        assert not invoke(registry, ctx, "graph.set_node_content", {
            "node_id": note.id, "content": "note text",
        }).is_error
        assert not invoke(registry, ctx, "graph.set_node_content", {
            "node_id": pycoder.id, "content": "x = 1",
        }).is_error

        assert document.nodes[chat.id].content == "new text"
        assert document.nodes[note.id].content == "note text"
        assert document.nodes[pycoder.id].state.pycoder_code == "x = 1"

    def test_unsupported_kind_is_a_clear_error(self):
        document = SceneDocument()
        code = document.add_code_node(0, 0, "print(1)", "python")
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.set_node_content", {
            "node_id": code.id, "content": "print(2)",
        })
        assert result.is_error
        assert "code" in result.content


class TestReadSubgraph:
    def test_returns_descendants_with_excerpts_and_edges(self):
        document = SceneDocument()
        root = document.add_chat_node(0, 0, "root", True)
        child = document.add_chat_node(0, 200, "child " + "x" * 2000, False, root.id)
        registry = make_registry(document)
        ctx, prompts = make_ctx()

        result = invoke(registry, ctx, "graph.read_subgraph", {"root_id": root.id})

        assert not result.is_error
        assert prompts == [], "read_subgraph is auto - it must never prompt"
        payload = json.loads(result.content)
        ids = {n["id"] for n in payload["nodes"]}
        assert ids == {root.id, child.id}
        child_row = next(n for n in payload["nodes"] if n["id"] == child.id)
        assert child_row["truncated"] is True
        assert len(child_row["content"]) == 1000
        assert {"source": root.id, "target": child.id} in payload["edges"]

    def test_depth_limits_the_walk(self):
        document = SceneDocument()
        a = document.add_chat_node(0, 0, "a", True)
        b = document.add_chat_node(0, 200, "b", False, a.id)
        c = document.add_chat_node(0, 400, "c", True, b.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": a.id, "depth": 1,
        }).content)
        ids = {n["id"] for n in payload["nodes"]}
        assert ids == {a.id, b.id}
        assert c.id not in ids


class TestApprovalAndScopeGating:
    def test_denied_approval_leaves_the_document_untouched(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, prompts = make_ctx(approve=False)

        result = invoke(registry, ctx, "graph.create_node", {"kind": "note", "content": "nope"})

        assert result.is_error
        assert "denied" in result.content
        assert len(prompts) == 1
        assert not document.nodes

    def test_missing_scope_is_denied_without_ever_prompting(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, prompts = make_ctx(scopes=frozenset({GRAPH_READ}))

        result = invoke(registry, ctx, "graph.create_node", {"kind": "note"})

        assert result.is_error
        assert "scope" in result.content
        assert prompts == [], "scope denial must never cost a human a prompt"


class TestRunIdStamping:
    def test_commands_carry_the_run_id_and_undo_run_reverts_the_build(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx(run_id="build-42")

        first = json.loads(invoke(registry, ctx, "graph.create_node", {
            "kind": "chat", "parent_id": parent.id, "content": "step one",
        }).content)
        second = json.loads(invoke(registry, ctx, "graph.create_node", {
            "kind": "note", "content": "step two",
        }).content)

        assert all(c.run_id == "build-42" for c in document.command_log), (
            "every builder mutation must be stamped - undo_run keys on it"
        )

        reverted = document.undo_run("build-42")

        assert reverted == 2
        assert first["node_id"] not in document.nodes
        assert second["node_id"] not in document.nodes
        assert parent.id in document.nodes, "the user's own pre-build node survives"

    def test_a_bare_run_context_records_unstamped_commands(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, _ = make_ctx()  # no run_id attached

        invoke(registry, ctx, "graph.create_node", {"kind": "note", "content": "x"})

        assert document.command_log[-1].run_id is None


def tool_call_event(call_id: str, name: str, arguments: dict) -> ProviderEvent:
    return ProviderEvent("tool_call", tool_call=ToolCall(id=call_id, name=name, arguments=arguments))


class TestChatTurnWithTools:
    """chat_turn_with_tools consumed against a scripted FakeProvider patched
    in at the model_ref seam - the REAL event-consumption loop runs."""

    def _patch_provider(self, monkeypatch, provider: FakeProvider):
        monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda ref, state: provider)

    def _model_ref(self):
        from graphlink_model_catalog import ModelRef

        return ModelRef(provider="ollama", model_id="fake-tools-model")

    def test_collects_tool_calls_final_text_and_usage(self, monkeypatch):
        provider = FakeProvider(
            events=[
                ProviderEvent("text", "Let me create that."),
                tool_call_event("c1", "graph.create_node", {"kind": "note"}),
                ProviderEvent("done", "Let me create that.", usage={"prompt_tokens": 10, "completion_tokens": 5}),
            ],
            capabilities=ProviderCapabilities(streaming=True, tools=True),
        )
        self._patch_provider(monkeypatch, provider)

        result = api_provider.chat_turn_with_tools(
            "task_chat", [{"role": "user", "content": "make a note"}],
            tools=(ToolSpec(name="graph.create_node", description="d", input_schema={"type": "object"}),),
            model_ref=self._model_ref(),
        )

        assert result["message"]["content"] == "Let me create that."
        assert [c.name for c in result["tool_calls"]] == ["graph.create_node"]
        assert result["tool_calls"][0].arguments == {"kind": "note"}
        assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
        assert provider.requests[0].tools[0].name == "graph.create_node", (
            "the specs must actually reach ChatRequest.tools - the exact gap "
            "this primitive exists to close"
        )

    def test_a_non_tools_provider_is_rejected_before_any_request(self, monkeypatch):
        provider = FakeProvider(capabilities=ProviderCapabilities(streaming=True, tools=False))
        self._patch_provider(monkeypatch, provider)

        with pytest.raises(RuntimeError, match="does not support tool calling"):
            api_provider.chat_turn_with_tools(
                "task_chat", [], tools=(ToolSpec(name="t", description="d", input_schema={}),),
                model_ref=self._model_ref(),
            )
        assert provider.requests == [], "the gate must fire before any provider call"

    def test_a_reset_discards_the_prior_attempts_tool_calls(self, monkeypatch):
        provider = FakeProvider(
            events=[
                tool_call_event("c1", "graph.create_node", {"kind": "note"}),
                ProviderEvent("reset", ""),
                tool_call_event("c2", "graph.connect", {"source_id": "a", "target_id": "b"}),
                ProviderEvent("done", "after retry"),
            ],
            capabilities=ProviderCapabilities(streaming=True, tools=True),
        )
        self._patch_provider(monkeypatch, provider)

        result = api_provider.chat_turn_with_tools(
            "task_chat", [], tools=(ToolSpec(name="t", description="d", input_schema={}),),
            model_ref=self._model_ref(),
        )

        assert [c.id for c in result["tool_calls"]] == ["c2"], (
            "a reasoning-retry reset discards the abandoned attempt's calls "
            "exactly like it discards its text"
        )

    def test_cancellation_raises_the_app_sentinel(self, monkeypatch):
        provider = FakeProvider(
            events=[tool_call_event("c1", "x", {})],
            capabilities=ProviderCapabilities(streaming=True, tools=True),
        )
        self._patch_provider(monkeypatch, provider)
        cancel_event = threading.Event()
        cancel_event.set()

        with pytest.raises(api_provider.RequestCancelledError):
            api_provider.chat_turn_with_tools(
                "task_chat", [], tools=(),
                model_ref=self._model_ref(), cancellation_event=cancel_event,
            )


class TestAgentTurnExitCriterion:
    """Stage 8.1 exit: an agent turn creates and connects two nodes via
    tools, approval-gated. The harness plays the loop's role (the loop
    itself is stage 8.3): turn 1's scripted model proposes two creates, the
    registry invokes them under a prompting ctx, the REAL node ids from the
    results feed turn 2's connect, and the whole build is one undo_run."""

    def test_an_agent_turn_creates_and_connects_two_nodes_approval_gated(self, monkeypatch):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, prompts = make_ctx(run_id="build-exit-8-1")

        turn_one = FakeProvider(
            events=[
                tool_call_event("c1", "graph.create_node", {
                    "kind": "chat", "parent_id": parent.id, "content": "research summary",
                }),
                tool_call_event("c2", "graph.create_node", {"kind": "note", "content": "key facts"}),
                ProviderEvent("done", ""),
            ],
            capabilities=ProviderCapabilities(streaming=True, tools=True),
        )
        monkeypatch.setattr(api_provider, "_provider_for_model_ref", lambda ref, state: turn_one)
        from graphlink_model_catalog import ModelRef

        specs = tuple(registry.specs())
        turn = api_provider.chat_turn_with_tools(
            "task_chat", [{"role": "user", "content": "build me a summary branch"}],
            tools=specs, model_ref=ModelRef(provider="ollama", model_id="fake"),
        )
        assert len(turn["tool_calls"]) == 2

        created_ids = []
        for call in turn["tool_calls"]:
            result = asyncio.run(registry.invoke(call, ctx))
            assert not result.is_error
            created_ids.append(json.loads(result.content)["node_id"])

        connect_result = asyncio.run(registry.invoke(
            ToolCall(id="c3", name="graph.connect", arguments={
                "source_id": created_ids[0], "target_id": created_ids[1],
            }),
            ctx,
        ))
        assert not connect_result.is_error

        # Approval-gated: all three mutating calls prompted.
        assert len(prompts) == 3

        # Two nodes exist and are connected.
        assert all(nid in document.nodes for nid in created_ids)
        assert any(
            e.source == created_ids[0] and e.target == created_ids[1]
            for e in document.edges.values()
        )

        # The whole agent turn is one run: one undo_run reverts everything,
        # the user's own node survives.
        reverted = document.undo_run("build-exit-8-1")
        assert reverted == 3
        assert all(nid not in document.nodes for nid in created_ids)
        assert parent.id in document.nodes
