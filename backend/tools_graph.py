"""ADR-008 stage 8.1: the graph as a tool surface.

Canvas mutation exposed as scoped, approval-gated tools in the ADR-007
registry - the ADR's own decision #1: "The agent literally drives the same
intents a user would - no privileged backdoor path." Concretely that means
every mutating handler here goes through the SAME domain factories the WS
intents call (add_chat_node, add_code_node, connect, ...) wrapped in the
SAME document.record_command() the intents wrap them in - an agent-created
node is undoable, patch-published, and session-persisted exactly like a
user-created one, because it took the identical write path.

Registration follows backend/tools_knowledge.py's factory pattern: a
make_*_handler(document) closure per tool so tests can bind a scratch
SceneDocument, plus one register_graph_tools(registry, document) that the
per-session wiring calls once.

Run attribution: handlers read `run_id` off the RunContext they are handed
(backend/builder.py's BuilderRunContext carries it; a bare RunContext has
none -> commands land unstamped, same as a direct user action). The stamp
is what makes `undo_run` able to revert a whole build (ADR-010 stage 10.5)
- and it is passed per record_command call rather than via a long-open
composite() deliberately: the composite buffer is document-global state,
so holding one open across an approval await would swallow CONCURRENT
user commands into the builder's undo entry. Every handler's
record_command call is one synchronous stretch - atomic w.r.t. the event
loop - so per-call stamping has no such window.

Placement: the model never picks coordinates. New nodes land relative to
their parent using the same MESSAGE_VERTICAL_SPACING convention
send_message's own reply placement uses (backend/domain/model.py), with a
horizontal fan-out for siblings so parallel children don't stack.
"""

from __future__ import annotations

import json
from typing import Any

from backend.domain.graph import SceneDocument, SceneError
from backend.domain.model import MESSAGE_VERTICAL_SPACING
from backend.domain.node_states import ChatState, NoteState, PycoderState
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import GRAPH_MUTATE, GRAPH_READ, RunContext, ToolRegistry, ToolResult

# Sibling fan-out: the second/third/... child of the same parent shifts right
# so a builder creating parallel children produces a readable fan, not a
# stack. Value matches the vertical rhythm rather than inventing a new one.
_SIBLING_HORIZONTAL_SPACING = 360

# read_subgraph excerpt cap: full content stays on the node (the canvas is
# the artifact); the tool result feeds the MODEL, where an unbounded excerpt
# would eat the build's own token budget. 1000 chars is enough to reason
# over; the model can ask for a specific node again if it truly needs more.
_READ_EXCERPT_CHARS = 1000
_READ_MAX_DEPTH = 3

_CREATABLE_KINDS = ("chat", "note", "code", "document", "pycoder", "web_research")


GRAPH_CREATE_NODE_SPEC = ToolSpec(
    name="graph.create_node",
    description=(
        "Create a new node on the canvas. kind must be one of: chat (a "
        "message bubble; set is_user false for assistant content), note (a "
        "free-floating sticky note), code (a code block with a language), "
        "document (a text attachment; parent required), pycoder (an "
        "executable Python node; parent required; set its code via "
        "graph.set_node_content, run it via run_node), web_research (a "
        "research node; parent required; run it via run_node). Returns the "
        "new node's id. Nodes are placed automatically relative to their "
        "parent - do not invent coordinates."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(_CREATABLE_KINDS)},
            "parent_id": {
                "type": "string",
                "description": "Existing node to attach under. Required for document/pycoder/web_research; optional for chat/code; ignored for note.",
            },
            "title": {"type": "string", "description": "Optional title (document kind requires one)."},
            "content": {"type": "string", "description": "Initial content/text/code for the node."},
            "language": {"type": "string", "description": "code kind only: the language label (defaults to python)."},
            "is_user": {"type": "boolean", "description": "chat kind only: true for a user-voice bubble (default false)."},
        },
        "required": ["kind"],
    },
)

GRAPH_CONNECT_SPEC = ToolSpec(
    name="graph.connect",
    description=(
        "Connect two existing nodes with a directed edge (source -> target). "
        "Idempotent: connecting an already-connected pair is a no-op."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "target_id": {"type": "string"},
        },
        "required": ["source_id", "target_id"],
    },
)

GRAPH_SET_NODE_CONTENT_SPEC = ToolSpec(
    name="graph.set_node_content",
    description=(
        "Replace an existing node's content. Supported kinds: chat (the "
        "message text), note (the note text), pycoder (the Python code that "
        "run_node will execute). Other kinds are read-only through this "
        "tool."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["node_id", "content"],
    },
)

GRAPH_READ_SUBGRAPH_SPEC = ToolSpec(
    name="graph.read_subgraph",
    description=(
        "Read the subgraph under a node: the node itself plus descendants "
        "(depth-limited). Returns JSON nodes [{id, kind, title, content "
        "(excerpt), truncated}] and edges [{source, target}]. Use this to "
        "see what exists before creating or connecting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "root_id": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1, "maximum": _READ_MAX_DEPTH},
        },
        "required": ["root_id"],
    },
)


def _run_id_of(ctx: RunContext) -> str | None:
    """The builder's BuilderRunContext carries run_id; a bare RunContext
    (tests, a future non-builder caller) does not - unstamped is the
    correct degradation, matching a direct user action."""
    return getattr(ctx, "run_id", None)


def _place_child(document: SceneDocument, parent_id: str | None) -> tuple[float, float]:
    """Parent-relative placement, model-free: directly below the parent,
    fanning right one slot per existing child so parallel children of the
    same parent land side by side instead of stacked."""
    if parent_id is None or parent_id not in document.nodes:
        # Free-floating (note, parentless chat/code): drop near the origin
        # offset by node count so repeated creations don't perfectly overlap.
        n = len(document.nodes)
        return 80.0 + (n % 5) * 40.0, 80.0 + (n % 7) * 40.0
    parent = document.nodes[parent_id]
    existing_children = sum(1 for e in document.edges.values() if e.source == parent_id)
    return (
        parent.x + existing_children * _SIBLING_HORIZONTAL_SPACING,
        parent.y + MESSAGE_VERTICAL_SPACING,
    )


def _error(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)


def make_create_node_handler(document: SceneDocument):
    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        args = call.arguments
        kind = args.get("kind")
        if kind not in _CREATABLE_KINDS:
            return _error(f"Unsupported kind {kind!r}. Supported: {', '.join(_CREATABLE_KINDS)}.")
        parent_id = args.get("parent_id") or None
        if parent_id is not None and parent_id not in document.nodes:
            return _error(f"Unknown parent node: {parent_id!r}.")
        if kind in ("document", "pycoder", "web_research") and parent_id is None:
            return _error(f"kind {kind!r} requires parent_id.")
        content = str(args.get("content") or "")
        title = str(args.get("title") or "")
        if kind == "document" and not title:
            return _error("kind 'document' requires a title.")

        x, y = _place_child(document, parent_id)
        run_id = _run_id_of(ctx)

        def mutator():
            if kind == "chat":
                return document.add_chat_node(x, y, content, bool(args.get("is_user", False)), parent_id)
            if kind == "note":
                node = document.add_note(x, y)
                if content:
                    document.set_note_content(node.id, content)
                return node
            if kind == "code":
                return document.add_code_node(x, y, content, str(args.get("language") or "python"), parent_id)
            if kind == "document":
                return document.add_document_node(x, y, title, content, "document", parent_id)
            if kind == "pycoder":
                node = document.add_pycoder_node(x, y, parent_id)
                if content and isinstance(node.state, PycoderState):
                    node.state.pycoder_code = content
                return node
            node = document.add_web_research_node(x, y, parent_id)
            if content:
                # WebResearchState's own contract: SceneNode.content holds
                # the query text (node_states.py) - so `content` here IS the
                # research question run_node will execute.
                node.content = content
            return node

        watch = [parent_id] if parent_id else []
        node, _command = document.record_command(
            "builderCreateNode", "agent", mutator, node_ids=watch, run_id=run_id,
        )
        return ToolResult(content=json.dumps({"node_id": node.id, "kind": node.kind, "title": node.title}))

    return handler


def make_connect_handler(document: SceneDocument):
    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        source_id = str(call.arguments.get("source_id") or "")
        target_id = str(call.arguments.get("target_id") or "")
        for node_id in (source_id, target_id):
            if node_id not in document.nodes:
                return _error(f"Unknown node: {node_id!r}.")
        try:
            _edge, _command = document.record_command(
                "builderConnect", "agent",
                lambda: document.connect(source_id, target_id),
                node_ids=[source_id, target_id], run_id=_run_id_of(ctx),
            )
        except SceneError as exc:
            return _error(str(exc))
        return ToolResult(content=json.dumps({"connected": [source_id, target_id]}))

    return handler


def make_set_node_content_handler(document: SceneDocument):
    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        node_id = str(call.arguments.get("node_id") or "")
        content = str(call.arguments.get("content") or "")
        node = document.nodes.get(node_id)
        if node is None:
            return _error(f"Unknown node: {node_id!r}.")
        run_id = _run_id_of(ctx)

        if node.kind == "chat" and isinstance(node.state, ChatState):
            mutator = lambda: document.update_chat_node_content(node_id, content)
        elif node.kind == "note" and isinstance(node.state, NoteState):
            mutator = lambda: document.set_note_content(node_id, content)
        elif node.kind == "pycoder" and isinstance(node.state, PycoderState):
            def mutator():
                node.state.pycoder_code = content
                return node
        else:
            return _error(
                f"Node {node_id!r} is kind {node.kind!r} - not writable via "
                "graph.set_node_content (supported: chat, note, pycoder)."
            )

        document.record_command(
            "builderSetContent", "agent", mutator, node_ids=[node_id], run_id=run_id,
        )
        return ToolResult(content=json.dumps({"node_id": node_id, "kind": node.kind}))

    return handler


def make_read_subgraph_handler(document: SceneDocument):
    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        root_id = str(call.arguments.get("root_id") or "")
        if root_id not in document.nodes:
            return _error(f"Unknown node: {root_id!r}.")
        depth_arg = call.arguments.get("depth")
        try:
            depth = min(int(depth_arg), _READ_MAX_DEPTH) if depth_arg is not None else _READ_MAX_DEPTH
        except (TypeError, ValueError):
            return _error(f"depth must be an integer, got {depth_arg!r}.")
        if depth < 1:
            return _error(f"depth must be >= 1, got {depth}.")

        # BFS over outgoing edges only - "the subgraph under a node" is the
        # branch it roots, matching how every branch-walk in this codebase
        # reads direction (chat_branch_history walks parents via incoming;
        # this is the mirror image for descendants).
        seen = {root_id}
        frontier = [root_id]
        edges_out: list[dict[str, str]] = []
        for _ in range(depth):
            next_frontier = []
            for node_id in frontier:
                for edge in document.edges.values():
                    if edge.source != node_id:
                        continue
                    edges_out.append({"source": edge.source, "target": edge.target})
                    if edge.target not in seen:
                        seen.add(edge.target)
                        next_frontier.append(edge.target)
            frontier = next_frontier
            if not frontier:
                break

        nodes_out: list[dict[str, Any]] = []
        for node_id in seen:
            node = document.nodes[node_id]
            content = node.content or ""
            nodes_out.append({
                "id": node.id,
                "kind": node.kind,
                "title": node.title,
                "content": content[:_READ_EXCERPT_CHARS],
                "truncated": len(content) > _READ_EXCERPT_CHARS,
            })
        return ToolResult(content=json.dumps({"nodes": nodes_out, "edges": edges_out}))

    return handler


def register_graph_tools(registry: ToolRegistry, document: SceneDocument) -> None:
    """Registers the stage-8.1 graph tool family. Mutating tools are
    approval="once" (every call prompts in co-pilot; backend/builder.py's
    mode-aware router is what makes autopilot auto-approve them);
    read_subgraph is auto - reading the canvas costs nothing and gates
    nothing, the same posture knowledge.search ships with."""
    registry.register(
        GRAPH_CREATE_NODE_SPEC, make_create_node_handler(document),
        scopes={GRAPH_MUTATE}, approval="once",
    )
    registry.register(
        GRAPH_CONNECT_SPEC, make_connect_handler(document),
        scopes={GRAPH_MUTATE}, approval="once",
    )
    registry.register(
        GRAPH_SET_NODE_CONTENT_SPEC, make_set_node_content_handler(document),
        scopes={GRAPH_MUTATE}, approval="once",
    )
    registry.register(
        GRAPH_READ_SUBGRAPH_SPEC, make_read_subgraph_handler(document),
        scopes={GRAPH_READ}, approval="auto",
    )
