"""ADR-014 stage 14.5: the out-of-process plugin worker subprocess entry
point. Runs INSIDE the spawned child process only - launched as
`python -m backend.plugin_worker <plugin_id> <source_dir>` by
backend/plugin_sdk.py's PluginWorkerClient.connect(), never imported by the
host process itself.

THIS is the one and only place an out-of-process plugin's own Python code
(its plugin.py entry module, imported here via the SAME importlib.util.
spec_from_file_location mechanism backend/plugin_sdk.py's in-process
discovery already uses - see _load_manifest/_check_sdk_api_version/
_import_entry_module, imported and reused here verbatim, not reinvented)
ever executes. The host process never imports it, directly or transitively
- that is the entire isolation property this stage buys: whatever lives in
the HOST process's own memory (decrypted API keys read via os.environ,
DPAPI-decrypted secrets, the live SceneDocument, other sessions' state) is
simply not reachable from code running in a DIFFERENT OS process, regardless
of what that code tries to do. Resource/lifecycle containment (memory cap,
process-count cap, whole-tree kill) and the environment allowlist (this
worker never sees GRAPHLINK_*_API_KEY/etc.) are the HOST's job, applied at
Popen time (graphlink_execution_guard.create_execution_guard(),
graphlink_process_env.safe_subprocess_env()) - nothing in this module does
anything special for either; it just runs as whatever process the host
decided to spawn it as, with whatever environment the host decided to hand
it.

HONESTY NOTE on what this DOES NOT isolate: this is a process boundary
against reading the HOST's secrets/state, not a general-purpose security
sandbox. A plugin's own code, once running inside this worker process, can
still do anything a plain Python process can do on this machine - open
files, make outbound network calls, spawn its own children (bounded by the
guard's own process-count/memory caps, not blocked outright). See ADR-014
stage 14.5's own "what 14.5 does NOT do" scope note for the full, honest
boundary - matching stage 14.4's own honesty about what its grant gate does
and doesn't verify.

Serves a newline-delimited JSON-RPC 2.0 loop over stdin/stdout - the SAME
wire shape backend/mcp_client.py's McpStdioClient speaks from the CLIENT
side, mirrored here from the SERVER side: one line in, one line out, no
concurrent requests in flight (PluginWorkerClient serializes calls under one
lock, so this loop is never asked to interleave two in-flight requests).
Three methods only - "get_registrations", "invoke_factory", "invoke_intent"
- see each branch's own comment below for its exact contract.

WHY HOST-INITIATED-ONLY RPC IS SUFFICIENT: a factory needs READ access to
its parent node's public fields, but must never receive a live, mutable
SceneDocument (that would defeat isolation outright - a malicious factory
could reach into the whole graph). So a factory here receives a plain,
read-only stand-in (_WorkerDocumentStandin/_WorkerParentSnapshot below)
carrying exactly the one parent node's id/title/content/kind - nothing else
- and returns a plain dict the HOST reconstructs into a PluginNodeSeed. The
host is what calls SceneDocument.add_plugin_node(...), never this process.
This means the worker never needs to call BACK into the host mid-flight -
every exchange is one host request, one worker response - so this loop
stays a plain request/response server, never needing to become a client
itself."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

from backend.notifications import NotificationState
from backend.plugin_sdk import (
    MANIFEST_FILENAME,
    HostContext,
    PluginRunContext,
    _check_sdk_api_version,
    _import_entry_module,
    _load_manifest,
)


class _WorkerParentSnapshot:
    """A read-only stand-in for the parent SceneNode a factory would
    normally read from a live SceneDocument - carries exactly the public
    fields plugins/hello_node's own in-process factory reads (parent.title),
    plus content/kind for parity, and NOTHING else. No live document
    reference, no other node, ever crosses into this process."""

    def __init__(self, data: dict) -> None:
        self.id = str(data.get("id", ""))
        self.title = str(data.get("title", ""))
        self.content = str(data.get("content", ""))
        self.kind = str(data.get("kind", ""))


class _WorkerDocumentStandin:
    """Handed to a worker-side factory/intent handler in place of a live
    SceneDocument. `.nodes` is a plain dict - the ONE access pattern every
    real factory in this codebase uses (`document.nodes[parent_id]`, e.g.
    plugins/hello_node's own _make_hello_note) is satisfied; any OTHER
    SceneDocument attribute/method a buggy or malicious factory reaches for
    (`.edges`, `.add_node`, anything else real) raises AttributeError
    immediately - the absence of a live document is enforced by this
    class's own minimalism, not by convention or by trusting the plugin not
    to try."""

    def __init__(self, nodes: dict) -> None:
        self.nodes = nodes


def _send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _local_kind(host: HostContext, namespaced_kind: str) -> str:
    prefix = f"{host.plugin_id}."
    return namespaced_kind[len(prefix):] if namespaced_kind.startswith(prefix) else namespaced_kind


def _node_kinds_payload(host: HostContext) -> list:
    return [
        {"local_kind": _local_kind(host, kind), "requires_parent": spec.requires_parent}
        for kind, spec in host._node_kinds.items()
    ]


def _picker_entries_payload(host: HostContext) -> list:
    return [
        {
            "local_kind": _local_kind(host, entry.node_kind),
            "name": entry.name,
            "description": entry.description,
            "category": entry.category,
        }
        for entry in host._picker_entries.values()
    ]


def _intents_payload(host: HostContext) -> list:
    return [{"name": spec.name} for spec in host._intents]


def _state_to_plain_dict(state) -> "dict | None":
    """ADR-014 stage 14.5's deliberate simplification versus the in-process
    serialize/deserialize hook contract (NodeSerializeHook/NodeDeserializeHook,
    backend/plugin_sdk.py): an out-of-process plugin's own NodeState
    subclass, if it declares one, is auto-serialized here via
    dataclasses.asdict() rather than requiring the plugin author to also
    write and register an explicit serialize hook - there is no
    unserializable CALLABLE to avoid shipping across the RPC boundary the
    way an in-process factory function itself is unserializable, so
    requiring one here would be pure ceremony with no matching benefit.
    Returns None for a state that isn't a plain dataclass (never raises) -
    the node still gets created with its title/content; only the extra
    state is dropped, matching every other "degrade, don't crash" posture
    in this SDK (e.g. a raising in-process serialize hook)."""
    if state is None:
        return None
    if dataclasses.is_dataclass(state) and not isinstance(state, type):
        try:
            return dataclasses.asdict(state)
        except Exception:
            return None
    return None


def main() -> None:
    # argv[1] (plugin_id) is part of this module's own CLI contract (see the
    # module docstring's `<plugin_id> <source_dir>`, matching plugin_sdk.py's
    # own subprocess.Popen call) but never consulted here - manifest.id below
    # is the authoritative identity HostContext uses, not whatever argv claims.
    source_dir = Path(sys.argv[2])
    manifest_path = source_dir / MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path, source_dir)
    _check_sdk_api_version(manifest)
    module = _import_entry_module(manifest, source_dir)
    _module_name, _, fn_name = manifest.entry_point.partition(":")
    register_fn = getattr(module, fn_name)

    host = HostContext(manifest.id)
    register_fn(host)

    # Constructed once, never observed by anything outside this process - a
    # plugin's factory/intent handler calling run_ctx.notifications.show(...)
    # in this worker has no visible effect on the host's real session, same
    # documented limitation backend/plugins.py's register_plugin_tools()
    # accepts for its own ToolRegistry-invoked dispatch path.
    notifications = NotificationState()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue  # not JSON-RPC - skip rather than crash the whole loop
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        try:
            if method == "get_registrations":
                result = {
                    "node_kinds": _node_kinds_payload(host),
                    "picker_entries": _picker_entries_payload(host),
                    "intents": _intents_payload(host),
                }
            elif method == "invoke_factory":
                local_kind = str(params.get("kind", ""))
                namespaced_kind = f"{host.plugin_id}.{local_kind}"
                kind_spec = host._node_kinds.get(namespaced_kind)
                if kind_spec is None:
                    raise ValueError(f"unknown node kind: {local_kind!r}")
                parent_data = params.get("parent_snapshot") or {}
                parent_id = str(parent_data.get("id", ""))
                document = _WorkerDocumentStandin({parent_id: _WorkerParentSnapshot(parent_data)})
                run_ctx = PluginRunContext(plugin_id=host.plugin_id, notifications=notifications)
                seed = kind_spec.factory(document, run_ctx, parent_id)
                result = {
                    "title": seed.title,
                    "content": seed.content,
                    "state": _state_to_plain_dict(seed.state),
                }
            elif method == "invoke_intent":
                name = str(params.get("name", ""))
                spec = next((s for s in host._intents if s.name == name), None)
                if spec is None:
                    raise ValueError(f"unknown intent: {name!r}")
                document = _WorkerDocumentStandin({})
                run_ctx = PluginRunContext(plugin_id=host.plugin_id, notifications=notifications)
                value = spec.handler(document, run_ctx)
                result = {"result": value}
            else:
                _send({
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": f"unknown method: {method!r}"},
                })
                continue
        except Exception as exc:
            _send({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"},
            })
            continue
        _send({"jsonrpc": "2.0", "id": request_id, "result": result})


if __name__ == "__main__":
    main()
