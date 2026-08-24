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
from graphlink_scratch_dirs import HARNESS_WORKSPACE_ROOT, remove_scratch_dir_for_id
from backend.domain.node_states import (
    ArtifactState,
    ChatState,
    CodeState,
    NoteState,
    PycoderState,
)
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

# ADR-021 stage 21.1: read_subgraph walks descendants by default ("down",
# the pre-21.1 behavior). "up" is the ancestor walk chat_branch_history
# itself does (incoming edges); "both" is the neighbourhood around a node.
# The caps above apply per call regardless of direction.
_READ_DIRECTIONS = ("down", "up", "both")

# ADR-021 stage 21.1: list_nodes is an INDEX, not a read - it answers "what
# exists" so the model can then read_subgraph the part it wants. Its excerpt
# is therefore much tighter than _READ_EXCERPT_CHARS: 40 nodes x 1000 chars
# would spend a read's whole budget on an enumeration the model asked for
# precisely because it did not know what it was looking for yet.
_LIST_EXCERPT_CHARS = 160
_LIST_MAX_NODES = 40

# ADR-021 stage 21.2 widened this from the 6 first-party kinds stage 8.1
# shipped. `chart` stays out deliberately: chart creation is
# generation-coupled (there is no meaningful empty chart node - see
# add_chart_node's own contract), so run_node(action="chart") is its one
# creation path. Group/plan kinds stay out for the same reason
# graph.delete_node refuses them.
_CREATABLE_KINDS = (
    "chat", "note", "code", "document", "pycoder", "web_research",
    "html", "artifact", "conversation",
)
# Kinds whose domain factory requires a parent - the handler rejects a
# parentless create for these before touching the document.
_PARENT_REQUIRED_KINDS = (
    "document", "pycoder", "web_research", "html", "artifact", "conversation",
)


GRAPH_CREATE_NODE_SPEC = ToolSpec(
    name="graph.create_node",
    description=(
        "Create a new node on the canvas. kind must be one of: chat (a "
        "message bubble; set is_user false for assistant content), note (a "
        "free-floating sticky note), code (a code block with a language), "
        "document (a text attachment; parent required), pycoder (an "
        "executable Python node; parent required; set its code via "
        "graph.set_node_content, run it via run_node), web_research (a "
        "research node; parent required; run it via run_node), html (a "
        "rendered HTML page; parent required; content is the raw source), "
        "artifact (a long-form Markdown drafting node; parent required; "
        "starts empty), conversation (a self-contained linear chat; parent "
        "required). Returns the new node's id. Nodes are placed "
        "automatically relative to their parent - do not invent "
        "coordinates."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(_CREATABLE_KINDS)},
            "parent_id": {
                "type": "string",
                "description": "Existing node to attach under. Required for document/pycoder/web_research/html/artifact/conversation; optional for chat/code; ignored for note.",
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
        "run_node will execute), code (the source text), document (the "
        "document body), html (the raw HTML source), artifact (the whole "
        "Markdown document). Kinds whose content IS a run's output - chart, "
        "image, thinking, web_research - are read-only through this tool: "
        "re-run them instead. A node's title is not changed."
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
        "Read the subgraph around a node: the node itself plus its "
        "neighbours in `direction` (depth-limited). Returns JSON nodes "
        "[{id, kind, title, content (excerpt), truncated}], edges [{source, "
        "target}], and nodes_truncated (true if the branch has more nodes "
        "than fit in one read - narrow the root, depth, or direction to see "
        "the rest). Use graph.list_nodes first if you do not know which "
        "node to root at."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "root_id": {"type": "string"},
            "depth": {"type": "integer", "minimum": 1, "maximum": _READ_MAX_DEPTH},
            "direction": {
                "type": "string",
                "enum": list(_READ_DIRECTIONS),
                "description": "down (default): descendants. up: ancestors - the branch this node hangs off. both: the surrounding neighbourhood.",
            },
        },
        "required": ["root_id"],
    },
)

GRAPH_LIST_NODES_SPEC = ToolSpec(
    name="graph.list_nodes",
    description=(
        "List what is on the canvas, newest-last, optionally filtered. "
        "Returns JSON nodes [{id, kind, title, excerpt, truncated}] plus "
        "total/offset/returned/more. This is the way to find nodes you were "
        "not told about - graph.read_subgraph needs a root_id you already "
        "know, this does not. Filter with `kind` (exact node kind) and/or "
        "`query` (case-insensitive substring of title or content); page with "
        "`offset` when `more` is true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "description": "Only nodes of this exact kind (e.g. chat, code, pycoder, note)."},
            "query": {"type": "string", "description": "Case-insensitive substring match against title and content."},
            "offset": {"type": "integer", "minimum": 0, "description": "Skip this many matches (for paging; default 0)."},
        },
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


def run_node_pending_code(document: SceneDocument, call: ToolCall) -> str | None:
    """REVIEW-FIX: the code a run_node(action="execute") call is about to
    hand to PythonREPL.execute() - or None if `call` is not such a call.
    Exists so the approval prompt shown to a human (builder.py's
    _approval_summary) can disclose the CODE, not just the call's own
    arguments ({"node_id": ..., "action": "execute"}). The code itself
    lives on the TARGET node's state (set by an earlier, separately-
    approved graph.set_node_content call), not in this call - a human
    approving from arguments alone would be blessing a black box, unlike
    every other run_node action where the arguments already say what will
    happen. Mirrors run_node_effective_scope's "derive what the call will
    actually do before it's invoked" shape."""
    if call.name != "run_node":
        return None
    node = document.nodes.get(str(call.arguments.get("node_id") or ""))
    if node is None or node.kind != "pycoder":
        return None
    action = str(call.arguments.get("action") or "") or _RUN_NODE_DEFAULT_ACTIONS.get(node.kind, "")
    if action != "execute":
        return None
    return node.state.pycoder_code


def run_node_pending_disclosure(document: SceneDocument, call: ToolCall) -> str | None:
    """SECURITY-FIX: the human-readable thing a run_node call is about to DO
    that its own arguments ({node_id, action}) don't reveal - so the
    approval prompt can disclose it. Two cases today:

    - execute: the pycoder code (delegated to run_node_pending_code).
    - research: the web_research node's content IS the search query that
      gets sent to the external search/fetch provider (net.fetch, which
      always prompts even in autopilot). The query lived only on the node,
      so the approval summary showed 'run_node {"action":"research",...}'
      and the approver blessed an outbound network request without seeing
      what was being searched for - a query the model composed from canvas
      content it may have been prompt-injected through, i.e. an exfiltration
      channel the human had no chance to catch.

    Returns None for any other call, leaving the plain-arguments summary."""
    code = run_node_pending_code(document, call)
    if code is not None:
        return "will run this code:\n" + code
    if call.name != "run_node":
        return None
    node = document.nodes.get(str(call.arguments.get("node_id") or ""))
    if node is None or node.kind != "web_research":
        return None
    action = str(call.arguments.get("action") or "") or _RUN_NODE_DEFAULT_ACTIONS.get(node.kind, "")
    if action != "research":
        return None
    query = (node.content or "").strip()
    return "will search the web for:\n" + query if query else None


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
    same parent land side by side instead of stacked.

    review-fix (stage 8.7): the plan node can be reached by EITHER path -
    as an explicit parent_id (the executor prompt tells the model the plan
    node's own id, and nothing stops it passing that id back as parent_id
    for a chat/code node) or as the implicit anchor for a parentless create.
    The two branches below used to count siblings differently (edges vs.
    position), so a parentless create landing via the anchor branch was
    invisible to the parent branch's edge count and vice versa - two nodes
    from the two different paths could land on the exact same coordinates.
    `parent_id != anchor_id` routes that one specific case (parent_id IS the
    anchor) into the position-based branch below, which sees every node in
    the row regardless of which path placed it. A normal parent - anything
    other than the anchor - is completely unaffected."""
    if parent_id is not None and parent_id in document.nodes and parent_id != anchor_id:
        parent = document.nodes[parent_id]
        existing_children = sum(1 for e in document.edges.values() if e.source == parent_id)
        return (
            parent.x + existing_children * _SIBLING_HORIZONTAL_SPACING,
            parent.y + MESSAGE_VERTICAL_SPACING,
        )
    reference_id = parent_id or anchor_id
    if reference_id is not None and reference_id in document.nodes:
        # The anchor (a plan node) never gains an EDGE to what it builds -
        # it is a placement reference only - so there is no edge count to
        # read a sibling index off. A node already sitting on the
        # reference's own placement row is this same reference's own
        # earlier creation (nothing else places there), so counting THOSE
        # stands in for "existing children" without needing a persisted
        # counter - and, per the docstring above, catches siblings placed
        # via EITHER path.
        reference = document.nodes[reference_id]
        row_y = reference.y + MESSAGE_VERTICAL_SPACING
        row_siblings = sum(
            1 for n in document.nodes.values() if n.x >= reference.x and abs(n.y - row_y) < 1.0
        )
        return reference.x + row_siblings * _SIBLING_HORIZONTAL_SPACING, row_y
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
        if kind in _PARENT_REQUIRED_KINDS and parent_id is None:
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
            if kind == "html":
                # add_html_node takes the raw source positionally and derives
                # its own title from it; the backend never parses or
                # sanitizes that string (the preview render is entirely
                # client-side - see the factory's own docstring).
                return document.add_html_node(x, y, content, parent_id)
            if kind == "artifact":
                # Fixed title, empty document by design: artifact_content
                # only lands once a generation completes, so `content` has
                # nowhere honest to go here and is deliberately ignored.
                return document.add_artifact_node(x, y, parent_id)
            if kind == "conversation":
                return document.add_conversation_node(x, y, parent_id)
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
            # SECURITY-FIX (PYC-1): a Py-Coder node parked at its human-
            # approval gate (node.pending_request_id set while
            # AgentDispatcher.start_pycoder_run awaits approval_future) has
            # already committed to executing a SPECIFIC program - the
            # approval panel renders node.state.pycoder_code reactively,
            # but what actually runs after approval is the coroutine-local
            # `current_code` captured when the gate opened, and the
            # defense-in-depth fingerprint check compares that SAME local
            # against pycoder_approved_fingerprint, never this field. This
            # branch had no guard at all (unlike run_node/delete_node just
            # above), so a SEPARATE, auto-approved tool call - e.g. an
            # autopilot Builder run's own graph.set_node_content, needing
            # no human click - could silently swap what the panel shows
            # while the human approves what they SEE, not what runs: a
            # genuine display/execute divergence at the one gate that
            # exists specifically so a human can review code before it
            # executes. Refused the same way delete_node already refuses a
            # write against a node with a run in flight.
            if getattr(node, "pending_request_id", None):
                return _error(
                    f"Node {node_id!r} has a run in flight - wait for it to "
                    "finish or stop it before changing its code."
                )

            def mutator():
                node.state.pycoder_code = content
                return node
        elif node.kind == "code" and isinstance(node.state, CodeState):
            # CodeState.code is what the wire actually publishes for a code
            # node (graph.py's _node_wire), not SceneNode.content.
            def mutator():
                node.state.code = content
                return node
        elif node.kind == "artifact" and isinstance(node.state, ArtifactState):
            def mutator():
                node.state.artifact_content = content
                return node
        elif node.kind in ("document", "html"):
            # Both keep their body on SceneNode.content itself (the document
            # attachment's text; the html node's raw source), so this is the
            # same one-line in-place write set_note_content does.
            def mutator():
                node.content = content
                return node
        else:
            return _error(
                f"Node {node_id!r} is kind {node.kind!r} - not writable via "
                "graph.set_node_content (supported: chat, note, pycoder, "
                "code, document, html, artifact). A kind whose content is a "
                "run's own output is changed by re-running it, not by "
                "writing to it."
            )

        # Title is deliberately NOT recomputed, matching
        # update_chat_node_content's own documented posture: every in-place
        # mutator in the domain leaves title untouched post-creation.

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
        direction = str(call.arguments.get("direction") or "down")
        if direction not in _READ_DIRECTIONS:
            return _error(
                f"direction must be one of {', '.join(_READ_DIRECTIONS)}, got {direction!r}."
            )

        # BFS in the requested direction. "down" (the default, and the only
        # behavior before ADR-021 stage 21.1) walks outgoing edges - "the
        # subgraph under a node" is the branch it roots. "up" walks incoming
        # edges, the same ancestor direction chat_branch_history itself
        # walks, which is what lets a build see the branch a node hangs off
        # rather than only what hangs off it.
        walk_down = direction in ("down", "both")
        walk_up = direction in ("up", "both")
        seen = {root_id}
        frontier = [root_id]
        edges_out: list[dict[str, str]] = []
        # "both" can reach the SAME edge twice (once from each endpoint, on
        # different BFS levels), which one-directional walking never could -
        # so edge emission is deduped explicitly rather than relying on each
        # node being enqueued exactly once.
        seen_edges: set[tuple[str, str]] = set()
        nodes_truncated = False
        for _ in range(depth):
            next_frontier = []
            for node_id in frontier:
                for edge in document.edges.values():
                    if walk_down and edge.source == node_id:
                        neighbour = edge.target
                    elif walk_up and edge.target == node_id:
                        neighbour = edge.source
                    else:
                        continue
                    if neighbour not in seen:
                        if len(seen) >= _READ_MAX_NODES:
                            nodes_truncated = True
                            continue  # neither node nor edge included past the cap
                        seen.add(neighbour)
                        next_frontier.append(neighbour)
                    if (edge.source, edge.target) not in seen_edges:
                        seen_edges.add((edge.source, edge.target))
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


def make_list_nodes_handler(document: SceneDocument):
    """ADR-021 stage 21.1: the canvas enumeration read_subgraph cannot do.
    read_subgraph requires a root_id the caller already knows, and the only
    id a build is ever handed is its own plan node's - so before this tool a
    build structurally could not act on anything the user made before
    launching it. Paged and excerpted under the same discipline as
    read_subgraph (an unbounded enumeration re-enters the messages list on
    every subsequent turn), and read-only, so it registers `auto` and never
    costs a human an approval prompt."""

    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        kind_filter = str(call.arguments.get("kind") or "").strip()
        query = str(call.arguments.get("query") or "").strip().lower()
        offset_arg = call.arguments.get("offset")
        try:
            offset = int(offset_arg) if offset_arg is not None else 0
        except (TypeError, ValueError):
            return _error(f"offset must be an integer, got {offset_arg!r}.")
        if offset < 0:
            return _error(f"offset must be >= 0, got {offset}.")

        # document.nodes is insertion-ordered (creation order), so paging is
        # stable across calls: a node created mid-enumeration appends at the
        # end rather than shifting an already-returned page.
        matches = []
        for node in document.nodes.values():
            if kind_filter and node.kind != kind_filter:
                continue
            if query:
                haystack = ((node.title or "") + " " + (node.content or "")).lower()
                if query not in haystack:
                    continue
            matches.append(node)

        page = matches[offset : offset + _LIST_MAX_NODES]
        nodes_out = []
        for node in page:
            content = node.content or ""
            nodes_out.append({
                "id": node.id,
                "kind": node.kind,
                "title": node.title,
                "excerpt": content[:_LIST_EXCERPT_CHARS],
                "truncated": len(content) > _LIST_EXCERPT_CHARS,
            })
        return ToolResult(content=json.dumps({
            "nodes": nodes_out,
            "total": len(matches),
            "offset": offset,
            "returned": len(nodes_out),
            "more": offset + len(nodes_out) < len(matches),
        }))

    return handler


GRAPH_DELETE_NODE_SPEC = ToolSpec(
    name="graph.delete_node",
    description=(
        "Delete one node from the canvas, with its edges. Use this to "
        "remove something you created that turned out wrong - not to tidy "
        "up the user's own work. Refused for: a node that still has "
        "children (delete those first, so a subtree is never orphaned by "
        "one call), the build's own plan node, frames/containers, and any "
        "node with a run in flight. Deleting is undoable, and reverts with "
        "the rest of the build."
    ),
    input_schema={
        "type": "object",
        "properties": {"node_id": {"type": "string"}},
        "required": ["node_id"],
    },
)

# Kinds graph.delete_node refuses outright, with the reason the model is
# told. The plan node is the build's OWN resume point (backend/builder.py
# rebuilds a run's context from it), so deleting it mid-run would strand the
# very run making the call. Frames/containers carry membership and geometry
# semantics whose only real editor is the canvas UI - ADR-021 keeps group
# manipulation out of the Builder's surface deliberately.
_UNDELETABLE_KINDS = {
    "plan": "the plan node is this build's own resume point",
    "frame": "frames are organized on the canvas, not by the Builder",
    "container": "containers are organized on the canvas, not by the Builder",
}


def make_delete_node_handler(document: SceneDocument, dispatcher):
    """ADR-021 stage 21.2: the Builder's first destructive tool.

    Blast radius is bounded to ONE leaf node by construction: a node with
    children is refused rather than cascading, so no single call can take
    out a subtree the model only half-understood, and a partially-wrong
    branch has to be dismantled deliberately, leaf-first. That is also why
    it registers approval="always" rather than "once" - create/connect are
    additive and obvious in hindsight, but a delete destroys content the
    user may not have read yet, so every one prompts (the fingerprint means
    a genuinely repeated identical call in one run still only asks once).

    Live-resource teardown reuses backend/api/intents_nodes.py's own
    _capture_live_run_teardown + disposal sequence rather than reimplementing
    it: a deleted Py-Coder node's REPL subprocess and a deleted sandbox
    node's venv must not outlive the node whichever surface deleted it. The
    refusals above mean plan_cancels is always empty on this path, but the
    loop is kept so this stays a faithful mirror of the intent rather than a
    subset that silently drifts from it."""

    async def handler(call: ToolCall, ctx: RunContext) -> ToolResult:
        from backend.api.intents_nodes import _capture_live_run_teardown

        node_id = str(call.arguments.get("node_id") or "")
        node = document.nodes.get(node_id)
        if node is None:
            return _error(f"Unknown node: {node_id!r}.")

        refusal = _UNDELETABLE_KINDS.get(node.kind)
        if refusal is not None:
            return _error(f"Node {node_id!r} cannot be deleted: {refusal}.")
        if getattr(node, "pending_request_id", None):
            return _error(
                f"Node {node_id!r} has a run in flight - wait for it to "
                "finish or stop it before deleting."
            )
        child_ids = [e.target for e in document.edges.values() if e.source == node_id]
        if child_ids:
            return _error(
                f"Node {node_id!r} still has {len(child_ids)} child node(s) "
                f"({', '.join(child_ids[:5])}) - delete those first so no "
                "subtree is orphaned."
            )

        ids = [node_id]
        pycoder_ids, sandbox_ids, code_exec_cancels, plan_cancels, harness_workspace_ids, harness_cancels = (
            _capture_live_run_teardown(document, ids)
        )
        document.record_command(
            "builderDeleteNode", "agent", lambda: document.remove_nodes(ids),
            node_ids=ids, run_id=_run_id_of(ctx),
        )
        for kind, request_id in code_exec_cancels:
            if kind == "pycoder":
                dispatcher.cancel_pycoder(request_id)
            else:
                dispatcher.cancel_code_sandbox(request_id)
        for request_id in plan_cancels:
            dispatcher.cancel_builder(request_id)
        for request_id in harness_cancels:
            dispatcher.cancel_harness(request_id)
        for disposed_id, repl_id in pycoder_ids:
            await dispatcher.dispose_pycoder_repl(
                disposed_id, repl_id=repl_id, remove_scratch_dir=True,
            )
        for sandbox_id in sandbox_ids:
            await dispatcher.remove_code_sandbox_scratch_dir(sandbox_id)
        for workspace_id in harness_workspace_ids:
            # The same recompute-from-durable-id removal the intent path
            # uses (blank ids are refused inside remove_scratch_dir_for_id).
            await asyncio.to_thread(
                remove_scratch_dir_for_id, HARNESS_WORKSPACE_ROOT, workspace_id,
            )
        return ToolResult(content=json.dumps({"deleted": node_id, "kind": node.kind}))

    return handler


RUN_NODE_SPEC = ToolSpec(
    name="run_node",
    description=(
        "Run a node's action and return its result. Actions: execute (a "
        "pycoder node's default - runs its current code in its Python REPL; "
        "requires the code.execute scope; always prompts for human "
        "approval showing the code, even in autopilot), reply (a chat "
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
    cancel event instead (the design doc's D9).

    REVIEW-FIX: the execute action does NOT reuse start_pycoder_run's own
    dedicated approval_future/pycoder_awaiting_approval gate (that surface
    is fire-and-forget, as above) - it relies entirely on run_node's own
    registry-level approval ("once", registered below) instead. That gate
    used to be silently satisfied in autopilot (code.execute was in
    builder.py's _AUTOPILOT_AUTO_SCOPES) and, in copilot, disclosed only
    the call's own arguments (node_id/action, not the code) - the code
    lives on the target node's state, written by an earlier, separately-
    approved graph.set_node_content call the human may not connect to
    this one. Fixed at the source: _AUTOPILOT_AUTO_SCOPES no longer
    auto-approves code.execute (builder.py), and _approval_summary there
    now shows the actual code for this call via run_node_pending_code
    above - so BOTH modes now require a human to see and approve the
    code immediately before it runs, not just the tool-call shape."""

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
        # REVIEW-FIX: an ordinary user can delete node_id while the await
        # above was in flight (remove_nodes has no special-casing for a
        # chat node just because run_node has a pending request on it - see
        # run_node_pending_code's own neighbourhood for the analogous
        # pycoder case). add_chat_node below raises SceneError for a
        # missing parent BY DESIGN (its own docstring) - uncaught, that
        # would propagate out of handler() into ToolRegistry.invoke's
        # generic `except Exception`, discarding a reply the model already
        # paid for. Land it as a clean, expected tool error instead - the
        # same graceful-no-op posture fail_pycoder_run's own silent no-op
        # and intents_web_research.py's `if node_id not in document.nodes:
        # return` already give the sibling cases of this identical race.
        if node_id not in document.nodes:
            return _error(f"Node {node_id!r} no longer exists - the reply was discarded.")
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
            # REVIEW-FIX: node_id can be gone by the time this lands (a
            # concurrent user delete - remove_nodes does not special-case a
            # web_research node just because a run_node call has it
            # pending). fail_web_research_run raises SceneError for a
            # missing node BY DESIGN (its own docstring says the caller
            # must check liveness first) - guard it here rather than
            # letting that propagate out as an uncaught exception; the
            # _error result below still reaches the model either way,
            # mirroring _on_failure's own `if node_id not in document.
            # nodes: return` in the dedicated WS intent (intents_web_
            # research.py) for this identical race.
            if node_id in document.nodes:
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
            # cancel_event checkpoint. REVIEW-FIX: guarded for the same
            # missing-node race as the ResearchFailure branch above - the
            # re-raise must still happen unconditionally so Stop is
            # honored, only the landing call is conditional.
            if node_id in document.nodes:
                document.fail_web_research_run(node_id, cancelled=True, message="Web research was cancelled.")
            raise RequestCancelledError("Web research was cancelled.")
        except asyncio.TimeoutError:
            token.cancel()
            # REVIEW-FIX: same missing-node race as the ResearchFailure
            # branch above.
            if node_id in document.nodes:
                document.fail_web_research_run(
                    node_id, cancelled=False, message="Research timed out.",
                )
            return _error(f"Research on node {node_id!r} timed out.")
        finally:
            watcher.cancel()
        # REVIEW-FIX: same missing-node race as the ResearchFailure branch
        # above, on the success path. Unlike the chat/chart branches below
        # (which CREATE a new node parented on node_id and so have nothing
        # useful to return if that parent is gone), the research answer
        # itself is already fully formed here - mirror _run_pycoder's own
        # posture (complete_pycoder_run's silent no-op) rather than
        # discarding a completed result the model already paid for: skip
        # the landing call but still return it.
        if node_id in document.nodes:
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
        # REVIEW-FIX: node_id can be gone by the time this lands (same
        # concurrent-delete race as _run_chat's own identical guard above -
        # remove_nodes does not special-case a chat/note/document/code node
        # just because run_node has it pending). add_chart_node below
        # raises SceneError for a missing parent BY DESIGN - a chart with
        # no source node to attach to is not a node this call can create at
        # all, so there is nothing useful to land; fail cleanly instead of
        # letting that SceneError propagate out of handler() uncaught.
        if node_id not in document.nodes:
            return _error(f"Node {node_id!r} no longer exists - the chart was discarded.")
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
    # ADR-021 stage 21.2: delete registers HERE rather than in
    # register_graph_tools for the same reason run_node does - it needs the
    # dispatcher, to tear down a deleted node's REPL/venv and cancel any
    # run it owned. approval="always": a delete destroys content the user
    # may not have read yet, unlike the additive create/connect pair.
    registry.register(
        GRAPH_DELETE_NODE_SPEC, make_delete_node_handler(document, dispatcher),
        scopes={GRAPH_MUTATE}, approval="always",
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
    registry.register(
        GRAPH_LIST_NODES_SPEC, make_list_nodes_handler(document),
        scopes={GRAPH_READ}, approval="auto",
    )
