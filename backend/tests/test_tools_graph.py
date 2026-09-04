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
        # _run_id_of() reads it - duck-typed, deliberately, which is exactly
        # what the base RunContext cannot declare.
        ctx.run_id = run_id  # type: ignore[attr-defined]
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

    def test_parent_required_kinds_error_without_one(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        for kind in ("web_research", "document"):
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
    def test_updates_chat_and_note_content(self):
        document = SceneDocument()
        chat = document.add_chat_node(0, 0, "old", False)
        note = document.add_note(50, 50)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        assert not invoke(registry, ctx, "graph.set_node_content", {
            "node_id": chat.id, "content": "new text",
        }).is_error
        assert not invoke(registry, ctx, "graph.set_node_content", {
            "node_id": note.id, "content": "note text",
        }).is_error

        assert document.nodes[chat.id].content == "new text"
        assert document.nodes[note.id].content == "note text"

    def test_unsupported_kind_is_a_clear_error(self):
        """ADR-021 stage 21.2 widened this tool to code/document/html/
        artifact, so the read-only set is now exactly the kinds whose
        content IS a run's own output - a thinking node among them."""
        document = SceneDocument()
        parent = document.add_chat_node(0, 0, "parent", True)
        thinking = document.add_thinking_node(0, 200, "reasoning text", parent.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.set_node_content", {
            "node_id": thinking.id, "content": "rewritten",
        })
        assert result.is_error
        assert "thinking" in result.content
        assert document.nodes[thinking.id].content == "reasoning text"

    def test_widened_kinds_are_writable_in_place(self):
        """ADR-021 stage 21.2: code/document/html/artifact became writable.
        Each writes the field its own wire row publishes, and none of them
        touch the title - the same posture update_chat_node_content
        documents for every in-place domain mutator."""
        document = SceneDocument()
        parent = document.add_chat_node(0, 0, "parent", True)
        code = document.add_code_node(0, 200, "print(1)", "python", parent.id)
        doc = document.add_document_node(0, 400, "Report", "old body", "document", parent.id)
        html = document.add_html_node(0, 600, "<p>old</p>", parent.id)
        artifact = document.add_artifact_node(0, 800, parent.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        titles_before = {n.id: n.title for n in (code, doc, html, artifact)}

        for node, new_content in (
            (code, "print(2)"),
            (doc, "new body"),
            (html, "<p>new</p>"),
            (artifact, "# A draft"),
        ):
            assert not invoke(registry, ctx, "graph.set_node_content", {
                "node_id": node.id, "content": new_content,
            }).is_error, f"{node.kind} must be writable"

        assert document.nodes[code.id].state.code == "print(2)"
        assert document.nodes[doc.id].content == "new body"
        assert document.nodes[html.id].content == "<p>new</p>"
        assert document.nodes[artifact.id].state.artifact_content == "# A draft"
        for node_id, title in titles_before.items():
            assert document.nodes[node_id].title == title, (
                "in-place content writes never recompute a title"
            )


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

    def test_node_count_is_capped_and_flagged_truncated(self):
        """review-fix: depth alone doesn't bound node COUNT - a hub node
        can have hundreds of descendants within a couple hops. Each one
        re-enters the builder's messages list and is re-sent on every
        later turn (messages only ever grow), so an unbounded read could
        alone overflow a turn's context window."""
        from backend.tools_graph import _READ_MAX_NODES

        document = SceneDocument()
        root = document.add_chat_node(0, 0, "root", True)
        for i in range(_READ_MAX_NODES + 15):
            document.add_chat_node(0, 200 + i, f"child {i}", False, root.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": root.id, "depth": 1,
        }).content)

        assert len(payload["nodes"]) == _READ_MAX_NODES
        assert payload["nodes_truncated"] is True
        node_ids = {n["id"] for n in payload["nodes"]}
        edge_targets = {e["target"] for e in payload["edges"]}
        assert edge_targets <= node_ids, "no edge should reference a node excluded by the cap"

    def test_a_small_subgraph_is_not_flagged_truncated(self):
        document = SceneDocument()
        root = document.add_chat_node(0, 0, "root", True)
        document.add_chat_node(0, 200, "child", False, root.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": root.id,
        }).content)

        assert payload["nodes_truncated"] is False


class RecordingDispatcher:
    """Just enough dispatcher for graph.delete_node's teardown seam - it
    records what it was asked to tear down so tests can assert a deleted
    node's live resources are actually released, not silently leaked."""

    def __init__(self):
        self.removed_sandboxes: list[str] = []
        self.cancelled: list[tuple] = []

    async def remove_code_sandbox_scratch_dir(self, sandbox_id):
        self.removed_sandboxes.append(sandbox_id)

    def cancel_code_sandbox(self, request_id):
        self.cancelled.append(("code_sandbox", request_id))

    def cancel_builder(self, request_id):
        self.cancelled.append(("builder", request_id))

    def cancel_harness(self, request_id):
        self.cancelled.append(("harness", request_id))


def make_registry_with_delete(document: SceneDocument):
    """graph.delete_node registers alongside run_node (both need the
    dispatcher), so it is absent from make_registry's pure-graph set."""
    from backend.tools_graph import GRAPH_DELETE_NODE_SPEC, make_delete_node_handler

    registry = make_registry(document)
    dispatcher = RecordingDispatcher()
    registry.register(
        GRAPH_DELETE_NODE_SPEC, make_delete_node_handler(document, dispatcher),
        scopes={GRAPH_MUTATE}, approval="always",
    )
    return registry, dispatcher


class TestDeleteNode:
    """ADR-021 stage 21.2: the Builder's first destructive tool. Blast
    radius is one leaf node by construction."""

    def test_deletes_a_leaf_node_and_its_edge(self):
        document = SceneDocument()
        parent = seed_parent(document)
        leaf = document.add_chat_node(0, 200, "wrong answer", False, parent.id)
        registry, _ = make_registry_with_delete(document)
        ctx, prompts = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": leaf.id})

        assert not result.is_error
        assert leaf.id not in document.nodes
        assert parent.id in document.nodes, "only the named node is deleted"
        assert not any(
            e.source == leaf.id or e.target == leaf.id for e in document.edges.values()
        ), "edges die with either endpoint"
        assert [c.name for c in prompts] == ["graph.delete_node"], (
            "a delete always prompts - it destroys content the user may not "
            "have read yet"
        )

    def test_a_node_with_children_is_refused(self):
        document = SceneDocument()
        parent = seed_parent(document)
        middle = document.add_chat_node(0, 200, "middle", False, parent.id)
        document.add_chat_node(0, 400, "leaf", True, middle.id)
        registry, _ = make_registry_with_delete(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": middle.id})

        assert result.is_error
        assert "child" in result.content
        assert middle.id in document.nodes, "no subtree is orphaned by one call"

    def test_the_plan_node_cannot_be_deleted(self):
        document = SceneDocument()
        plan = document.add_plan_node(0, 0, "build something")
        registry, _ = make_registry_with_delete(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": plan.id})

        assert result.is_error
        assert "resume point" in result.content
        assert plan.id in document.nodes

    def test_groups_cannot_be_deleted(self):
        document = SceneDocument()
        a = document.add_chat_node(0, 0, "a", True)
        b = document.add_chat_node(0, 200, "b", False, a.id)
        frame = document.create_frame([a.id, b.id])
        registry, _ = make_registry_with_delete(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": frame.id})

        assert result.is_error
        assert frame.id in document.nodes

    def test_a_node_with_a_run_in_flight_is_refused(self):
        document = SceneDocument()
        parent = seed_parent(document)
        sandbox = document.add_code_sandbox_node(0, 200, parent.id)
        sandbox.pending_request_id = "req-live"
        registry, _ = make_registry_with_delete(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": sandbox.id})

        assert result.is_error
        assert "in flight" in result.content
        assert sandbox.id in document.nodes

    def test_deleting_a_code_sandbox_node_removes_its_scratch_dir(self):
        """A deleted Execution Sandbox node's on-disk venv must not outlive
        it, whichever surface deleted it - so this reuses the same teardown
        capture the removeNodes intent uses rather than a subset."""
        document = SceneDocument()
        parent = seed_parent(document)
        sandbox = document.add_code_sandbox_node(0, 200, parent.id)
        registry, dispatcher = make_registry_with_delete(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": sandbox.id})

        assert not result.is_error
        assert sandbox.id not in document.nodes
        assert dispatcher.removed_sandboxes == [sandbox.state.code_sandbox_sandbox_id]

    def test_delete_is_undoable_and_run_stamped(self):
        document = SceneDocument()
        parent = seed_parent(document)
        leaf = document.add_chat_node(0, 200, "oops", False, parent.id)
        registry, _ = make_registry_with_delete(document)
        ctx, _ = make_ctx(run_id="run-42")

        assert not invoke(registry, ctx, "graph.delete_node", {"node_id": leaf.id}).is_error
        assert leaf.id not in document.nodes

        document.undo()
        assert leaf.id in document.nodes, "a deleted node comes back on undo"

    def test_unknown_node_is_a_clear_error(self):
        document = SceneDocument()
        registry, _ = make_registry_with_delete(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": "nope"})

        assert result.is_error
        assert "Unknown node" in result.content

    def test_requires_the_mutate_scope(self):
        document = SceneDocument()
        parent = seed_parent(document)
        leaf = document.add_chat_node(0, 200, "x", False, parent.id)
        registry, _ = make_registry_with_delete(document)
        ctx, prompts = make_ctx(scopes=frozenset({GRAPH_READ}))

        result = invoke(registry, ctx, "graph.delete_node", {"node_id": leaf.id})

        assert result.is_error
        assert prompts == [], "a scope denial must never cost an approval prompt"
        assert leaf.id in document.nodes


class TestCreateNodeWidenedKinds:
    """ADR-021 stage 21.2: html/artifact/conversation joined the creatable
    set, so a build can end in a rendered page or a long-form draft."""

    def test_creates_an_html_node_from_raw_source(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {
            "kind": "html", "parent_id": parent.id, "content": "<h1>Report</h1>",
        })

        assert not result.is_error
        node = document.nodes[json.loads(result.content)["node_id"]]
        assert node.kind == "html"
        assert node.content == "<h1>Report</h1>"

    def test_creates_an_artifact_node(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {
            "kind": "artifact", "parent_id": parent.id,
        })

        assert not result.is_error
        node = document.nodes[json.loads(result.content)["node_id"]]
        assert node.kind == "artifact"

    def test_creates_a_conversation_node(self):
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {
            "kind": "conversation", "parent_id": parent.id,
        })

        assert not result.is_error
        assert document.nodes[json.loads(result.content)["node_id"]].kind == "conversation"

    def test_the_new_kinds_all_require_a_parent(self):
        document = SceneDocument()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        for kind in ("html", "artifact", "conversation"):
            result = invoke(registry, ctx, "graph.create_node", {"kind": kind})
            assert result.is_error, f"{kind} must require a parent"
            assert "parent_id" in result.content

    def test_chart_is_still_not_directly_creatable(self):
        """Chart creation is generation-coupled - run_node(action="chart")
        is its one path, since an empty chart node has no honest meaning."""
        document = SceneDocument()
        parent = seed_parent(document)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.create_node", {
            "kind": "chart", "parent_id": parent.id,
        })

        assert result.is_error


class TestReadSubgraphDirection:
    """ADR-021 stage 21.1: read_subgraph gained a direction. "down" is the
    pre-21.1 behavior and stays the default; "up" is the ancestor walk that
    lets a build see the branch a node hangs off, not just what hangs off
    it."""

    def build_chain(self):
        document = SceneDocument()
        a = document.add_chat_node(0, 0, "a", True)
        b = document.add_chat_node(0, 200, "b", False, a.id)
        c = document.add_chat_node(0, 400, "c", True, b.id)
        return document, a, b, c

    def test_default_direction_is_down_and_unchanged(self):
        document, a, b, c = self.build_chain()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": a.id,
        }).content)

        assert {n["id"] for n in payload["nodes"]} == {a.id, b.id, c.id}

    def test_up_walks_ancestors_not_descendants(self):
        document, a, b, c = self.build_chain()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": c.id, "direction": "up",
        }).content)
        assert {n["id"] for n in payload["nodes"]} == {a.id, b.id, c.id}, (
            "the branch c hangs off must be visible"
        )

        # The same root read downward sees only itself - exactly the
        # blindness "up" exists to fix.
        down = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": c.id, "direction": "down",
        }).content)
        assert {n["id"] for n in down["nodes"]} == {c.id}

    def test_both_sees_the_neighbourhood_without_duplicating_edges(self):
        document, a, b, c = self.build_chain()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": b.id, "direction": "both",
        }).content)

        assert {n["id"] for n in payload["nodes"]} == {a.id, b.id, c.id}
        edge_pairs = [(e["source"], e["target"]) for e in payload["edges"]]
        assert sorted(edge_pairs) == sorted(set(edge_pairs)), (
            "both-direction walking reaches an edge from either endpoint - "
            "it must still be emitted exactly once"
        )

    def test_unknown_direction_is_a_tool_error_not_a_crash(self):
        document, a, _b, _c = self.build_chain()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": a.id, "direction": "sideways",
        })

        assert result.is_error
        assert "direction" in result.content

    def test_direction_respects_the_node_cap(self):
        from backend.tools_graph import _READ_MAX_NODES

        document = SceneDocument()
        hub = document.add_chat_node(0, 0, "hub", True)
        for i in range(_READ_MAX_NODES + 10):
            document.add_chat_node(0, 200 + i, "child " + str(i), False, hub.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.read_subgraph", {
            "root_id": hub.id, "direction": "both",
        }).content)

        assert len(payload["nodes"]) == _READ_MAX_NODES
        assert payload["nodes_truncated"] is True


class TestListNodes:
    """ADR-021 stage 21.1: the canvas enumeration read_subgraph cannot do -
    it needs a root_id, and the only id a build is ever handed is its own
    plan node's."""

    def seed(self):
        document = SceneDocument()
        chat = document.add_chat_node(0, 0, "the auth flow question", True)
        code = document.add_code_node(0, 200, "print(1)", "python", chat.id)
        note = document.add_note(500, 0)
        document.set_note_content(note.id, "a stray thought about caching")
        return document, chat, code, note

    def test_lists_every_node_without_being_given_a_root(self):
        document, chat, code, note = self.seed()
        registry = make_registry(document)
        ctx, prompts = make_ctx()

        result = invoke(registry, ctx, "graph.list_nodes", {})

        assert not result.is_error
        assert prompts == [], "list_nodes is read-only - it must never prompt"
        payload = json.loads(result.content)
        assert {n["id"] for n in payload["nodes"]} == {chat.id, code.id, note.id}
        assert payload["total"] == 3
        assert payload["more"] is False

    def test_filters_by_kind(self):
        document, _chat, code, _note = self.seed()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.list_nodes", {
            "kind": "code",
        }).content)

        assert [n["id"] for n in payload["nodes"]] == [code.id]
        assert payload["total"] == 1

    def test_query_matches_content_case_insensitively(self):
        document, _chat, _code, note = self.seed()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.list_nodes", {
            "query": "CACHING",
        }).content)

        assert [n["id"] for n in payload["nodes"]] == [note.id]

    def test_query_matches_title_too(self):
        document = SceneDocument()
        parent = document.add_chat_node(0, 0, "parent", True)
        document.add_document_node(0, 200, "Quarterly Report", "body text", "document", parent.id)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.list_nodes", {
            "query": "quarterly",
        }).content)

        assert [n["title"] for n in payload["nodes"]] == ["Quarterly Report"]

    def test_excerpts_are_capped_and_flagged(self):
        from backend.tools_graph import _LIST_EXCERPT_CHARS

        document = SceneDocument()
        document.add_chat_node(0, 0, "y" * (_LIST_EXCERPT_CHARS + 50), True)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        payload = json.loads(invoke(registry, ctx, "graph.list_nodes", {}).content)

        row = payload["nodes"][0]
        assert len(row["excerpt"]) == _LIST_EXCERPT_CHARS
        assert row["truncated"] is True

    def test_pages_with_offset_and_reports_more(self):
        from backend.tools_graph import _LIST_MAX_NODES

        document = SceneDocument()
        for i in range(_LIST_MAX_NODES + 5):
            document.add_chat_node(0, i, "message " + str(i), True)
        registry = make_registry(document)
        ctx, _ = make_ctx()

        first = json.loads(invoke(registry, ctx, "graph.list_nodes", {}).content)
        assert first["returned"] == _LIST_MAX_NODES
        assert first["total"] == _LIST_MAX_NODES + 5
        assert first["more"] is True

        second = json.loads(invoke(registry, ctx, "graph.list_nodes", {
            "offset": _LIST_MAX_NODES,
        }).content)
        assert second["returned"] == 5
        assert second["more"] is False
        first_ids = {n["id"] for n in first["nodes"]}
        second_ids = {n["id"] for n in second["nodes"]}
        assert not (first_ids & second_ids), "pages must not overlap"

    def test_negative_offset_is_a_tool_error(self):
        document, _chat, _code, _note = self.seed()
        registry = make_registry(document)
        ctx, _ = make_ctx()

        result = invoke(registry, ctx, "graph.list_nodes", {"offset": -1})

        assert result.is_error
        assert "offset" in result.content

    def test_requires_only_the_read_scope(self):
        document, _chat, _code, _note = self.seed()
        registry = make_registry(document)
        ctx, _ = make_ctx(scopes=frozenset({GRAPH_READ}))

        result = invoke(registry, ctx, "graph.list_nodes", {})

        assert not result.is_error


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
