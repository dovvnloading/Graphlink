"""Previewed, always-confirmed Workflow Library canvas edits for Builder.

Only existing executable canvas node kinds are created. No tool can start a
workflow, change a preset, issue a run token, or approve a pending step.
"""
from __future__ import annotations

import copy
import json
from contextlib import closing

from backend import workflow_library_store as library
from backend.domain.node_states import PluginStateSnapshot
from backend.providers.base import ToolSpec
from backend.tools import GRAPH_MUTATE, GRAPH_READ, ToolResult
from backend.tools_graph import _anchor_id_of, _place_child, _run_id_of
from backend.workflow import canvas

_ACTIONS = {
    "instantiate": "Insert the exact pinned library workflow revision. Arguments: workflow_id, revision.",
    "wire_inputs": "Configure a pinned workflow node. Arguments: node_id, inputs, parameters, sources (input name to source node ID).",
    "create_step": "Create a bound text operation note. Arguments: source_id, operation (copy or word_count).",
    "create_control": "Create a text input or output boundary. Arguments: kind (input or output), text for input or source_id for output.",
}
_MAX_PREVIEW = 80_000


def _spec(name, description, properties, required):
    return ToolSpec(name=name, description=description, input_schema={
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    })


def _fields(args, required):
    if not isinstance(args, dict) or set(args) != set(required):
        raise ValueError("Invalid workflow edit arguments; see the tool description.")


def register_workflow_tools(registry, document, db_path):
    """Bind a session registry to its own canvas and the global local library."""
    from backend.chat_library import _connect

    def load(workflow_id, revision):
        with closing(_connect(db_path)) as conn, conn:
            frozen = library.get(conn, workflow_id, revision)
            version = next(row["version"] for row in library.history(conn, workflow_id)
                           if row["revision"] == revision)
        return frozen, version

    def source(source_id):
        if not isinstance(source_id, str) or source_id not in document.nodes:
            raise ValueError("Choose an existing text source node.")
        node = document.nodes[source_id]
        value = document._text_output(source_id)
        return {"node_id": source_id, "title": node.title, "kind": node.kind,
                "text_fingerprint": canvas.digest(value)}

    def preview(action, args):
        if action == "instantiate":
            _fields(args, ("workflow_id", "revision"))
            frozen, version = load(args["workflow_id"], args["revision"])
            details = {"document": frozen, "version": version}
        elif action == "wire_inputs":
            _fields(args, ("node_id", "inputs", "parameters", "sources"))
            node = document.nodes[args["node_id"]]
            # Configure only a detached copy; reuse the real validation,
            # including typed inputs, source port compatibility and cycles.
            old = copy.deepcopy(canvas.data(node))
            probe = copy.copy(node)
            probe.state = PluginStateSnapshot.from_data(copy.deepcopy(old))
            canvas.configure(document, probe, args["inputs"], args["parameters"], args["sources"])
            details = {"before": old, "after": canvas.data(probe),
                       "sources": {port: source(sid) for port, sid in args["sources"].items()}}
        elif action == "create_step":
            _fields(args, ("source_id", "operation"))
            if args["operation"] not in ("copy", "word_count"):
                raise ValueError("Only copy and word_count have supported editable canvas steps.")
            details = {"source": source(args["source_id"])}
        elif action == "create_control":
            if not isinstance(args, dict):
                raise ValueError("Choose an input or output control.")
            if args.get("kind") == "input":
                _fields(args, ("kind", "text"))
                if not isinstance(args["text"], str):
                    raise ValueError("Input text must be a string.")
                details = {"role": "literal workflow input"}
            elif args.get("kind") == "output":
                _fields(args, ("kind", "source_id"))
                details = {"role": "workflow output boundary", "source": source(args["source_id"])}
            else:
                raise ValueError("Only input and output controls are supported.")
        else:
            raise ValueError("Unknown workflow edit.")
        packet = {"action": action, "arguments": args, "details": details,
                  "workspace_id": document.current_workspace_id, "starts_run": False}
        encoded = canvas.canonical(packet)
        if len(encoded.encode()) > _MAX_PREVIEW:
            raise ValueError("Workflow edit is too large to review in Builder; use the Workflow Library UI.")
        return packet

    async def list_workflows(call, ctx):
        query = call.arguments.get("query", "")
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Invalid workflow search.")
        with closing(_connect(db_path)) as conn, conn:
            rows = library.list_library(conn, query)
        return ToolResult(content=json.dumps({"workflows": rows[:40], "total": len(rows)}))

    async def preview_edit(call, ctx):
        _fields(call.arguments, ("action", "arguments"))
        packet = preview(call.arguments["action"], call.arguments["arguments"])
        return ToolResult(content=canvas.canonical(packet))

    def checked(action, call):
        _fields(call.arguments, ("review",))
        reviewed = call.arguments["review"]
        if not isinstance(reviewed, dict) or reviewed.get("action") != action:
            raise ValueError("Call workflow.preview_edit and supply its exact review object.")
        current = preview(action, reviewed.get("arguments"))
        if canvas.canonical(current) != canvas.canonical(reviewed):
            raise ValueError("Workflow or canvas changed. Preview this edit again.")
        return current

    def register_edit(action, description):
        async def precheck(call, ctx):
            checked(action, call)
            return None

        async def apply(call, ctx):
            review = checked(action, call)
            args = review["arguments"]
            watch = []
            if action == "instantiate":
                def mutate():
                    details = review["details"]
                    x, y = _place_child(document, None, _anchor_id_of(ctx), "note")
                    return canvas.insert(document, details["document"], details["version"], x, y)
            elif action == "wire_inputs":
                watch = [args["node_id"]]
                def mutate():
                    node = document.nodes[args["node_id"]]
                    canvas.configure(document, node, args["inputs"], args["parameters"], args["sources"])
                    return node.id
            else:
                source_id = args.get("source_id")
                if source_id is not None:
                    watch = [source_id]
                def mutate():
                    x, y = _place_child(document, source_id, _anchor_id_of(ctx), "note")
                    node = document.add_note(x, y)
                    if source_id is None:
                        node.content = args["text"]
                    else:
                        document.bind_note_text_input(source_id, node.id, args.get("operation", "copy"))
                    return node.id
            node_id, _command = document.record_command(
                "builderWorkflowEdit", "agent", mutate, node_ids=watch, run_id=_run_id_of(ctx),
            )
            return ToolResult(content=json.dumps({"node_id": node_id, "starts_run": False}))

        registry.register(_spec("workflow." + action,
                                description + " Requires the exact review object from workflow.preview_edit and fresh operator approval.",
                                {"review": {"type": "object"}}, ["review"]),
                          apply, scopes={GRAPH_READ, GRAPH_MUTATE}, approval="always",
                          always_reprompt=lambda call: True, pre_approval_check=precheck)

    registry.register(_spec("workflow.list", "List local Workflow Library definitions and pinned revisions.",
                            {"query": {"type": "string"}}, []),
                      list_workflows, scopes={GRAPH_READ}, approval="auto")
    registry.register(_spec("workflow.preview_edit",
                            "Preview a workflow canvas edit without mutating or running anything. "
                            + " ".join(f"{name}: {description}" for name, description in _ACTIONS.items()),
                            {"action": {"type": "string", "enum": list(_ACTIONS)},
                             "arguments": {"type": "object"}}, ["action", "arguments"]),
                      preview_edit, scopes={GRAPH_READ}, approval="auto")
    for action, description in _ACTIONS.items():
        register_edit(action, description)
