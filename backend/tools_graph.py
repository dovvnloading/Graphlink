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
horizontal fan-out for siblings so parallel children don't stack. A
parentless create (stage 8.7) instead anchors near the run's plan node, if
one exists, so a build's output lands where the user just looked rather
than at the canvas origin - see _place_child's own doc.
"""

from __future__ import annotations

import asyncio
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
# review-fix: depth alone doesn't bound node COUNT - a hub node can have
# hundreds of descendants within 3 hops. Each carries up to
# _READ_EXCERPT_CHARS of content that then re-enters the builder's
# messages list and gets re-sent on every subsequent turn (messages only
# ever grow - see builder.py's run_build), so an unbounded read can alone
# overflow a turn's context window and land the whole build as terminal
# "failed". 40 nodes * 1000 chars stays comfortably under any provider's
# context budget for one tool result.
_READ_MAX_NODES = 40

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
        "(excerpt), truncated}], edges [{source, target}], and "
        "nodes_truncated (true if the branch has more nodes than fit in one "
        "read - narrow the root or depth to see the rest). Use this to see "
        "what exists before creating or connecting."
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


def run_node_effective_scope(document: SceneDocument, call: ToolCall) -> str | None:
    """The scope a run_node CALL actually exercises - derived from its
    target node's kind + the requested action, exactly as the handler will
    enforce it. The mode-aware approval router needs this BEFORE invocation
    (autopilot auto-approves by registered scope, but run_node registers
    only graph.read; without this derivation, autopilot would silently
    auto-approve a net.fetch research run - the exact 'no network unless
    approved' hole the router exists to close). Returns None when the call
    is malformed/unknown - the handler's own validation will reject it with
    a proper error, so the router treats None as 'prompt'."""
    if call.name != "run_node":
        return None
    node = document.nodes.get(str(call.arguments.get("node_id") or ""))
    if node is None:
        return None
    action = str(call.arguments.get("action") or "") or _RUN_NODE_DEFAULT_ACTIONS.get(node.kind, "")
    return _RUN_NODE_ACTION_SCOPES.get(action)


def _run_id_of(ctx: RunContext) -> str | None:
    """The builder's BuilderRunContext carries run_id; a bare RunContext
    (tests, a future non-builder caller) does not - unstamped is the
    correct degradation, matching a direct user action."""
    return getattr(ctx, "run_id", None)


def _anchor_id_of(ctx: RunContext) -> str | None:
    """stage 8.7: BuilderRunContext already carries plan_node_id (builder.py)
    for run attribution - reused here as a PLACEMENT reference, not graph
    parentage, so a build's parentless creates (a note, a from-scratch
    chat/code node) land near the plan node the user just launched instead
    of scattered near the canvas origin. Same duck-typed degradation as
    _run_id_of: a bare RunContext has none, and placement falls through to
    the origin-drop fallback exactly as before this existed."""
    return getattr(ctx, "plan_node_id", None)


def _place_child(
    document: SceneDocument, parent_id: str | None, anchor_id: str | None = None,
) -> tuple[float, float]:
    """Parent-relative placement, model-free: directly below the parent,
    fanning right one slot per existing child so parallel children of the
    same parent land side by side instead of stacked."""
    if parent_id is not None and parent_id in document.nodes:
        parent = document.nodes[parent_id]
        existing_children = sum(1 for e in document.edges.values() if e.source == parent_id)
        return (
            parent.x + existing_children * _SIBLING_HORIZONTAL_SPACING,
            parent.y + MESSAGE_VERTICAL_SPACING,
        )
    if anchor_id is not None and anchor_id in document.nodes:
        # stage 8.7: same fan-out shape as the parented branch above, but
        # the anchor (a plan node) never gains an EDGE to what it builds -
        # it is a placement reference only - so there is no edge count to
        # read a sibling index off. A node already sitting on the anchor's
        # own placement row is this same anchor's own earlier creation
        # (nothing else places there), so counting THOSE stands in for
        # "existing children" without needing a persisted counter.
        anchor = document.nodes[anchor_id]
        row_y = anchor.y + MESSAGE_VERTICAL_SPACING
        row_siblings = sum(
            1 for n in document.nodes.values() if n.x >= anchor.x and abs(n.y - row_y) < 1.0
        )
        return anchor.x + row_siblings * _SIBLING_HORIZONTAL_SPACING, row_y
    # Free-floating with no anchor either (a bare RunContext, e.g. a future
    # non-builder caller): drop near the origin offset by node count so
    # repeated creations don't perfectly overlap.
    n = len(document.nodes)
    return 80.0 + (n % 5) * 40.0, 80.0 + (n % 7) * 40.0


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

        x, y = _place_child(document, parent_id, _anchor_id_of(ctx))
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
        nodes_truncated = False
        for _ in range(depth):
            next_frontier = []
            for node_id in frontier:
                for edge in document.edges.values():
                    if edge.source != node_id:
                        continue
                    if edge.target not in seen:
                        if len(seen) >= _READ_MAX_NODES:
                            nodes_truncated = True
                            continue  # neither node nor edge included past the cap
                        seen.add(edge.target)
                        next_frontier.append(edge.target)
                    edges_out.append({"source": edge.source, "target": edge.target})
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
        return ToolResult(content=json.dumps({
            "nodes": nodes_out, "edges": edges_out, "nodes_truncated": nodes_truncated,
        }))

    return handler


RUN_NODE_SPEC = ToolSpec(
    name="run_node",
    description=(
        "Run a node's action and return its result. Actions: execute (a "
        "pycoder node's default - runs its current code in its Python REPL; "
        "requires the code.execute scope), reply (a chat node's default - "
        "generates an assistant reply as a new child node from its branch "
        "history; requires provider.call), chart (explicit action on any "
        "content node - chat/note/document/code - generates a chart node "
        "FROM that node's content; requires provider.call; optional "
        "chart_type, default bar), research (a web_research node's default "
        "- runs a web search/fetch on its content as the query and returns "
        "cited findings; requires net.fetch - always prompts for approval, "
        "even in autopilot). Omit `action` to run the node's own default."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "node_id": {"type": "string"},
            "action": {"type": "string", "enum": ["execute", "reply", "chart", "research"]},
            "chart_type": {
                "type": "string",
                "description": "chart action only: bar, line, pie, scatter... (default bar).",
            },
        },
        "required": ["node_id"],
    },
)

# What each ACTION demands, enforced inside the handler: the registry's own
# scope check is static per-tool (registration-time), but the ADR's decision
# #1 says run_node "additionally carries the scope of what it runs" - a
# dynamic, per-call property only the handler can evaluate. The error
# message mirrors the registry's own scope-denial wording so the model sees
# one consistent shape for both static and dynamic denials.
_RUN_NODE_ACTION_SCOPES = {
    "execute": "code.execute",
    "reply": "provider.call",
    "chart": "provider.call",
    # ADR-008 stage 8.5: research is THE net.fetch action - in autopilot it
    # is the one thing that still always prompts (the ADR's "no network
    # unless approved" exit criterion), enforced by the mode router keying
    # on this scope.
    "research": "net.fetch",
}

# A node kind's default action (what "run this node" means with no explicit
# `action`), and which kinds each action accepts. Chart is deliberately
# never a default: it runs ON a content node (the UI's own "Generate chart"
# is an action offered on content nodes, not a node kind's own run), so it
# must be asked for by name.
_RUN_NODE_DEFAULT_ACTIONS = {"pycoder": "execute", "chat": "reply", "web_research": "research"}
_RUN_NODE_ACTION_KINDS = {
    "execute": ("pycoder",),
    "reply": ("chat",),
    "chart": ("chat", "note", "document", "code"),
    "research": ("web_research",),
}

_RUN_OUTPUT_EXCERPT_CHARS = 4000
_RUN_REPLY_EXCERPT_CHARS = 2000


def make_run_node_handler(document: SceneDocument, dispatcher):
    """`dispatcher` is the session's AgentDispatcher - run_node reuses its
    REPL registry (get_pycoder_repl - same REPL a manual Run would use, so
    builder runs and manual runs share one kernel per node) and its branch
    System-Prompt resolution. Execution results land on nodes through the
    SAME domain methods the dedicated surfaces call (complete_pycoder_run /
    fail_pycoder_run, add_chart_node, add_chat_node) - deliberately NOT
    re-entering the fire-and-forget AgentDispatcher surfaces themselves:
    those claim their own busy kinds, wire callbacks to intents, and offer
    no awaitable completion; this runs inline under the BUILDER's run and
    cancel event instead (the design doc's D9)."""

    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        from backend import agents as _agents  # late import + late binding (test seam)

        node_id = str(call.arguments.get("node_id") or "")
        node = document.nodes.get(node_id)
        if node is None:
            return _error(f"Unknown node: {node_id!r}.")
        action = str(call.arguments.get("action") or "") or _RUN_NODE_DEFAULT_ACTIONS.get(node.kind, "")
        if action not in _RUN_NODE_ACTION_SCOPES:
            return _error(
                f"Node {node_id!r} is kind {node.kind!r} with no default run "
                "action - pass an explicit `action` (execute, reply, chart)."
            )
        if node.kind not in _RUN_NODE_ACTION_KINDS[action]:
            return _error(
                f"Action {action!r} does not apply to a {node.kind!r} node "
                f"(accepts: {', '.join(_RUN_NODE_ACTION_KINDS[action])})."
            )
        required_scope = _RUN_NODE_ACTION_SCOPES[action]
        if required_scope not in ctx.granted_scopes:
            return _error(
                f"Tool 'run_node' action {action!r} requires scope(s) "
                f"['{required_scope}'] that this run was not granted."
            )
        if node.pending_request_id:
            return _error(f"Node {node_id!r} already has a run in flight.")

        run_id = _run_id_of(ctx)
        cancel_event = ctx.cancel.event if ctx.cancel is not None else None
        claim = run_id or "builder-run"
        # The pending stamp is what gives the builder path per-node conflict
        # guarding, the live-run undo refusal, and the spinner UI - the same
        # three things it provides every dedicated surface.
        node.pending_request_id = claim
        try:
            if action == "execute":
                return await _run_pycoder(document, dispatcher, node, node_id, cancel_event)
            if action == "reply":
                return await _run_chat(document, dispatcher, node_id, run_id, cancel_event, ctx)
            if action == "research":
                return await _run_research(document, node, node_id, claim, cancel_event)
            return await _run_chart(document, node, node_id, run_id, call, _agents, cancel_event)
        finally:
            if node.pending_request_id == claim:
                node.pending_request_id = None

    async def _run_pycoder(document, dispatcher, node, node_id, cancel_event):
        code = node.state.pycoder_code
        if not str(code).strip():
            return _error(
                f"Node {node_id!r} has no code to execute - set it first via "
                "graph.set_node_content."
            )
        repl = dispatcher.get_pycoder_repl(node_id, node.state.pycoder_repl_id)
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(repl.execute, code),
                timeout=_agents_const("PYCODER_EXECUTE_TIMEOUT_SECONDS"),
            )
            failed = bool(getattr(repl, "last_run_failed", False))
        except asyncio.TimeoutError:
            await dispatcher.dispose_pycoder_repl(node_id)
            document.fail_pycoder_run(node_id, "Execution timed out and was terminated.")
            return _error(f"Execution of node {node_id!r} timed out and was terminated.")
        output_text = output if output else "[No output produced]"
        # No analysis turn: the BUILDER model is the analyst here - it reads
        # the output itself in this same loop, so a second model call to
        # summarize it would be spend with no reader.
        document.complete_pycoder_run(node_id, code, output_text, "", failed)
        return ToolResult(content=json.dumps({
            "node_id": node_id,
            "failed": failed,
            "output": output_text[:_RUN_OUTPUT_EXCERPT_CHARS],
            "truncated": len(output_text) > _RUN_OUTPUT_EXCERPT_CHARS,
        }))

    async def _run_chat(document, dispatcher, node_id, run_id, cancel_event, ctx):
        source = document.nodes[node_id]
        if source.kind != "chat":
            return _error(f"Node {node_id!r} is not a chat node.")
        history = document.chat_branch_history(node_id)
        persona_text = dispatcher._resolve_branch_system_prompt(document, node_id)
        from backend import agents as _agents

        reply_text = await asyncio.wait_for(
            asyncio.to_thread(
                _agents._call_chat_agent, history, persona_text, cancel_event,
                model_ref=getattr(ctx, "model_ref", None),
                settings_manager=getattr(ctx, "settings_manager", None),
                runtime=getattr(ctx, "runtime", None),
            ),
            timeout=_agents_const("WATCHDOG_TIMEOUT_SECONDS"),
        )
        x, y = _place_child(document, node_id)

        def mutator():
            reply = document.add_chat_node(x, y, reply_text, False, node_id)
            provider, model = dispatcher.active_provider_model()
            reply.state.provider = provider
            reply.state.model = model
            return reply

        reply_node, _command = document.record_command(
            "builderRunChat", "agent", mutator, node_ids=[node_id], run_id=run_id,
        )
        return ToolResult(content=json.dumps({
            "reply_node_id": reply_node.id,
            "reply": reply_text[:_RUN_REPLY_EXCERPT_CHARS],
            "truncated": len(reply_text) > _RUN_REPLY_EXCERPT_CHARS,
        }))

    async def _run_research(document, node, node_id, claim, cancel_event):
        """ADR-008 stage 8.5: the net.fetch action. Runs the SAME sync
        pipeline the dedicated surface runs (WebResearchService.run - all
        of ADR-004's SSRF/IP-pinning/robots machinery included, since it
        lives inside the service's fetcher), landing results through the
        same complete/fail domain methods, inline under the builder's run.
        Cancellation bridges the builder's threading.Event onto the
        service's own CancellationToken via a watcher task - the service's
        stages checkpoint on the token, not on our event."""
        from api_provider import RequestCancelledError
        from backend.canvas import _research_result_wire
        from graphlink_plugins.web_research.domain import (
            CancellationToken,
            RequestCancelled,
            ResearchFailure,
            WebResearchRequest,
        )
        from graphlink_plugins.web_research.service import WebResearchService

        query = (node.content or "").strip()
        if not query:
            return _error(
                f"Node {node_id!r} has no research query - set it via "
                "graph.set_node_content (the node's content IS the query)."
            )
        document.start_web_research_run(node_id, query)
        parent_edge = document._branch_parent_edge(node_id)
        branch_history = (
            document.chat_branch_history(parent_edge.source) if parent_edge else []
        )
        request = WebResearchRequest(
            request_id=claim, node_id=node_id, chat_epoch=0,
            original_query=query, branch_history=list(branch_history),
        )
        token = CancellationToken()

        async def _bridge_cancel() -> None:
            while not token.cancelled:
                if cancel_event is not None and cancel_event.is_set():
                    token.cancel()
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(_bridge_cancel())
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(WebResearchService().run, request, token=token),
                timeout=_agents_const("WEB_RESEARCH_WATCHDOG_TIMEOUT_SECONDS"),
            )
        except ResearchFailure as exc:
            document.fail_web_research_run(node_id, cancelled=token.cancelled, message=str(exc))
            return _error(f"Research failed: {exc}")
        except RequestCancelled:
            # review-fix: this is a DIFFERENT class from api_provider's own
            # RequestCancelledError (same name, wrong module) - the service
            # raises it when the bridged builder Stop trips `token`.
            # Uncaught, it fell into ToolRegistry.invoke's generic
            # `except Exception`, which turns it into an ERROR ToolResult
            # fed back to the model as ordinary tool feedback instead of
            # propagating as a real cancellation - the node was also never
            # landed, leaving it wedged mid-run. Land it properly, then
            # re-raise as the type invoke() DOES special-case so Stop is
            # honored immediately instead of only at the loop's next
            # cancel_event checkpoint.
            document.fail_web_research_run(node_id, cancelled=True, message="Web research was cancelled.")
            raise RequestCancelledError("Web research was cancelled.")
        except asyncio.TimeoutError:
            token.cancel()
            document.fail_web_research_run(
                node_id, cancelled=False, message="Research timed out.",
            )
            return _error(f"Research on node {node_id!r} timed out.")
        finally:
            watcher.cancel()
        document.complete_web_research_run(node_id, _research_result_wire(result))
        return ToolResult(content=json.dumps({
            "node_id": node_id,
            "answer": result.answer_markdown[:_RUN_OUTPUT_EXCERPT_CHARS],
            "truncated": len(result.answer_markdown) > _RUN_OUTPUT_EXCERPT_CHARS,
            "sources": [s.final_url for s in result.sources][:8],
        }))

    async def _run_chart(document, node, node_id, run_id, call, _agents, cancel_event):
        from graphlink_chart_data import ChartDataError, SUPPORTED_CHART_TYPES, canonicalize_chart_data

        chart_type = str(call.arguments.get("chart_type") or "bar")
        if chart_type not in SUPPORTED_CHART_TYPES:
            return _error(
                f"Unsupported chart_type {chart_type!r}. Supported: "
                f"{', '.join(sorted(SUPPORTED_CHART_TYPES))}."
            )
        source_text = node.content or ""
        if not source_text.strip():
            return _error(f"Node {node_id!r} has no content to chart.")
        # ADR-013 stage 13.3: cancel_event (ctx.cancel.event, same primitive
        # every other run_node branch above already threads through) reaches
        # _call_chart_agent's own respond_json call now - a real
        # api_provider.RequestCancelledError on Stop, propagating straight
        # out of asyncio.to_thread and into ToolRegistry.invoke's own
        # special-casing for that exact type (see _run_web_research's own
        # comment above for why that type specifically matters).
        parsed = await asyncio.wait_for(
            asyncio.to_thread(_agents._call_chart_agent, source_text, chart_type, cancel_event),
            timeout=_agents_const("WATCHDOG_TIMEOUT_SECONDS"),
        )
        if isinstance(parsed, dict) and parsed.get("error"):
            return _error(f"Chart generation failed: {parsed['error']}")
        # review-fix: add_chart_node deliberately does NOT canonicalize
        # itself (its own docstring) - every other caller (intents_chart.py,
        # session_load.py) runs the model's raw structured output through
        # canonicalize_chart_data first. Skipping it here stored a
        # non-canonical shape, violating ChartState's documented invariant
        # (wrong containers, non-finite numbers, duplicate Sankey flows all
        # pass through uncaught).
        try:
            chart_data = canonicalize_chart_data(parsed, chart_type)
        except ChartDataError as exc:
            return _error(f"Chart generation produced invalid data: {exc}")
        x, y = _place_child(document, node_id)
        chart_node, _command = document.record_command(
            "builderRunChart", "agent",
            lambda: document.add_chart_node(x, y, node_id, chart_type, chart_data),
            node_ids=[node_id], run_id=run_id,
        )
        return ToolResult(content=json.dumps({
            "chart_node_id": chart_node.id, "chart_type": chart_type,
        }))

    return handler


def _agents_const(name: str):
    """Timeout constants read late off backend.agents so tests that shrink
    them (and any future tuning) bind at call time, not import time."""
    from backend import agents as _agents

    return getattr(_agents, name)


def register_run_node_tool(registry: ToolRegistry, document: SceneDocument, dispatcher) -> None:
    """Separate from register_graph_tools because run_node genuinely needs
    the AgentDispatcher (REPLs, persona resolution, provider identity) -
    the pure graph tools don't, and tests of those shouldn't have to build
    one. Registered with the GRAPH_READ scope only (it always reads the
    node); the kind-specific execution scope is enforced dynamically inside
    the handler per the ADR's "run_node additionally carries the scope of
    what it runs"."""
    registry.register(
        RUN_NODE_SPEC, make_run_node_handler(document, dispatcher),
        scopes={GRAPH_READ}, approval="once",
    )


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
