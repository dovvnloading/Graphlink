"""ADR-014 stage 14.1: the Plugin SDK - manifest format, discovery, and the
host API v1 (`HostContext`) a third-party plugin's `register()` call
receives.

Two context objects, mirroring ADR-007's `ToolRegistry.register()` (trusted
caller, once) vs. `RunContext` (per-invocation) split (backend/tools.py):

- `HostContext` is constructed fresh per plugin, once, at DISCOVERY time -
  before any `SceneDocument`/bus/notifications exist for a real session, so
  it deliberately holds none of those. A plugin's `register()` call uses it
  only to declare capabilities (register_node_kind/register_picker_entry/
  register_intent).
- `PluginRunContext` is handed to a `NodeFactory`/intent handler at CALL
  time, per invocation - the SDK's analog of `RunContext`.

Discovery is LAZY + MEMOIZED (by resolved plugins_root path), first
triggered from inside `backend/plugins.py`'s `register_plugins()` - never
from `create_app()`/`backend/app.py` directly, since that constructor runs
"dozens of times per pytest run" (its own comment) and eager discovery
there would re-glob/re-import/re-run every plugin's `register()` on every
test's app construction.

Node kinds are namespaced automatically as `f"{plugin_id}.{kind}"` - no
built-in kind string contains ".", so a namespaced kind can never literally
collide with one; no reserved-kind blocklist is needed. `requires_parent`
is v1-only-True: `PluginPicker.tsx`'s `executePlugin(name, parentId)` wire
call carries no x/y for a parentless node to spawn at.

DEVIATION from the design's own sketch, recorded here (RESOLVED at stage
14.4, see below): the design's own text said a plugin's `HostContext.
register_intent()`-declared custom intent would be "wired at
SESSION-ACTIVATION time" onto the bus, one live registration per declared
intent. That collided with a real, pre-existing, deliberately hard-locked
invariant - tests/test_undo_classification_gate.py (ADR-010 close-out)
requires every `bus.register_intent()` call under backend/ to use a
source-literal `(topic, intent)` pair, so every mutating action can be
enumerated in one static, hand-reviewed undo A/B table. A plugin-declared
intent name is inherently dynamic (unknown until a third-party plugin -
living outside backend/, invisible to that gate's own scan regardless - is
discovered at runtime), so it can never be a fixed table entry by
construction. Stage 14.1 left this activation step deliberately undone,
scoped to stage 14.4 (plugin scope/consent) as the natural adjacent-
governance decision rather than made unilaterally here.

Stage 14.4 resolved it with ONE new static (topic, intent) pair instead of
one-per-declared-intent: `("app-plugins", "invokePluginIntent")`
(backend/plugins.py) looks up the real target DYNAMICALLY at call time from
`PluginRegistry.intents` and grant-checks (SettingsManager.
get_plugin_grants) before dispatch - see
`_invoke_discovered_plugin_intent`'s own docstring for the full mechanism.
`HostContext.register_intent()`/`PluginIntentSpec`/`PluginRegistry.intents`
are unchanged by this - still populated exactly as stage 14.1 built them.

ADR-014 STAGE 14.5 STATUS NOTES (out-of-process third-party execution):

- DEVIATION from the design's own sketch: the design described an
  out-of-process NodeKindSpec's `factory` wrapper closure as doing
  `asyncio.to_thread(worker_client.call, ...)`. The real, already-landed
  `_execute_discovered_plugin` (backend/plugins.py) calls `kind_spec.
  factory(...)` SYNCHRONOUSLY, inside a plain zero-arg `_mutator` closure
  passed to `SceneDocument.record_command` - never awaited, never inside
  `asyncio.to_thread` itself. Wrapping the RPC call in `asyncio.to_thread`
  would require `_mutator`/`record_command` to become async too, which
  the design's own "ZERO changes needed to _execute_discovered_plugin"
  requirement forbids. `PluginWorkerClient.call()` is therefore a plain
  synchronous, blocking method (mirroring McpStdioClient's own private
  `_call`, not its `async def _handler` wrapper) - see PluginWorkerClient's
  own class-level comment block for the full reasoning and the accepted
  consequence (an out-of-process factory call blocks the event loop for its
  RPC round-trip, exactly as an in-process third-party factory already can
  today via the same synchronous call site).
- Confirmed TRUE, not just assumed: `_execute_discovered_plugin` needed
  ZERO changes for out-of-process plugins to work - `kind_spec.factory` is
  called with the exact same 3-argument signature regardless of whether it
  is a direct Python function or an RPC-backed wrapper closure
  (_make_worker_factory below), and the stage-14.4 grant gate
  (SettingsManager.get_plugin_grants(), checked BEFORE `kind_spec.factory`
  ever runs) applies identically either way - see backend/tests/
  test_plugin_worker.py's own "D" section for the empirical proof.
- Out-of-process v1 simplification versus the in-process serialize/
  deserialize hook contract: an out-of-process plugin's own NodeState
  subclass, if any, needs NO explicit serialize/deserialize hook - the
  worker (backend/plugin_worker.py) auto-serializes it via
  `dataclasses.asdict()`, and the host wraps the resulting plain dict in a
  single generic `GenericPluginState` (below) with generic serialize/
  deserialize hooks wired onto EVERY out-of-process NodeKindSpec uniformly
  - there is no unserializable callable to avoid shipping across the RPC
  boundary the way an in-process factory function itself is unserializable,
  so requiring one here would be pure ceremony.
- What is NOT enforced, stated plainly (matching stage 14.4's own honesty
  about its grant gate): this is a process boundary against the WORKER
  reading the HOST's secrets/state (proven empirically - a planted
  GRAPHLINK_OPENAI_API_KEY is genuinely absent from the worker's own
  os.environ). It is NOT a general-purpose security sandbox - a plugin's
  own code, once running inside its worker, can still make outbound network
  calls, write files, or do anything else a plain Python process can do on
  this machine, bounded only by graphlink_execution_guard's resource caps
  (memory, active-process count), never by capability. See ADR-014 stage
  14.5's own "what 14.5 does NOT do" scope note for the full boundary.

See doc/adr/ADR-014 stage 14.1's design for the full rationale (private,
not part of this repo)."""

from __future__ import annotations

import collections
import importlib.util
import json
import logging
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Python version note: this repo's pyproject.toml declares
# requires-python = ">=3.10", but tomllib is stdlib only from 3.11 onward.
# CI/dev both run 3.12+ (tomllib present natively) so this fallback is
# currently dead in practice, but is real correctness for the declared
# support floor - not speculative. `tomli` is declared as a conditional
# dependency in pyproject.toml (`python_version < "3.11"`) for exactly this
# branch.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - not exercised on this repo's CI (3.12+)
    import tomli as tomllib  # type: ignore[no-redef]

from backend.canvas import SceneDocument, SceneNode
from backend.domain.node_states import NodeState
from backend.notifications import NotificationState
# ADR-014 stage 14.4: reused, never re-declared - a plugin manifest's
# [scopes].grants entries are validated against the SAME closed vocabulary
# backend/tools.py's ToolRegistry already gates real tool calls with, so
# "graph.mutate" means the same thing whether it is a plugin's self-reported
# manifest checklist or a tool-call's enforced scope set.
from backend.tools import KNOWN_SCOPES
# ADR-014 stage 14.5: the SAME resource-cap/env-allowlist primitives
# PythonREPL/VirtualEnvSandbox already wrap their own subprocesses with
# (graphlink_execution_guard.py, graphlink_process_env.py) - reused exactly,
# never reinvented, for the out-of-process plugin worker's own Popen call.
# See PluginWorkerClient.connect's own docstring for the full call-order
# contract these two enforce together.
from graphlink_execution_guard import create_execution_guard
# ADR-021 stage 21.4: a plugin intent's declared args_schema is a
# dataclass, described and validated with the SAME ADR-003 machinery
# every wire payload already uses - not a second, plugin-only scheme.
from graphlink_wire_schema import json_schema_for, validate_payload
from graphlink_process_env import safe_subprocess_env

logger = logging.getLogger(__name__)

SDK_API_VERSION = 1
MIN_COMPATIBLE_SDK_API_VERSION = 1
MANIFEST_FILENAME = "plugin.toml"
DEFAULT_PLUGINS_ROOT = Path(__file__).resolve().parent.parent / "plugins"
# ADR-014 stage 14.5: the repo root - one level above backend/, the SAME
# base DEFAULT_PLUGINS_ROOT above is itself derived from. Passed as `cwd=`
# to the worker subprocess's own Popen call so `python -m backend.
# plugin_worker` resolves the `backend` package via cwd-insertion into
# sys.path (Python's own documented -m behavior), regardless of whatever
# directory the HOST process itself happens to be running from (a pytest
# run's cwd is not guaranteed to be the repo root) or whether this package
# is pip-installed at all.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# ADR-014 stage 14.5: the [runtime].isolation closed vocabulary - "in-process"
# (the default, and the ONLY value every plugin shipped before this stage
# implicitly had) or "out-of-process" (this stage's new opt-in). Anything
# else is a discovery-time PluginRegistrationError, same fail-soft
# per-plugin-skip posture as every other malformed-manifest case.
KNOWN_RUNTIME_ISOLATION = frozenset({"in-process", "out-of-process"})

_ID_PATTERN = re.compile(r"[a-z0-9_]+")

# Both halves of an entry_point ("<module>:<callable>"). The module half is
# interpolated straight into a filesystem path - `plugin_dir / f"{module}.py"`
# in _import_entry_module - and that file is then EXECUTED, so it has to be a
# bare module name and nothing else. Validating only the ":" split (all this
# used to do) let a manifest say `entry_point = "../../../evil:register"`,
# which resolves and executes a .py file OUTSIDE the plugin's own directory,
# in the host process. A single path separator, drive letter, or ".." is the
# whole exploit, so the allowlist is deliberately narrow: a leading letter or
# underscore followed by word characters, which is what every real plugin
# already uses ("plugin"). The callable half must likewise be a plain Python
# identifier - it is fed to getattr on the imported module.
_ENTRY_POINT_PART_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class PluginRegistrationError(Exception):
    """Raised for one plugin's own malformed manifest, bad register() call,
    or a picker-name collision. Always raised at DISCOVERY time, never at
    request-handling time. discover_plugins() catches this (and any other
    exception a plugin's own register() raises) PER PLUGIN and skips just
    that one - a bad plugin.toml must never take down every other plugin
    or the app itself."""


@dataclass(frozen=True)
class PluginManifest:
    """The parsed, validated shape of one plugin's plugin.toml [plugin]
    (+ optional [frontend]) table."""

    id: str
    name: str
    version: str
    sdk_api_version: int
    entry_point: str
    description: str
    view: str  # always "generic" in this stage; validated at load
    source_dir: Path
    # ADR-014 stage 14.4: the plugin's SELF-REPORTED declared-scope checklist
    # from an optional [scopes] table - a subset of backend.tools.KNOWN_SCOPES
    # (reused, never re-declared), validated at discovery time so an unknown
    # scope string fails this ONE plugin's load, same fail-soft per-plugin
    # skip as every other manifest error. Omitting [scopes] entirely yields
    # frozenset() here - NOT the same as "trusted": this stage has no way to
    # verify a plugin's code actually stays within what it declares (real
    # capability sandboxing is stage 14.5's job, out-of-process execution);
    # this field only feeds the Settings grants list's read-only "scopes"
    # column and the discovery-time validation above. The separate, actually
    # enforced gate - "has the user granted this plugin at all" - lives in
    # SettingsManager.get_plugin_grants()/set_plugin_grant(), consulted by
    # backend/plugins.py's _execute_discovered_plugin BEFORE any factory
    # call, not here.
    scopes_grants: frozenset[str] = frozenset()
    # ADR-014 stage 14.5: "in-process" (the default - discover_plugins()
    # imports the plugin's own module directly into the HOST process, byte-
    # identical to every plugin's behavior before this stage) or
    # "out-of-process" (discover_plugins() instead spawns a resident worker
    # subprocess - backend/plugin_worker.py - and never imports this
    # plugin's code into the host at all). See KNOWN_RUNTIME_ISOLATION's own
    # comment and _discover_out_of_process_plugin's docstring for the full
    # mechanism this opts into.
    runtime_isolation: str = "in-process"


@dataclass(frozen=True)
class PluginNodeSeed:
    """What a plugin's NodeFactory returns - the kind-specific slice of a
    new node. Position, id, kind, and the parent edge are ALL host-decided
    (SceneDocument.add_plugin_node) so a v1 factory cannot get positioning
    or id-minting wrong; only title/content/state are its job.

    'state' IS the real ADR-002 seam: a plugin MAY define its own dataclass
    subclassing NodeState in its own module and pass an instance here,
    exactly like the built-in per-kind dataclasses attach to
    SceneNode.state. This works TODAY for the live session: WS updates and
    undo/redo both operate on the live/deepcopied SceneNode object, which
    is kind-agnostic (record_command's snapshot is
    copy.deepcopy(self.nodes[nid]) - no isinstance check anywhere).

    ADR-014 stage 14.2 closed the two gaps this docstring used to name here
    (live-wire visibility beyond title/content, and save/reload dropping the
    node entirely): a plugin that wants either now passes `serialize`/
    `deserialize` to `HostContext.register_node_kind` - see that method's own
    docstring and NodeKindSpec's own fields."""

    title: str
    content: str = ""
    state: NodeState | None = None


@dataclass
class GenericPluginState(NodeState):
    """ADR-014 stage 14.5: the host-side stand-in for an OUT-OF-PROCESS
    plugin's PluginNodeSeed.state. An out-of-process plugin's own NodeState
    subclass (if it declares one, e.g. counter_node's real CounterState) is
    defined inside the WORKER subprocess and can never be imported by the
    host - the host importing third-party code at all, for an
    out-of-process plugin, is exactly the thing this stage's isolation
    property forbids. So instead of a typed subclass, the host wraps
    whatever plain, JSON-safe dict the worker's invoke_factory response
    carried under "state" (backend/plugin_worker.py auto-serializes a
    dataclass instance via dataclasses.asdict() on the worker side - no
    explicit serialize/deserialize hook is required of an out-of-process
    plugin author, unlike the in-process contract, since there is no
    unserializable callable to avoid shipping across the RPC boundary here).

    _out_of_process_state_serialize/_out_of_process_state_deserialize below
    are the two generic (never per-plugin) hooks wired onto EVERY
    out-of-process NodeKindSpec by _discover_out_of_process_plugin - so this
    class's own `data` dict IS both the live-wire pluginState payload and
    the persisted save-file plugin_state payload, unchanged, exactly
    mirroring NodeSerializeHook's existing dual call-site contract."""

    data: dict = field(default_factory=dict)


def _out_of_process_state_serialize(node: SceneNode) -> dict:
    if isinstance(node.state, GenericPluginState):
        return dict(node.state.data)
    return {}


def _out_of_process_state_deserialize(data: dict) -> "NodeState | None":
    return GenericPluginState(data=dict(data)) if data else None


NodeFactory = Callable[[SceneDocument, "PluginRunContext", str], PluginNodeSeed]
PluginIntentHandler = Callable[..., Any]  # (document, run_ctx, *args) -> Any; sync or async
# ADR-014 stage 14.2: the generic persistence/wire seam. `NodeSerializeHook`
# is reused for BOTH the live WS wire's pluginState field (backend/domain/
# graph.py's _node_wire, via SceneDocument.plugin_node_serializers - see that
# field's own comment for why the domain layer consults a plain dict of
# callables rather than importing this module) AND the persisted save file's
# "plugin_state" key (backend/session_save.py) - one function a plugin author
# writes once, read from two different call sites for two different
# purposes. `NodeDeserializeHook` is the save-side-only mirror (there is no
# live-wire equivalent to deserialize INTO - the wire is write-only from the
# backend's perspective).
NodeSerializeHook = Callable[[SceneNode], "dict[str, Any]"]
NodeDeserializeHook = Callable[["dict[str, Any]"], "NodeState | None"]


@dataclass(frozen=True)
class PluginRunContext:
    """Handed to a NodeFactory/intent handler at CALL time, per invocation -
    the SDK's analog of backend/tools.py's RunContext. Deliberately minimal
    in v1: no granted_scopes/approval fields here.

    ADR-014 stage 14.4 (plugin scope/consent) resolved the gap this
    docstring used to flag ("a later stage adds those as NEW FIELDS here")
    WITHOUT adding fields here after all: the grant check
    (SettingsManager.get_plugin_grants, deny-by-default) runs entirely in
    backend/plugins.py's _execute_discovered_plugin/
    _invoke_discovered_plugin_intent, BEFORE a PluginRunContext is even
    constructed for a given call - a plugin whose factory/intent handler
    actually runs has, by construction, already passed the gate, so there is
    nothing for the handler itself to inspect or re-check via its own
    context object. This is deliberately COARSER than backend/tools.py's own
    RunContext.granted_scopes (a per-tool-call scope SET a handler can
    consult mid-call) - 14.4's gate is a single install-time yes/no per
    plugin, not a scope-by-scope capability boundary; see PluginManifest.
    scopes_grants' own field comment for the honest limit of what a
    manifest's declared scopes actually verify."""

    plugin_id: str
    notifications: NotificationState


@dataclass(frozen=True)
class NodeKindSpec:
    plugin_id: str
    kind: str  # ALREADY namespaced: f"{plugin_id}.{local_kind}"
    factory: NodeFactory
    requires_parent: bool  # always True in v1
    # ADR-014 stage 14.2: OPTIONAL persistence/wire hooks - see
    # HostContext.register_node_kind's own docstring for the full contract.
    # A plugin that passes neither still gets its node's universal title/
    # content/is_collapsed fields persisted and reloaded (session_save.py's
    # own generic fallback default) - these two are strictly for a plugin's
    # OWN NodeState subclass fields, beyond that baseline.
    serialize: NodeSerializeHook | None = None
    deserialize: NodeDeserializeHook | None = None


@dataclass(frozen=True)
class PickerEntrySpec:
    plugin_id: str
    name: str  # the picker label AND executePlugin's dispatch key - same
    # name-doubles-as-key contract every built-in already uses
    description: str
    category: str
    node_kind: str  # references a NodeKindSpec.kind, same plugin


@dataclass(frozen=True)
class PluginIntentSpec:
    plugin_id: str
    name: str  # local name; namespaced at session-wiring time as
    # f"plugin:{plugin_id}:{name}"
    handler: PluginIntentHandler
    args_schema: "type | None" = None
    # ADR-021 stage 21.4: the JSON Schema for args_schema, when the dataclass
    # TYPE itself is not available in this process. That is exactly the
    # out-of-process case: the host never imports a sandboxed plugin's
    # module, so it cannot hold its dataclass - but it still needs the schema
    # to describe the tool to the model. The worker sends the schema up at
    # get_registrations time and validates/constructs on its own side, where
    # the real type does live. In-process specs leave this None and derive
    # the schema from args_schema directly (see intent_input_schema).
    args_json_schema: "dict | None" = None


# ADR-014 stage 14.3: the first-party migration escape hatch. 'document' is
# the live SceneDocument, 'run_ctx' the same per-invocation context a
# NodeFactory receives, 'parent_node_id' whatever executePlugin's wire call
# carried (may be None/unknown - the handler validates it itself, exactly
# like every pre-migration hardcoded branch did). Returns the created/
# resolved node's id on success, or None if it already showed its own
# notification - see HostContext.register_builtin_plugin's own docstring for
# the full contract this signature encodes.
BuiltinActionHandler = Callable[[SceneDocument, PluginRunContext, "str | None"], "str | None"]


@dataclass(frozen=True)
class BuiltinActionSpec:
    plugin_id: str
    name: str  # the picker label AND executePlugin's dispatch key - same
    # name-doubles-as-key contract PickerEntrySpec already uses
    description: str
    category: str
    handler: BuiltinActionHandler


class HostContext:
    """Constructed fresh per plugin, once, at discovery time - NEVER shared
    across plugins, so plugin_id provenance is free on every call and one
    plugin can never see or mutate another's declarations. Holds no
    bus/document/notifications: those don't exist yet at discovery time."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self._node_kinds: dict[str, NodeKindSpec] = {}  # namespaced kind -> spec
        self._picker_entries: dict[str, PickerEntrySpec] = {}  # display name -> entry
        self._builtin_actions: dict[str, BuiltinActionSpec] = {}  # display name -> spec
        self._intents: list[PluginIntentSpec] = []

    def register_node_kind(
        self,
        kind: str,
        factory: NodeFactory,
        *,
        requires_parent: bool = True,
        serialize: NodeSerializeHook | None = None,
        deserialize: NodeDeserializeHook | None = None,
    ) -> None:
        """Registers 'factory' as this plugin's creation primitive for
        'kind'. The kind actually stored (and later stamped onto
        SceneNode.kind) is namespaced as f"{plugin_id}.{kind}" - a
        structural guarantee, not just convention: no built-in kind string
        contains ".", so a namespaced kind can never literally collide with
        one, and no separate reserved-kind blocklist is needed.

        v1 ONLY supports requires_parent=True. False is rejected outright:
        PluginPicker.tsx's executePlugin(name, parentId) wire call carries
        no x/y at all, so a parentless node has no host-decided place to
        spawn.

        ADR-014 stage 14.2: 'serialize'/'deserialize' are the OPTIONAL
        generic persistence/wire seam for a plugin's own NodeState subclass
        (the "'state' IS the real ADR-002 seam" seam PluginNodeSeed's own
        docstring names). Neither is required - a plugin with no state
        (like plugins/hello_node/, which fits its one string in `content`)
        passes neither and its node's title/content/is_collapsed still
        round-trips through save/reload via session_save.py's generic
        fallback default; a plugin WITH real state opts in to round-trip it
        too:
          - serialize(node) -> dict: called with the live SceneNode both by
            backend/domain/graph.py's _node_wire (the live WS wire's
            pluginState field - see SceneDocument.plugin_node_serializers'
            own comment for why THAT call site is wired separately, from
            outside this class, to keep the domain layer import-free of
            this module) and by backend/session_save.py (the persisted
            "plugin_state" key). Must return a plain, JSON-serializable
            dict - session_save.py drops (never crashes on) a dict that
            isn't; the live-wire path coerces every value through str()
            regardless (SceneNodeRow.pluginState is dict[str, str], the
            same "narrowest accurate supertype under a closed schema
            generator" precedent already established by ResearchResultRow.
            providerSnapshot - see contracts/graphlink_scene_payload.py).
          - deserialize(data) -> NodeState | None: the save-side-only
            mirror, called by backend/session_load.py with whatever dict
            serialize(node) most recently produced, to reconstruct the
            plugin's own NodeState subclass instance. A raised exception
            here is caught by the caller - the node still restores with
            its title/content, just without its extra state - never a
            failed load."""
        if not requires_parent:
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": requires_parent=False is not supported in SDK '
                f"v1 - the picker's executePlugin(name, parentId) call carries no spawn "
                f'position for a parentless node.'
            )
        if not kind or not all(c.isalnum() or c == "_" for c in kind):
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": invalid node kind "{kind}" '
                f'(must be non-empty, letters/digits/underscore only)'
            )
        namespaced = f"{self.plugin_id}.{kind}"
        if namespaced in self._node_kinds:
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": node kind "{kind}" already registered'
            )
        self._node_kinds[namespaced] = NodeKindSpec(
            plugin_id=self.plugin_id, kind=namespaced, factory=factory, requires_parent=True,
            serialize=serialize, deserialize=deserialize,
        )

    def register_picker_entry(
        self, *, node_kind: str, name: str, description: str, category: str = "More Plugins",
    ) -> None:
        """Adds one row to the Plugins picker. PluginPicker.tsx already
        renders whatever the "app-plugins" topic sends (fully data-driven,
        zero frontend change needed). 'node_kind' is the LOCAL kind string
        passed to register_node_kind - must already be registered BY THIS
        SAME plugin. 'category' matching one of backend/plugins.py's fixed
        _CATEGORY_META names joins that existing flyout; any other string
        (including the default "More Plugins") falls into the existing
        synthetic catch-all."""
        namespaced = f"{self.plugin_id}.{node_kind}"
        if namespaced not in self._node_kinds:
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": register_picker_entry references node_kind '
                f'"{node_kind}" but register_node_kind("{node_kind}", ...) was not called first'
            )
        if name in self._picker_entries:
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": picker entry "{name}" already registered by '
                f'this plugin'
            )
        self._picker_entries[name] = PickerEntrySpec(
            plugin_id=self.plugin_id, name=name, description=description,
            category=category, node_kind=namespaced,
        )

    def register_builtin_plugin(
        self, *, name: str, description: str, category: str, handler: BuiltinActionHandler,
    ) -> None:
        """ADR-014 stage 14.3: the first-party migration escape hatch - lets
        a plugin's register() call attach a picker entry directly to an
        existing, already-rich SceneDocument mutator (e.g.
        add_web_research_node) rather than going through register_node_kind/
        PluginNodeSeed/add_plugin_node's generic, auto-namespaced wire
        format. Exists ONLY to migrate the 8 pre-SDK built-in picker actions
        onto real plugin packages under plugins/ without renaming their
        already-shipped, already-persisted kind strings (web_research,
        gitlink, pycoder, code_sandbox, html, artifact, conversation, note) -
        renaming any of those would be an invasive, unnecessary breaking
        change across the frontend's NODE_TYPES map, the wire contract, and
        session_save.py/session_load.py's hand-written per-kind
        serializers, for zero benefit. A third-party plugin should almost
        always use register_node_kind/register_picker_entry instead; this
        method is NOT namespaced and performs NO validation of any kind
        (node existence, parent liveness, ...) - 'handler' is trusted to do
        exactly what it needs, including its own record_command call
        (with whatever command_type string it chooses), its own
        parent-validation notification text, and its own
        return-None-on-failure/return-created-or-resolved-id-on-success
        contract.

        'handler' is SYNC, not async - every one of the 8 branches this
        method replaces has a fully synchronous body (record_command's
        mutator is always a plain zero-arg closure); wrapping it in an
        async def would be needless ceremony this call path has no use for.
        The caller (backend/plugins.py's _execute_discovered_plugin) applies
        one uniform post-handler rule around every registered handler:
        publish "scene" if the handler returned a real id, else publish
        "notification" - matching every one of the 8 migrated branches'
        own "show warning, return None" vs "create/resolve, return id"
        shape, including System Prompt's dedup path (resolves an EXISTING
        note's id, creates nothing new, still publishes "scene").

        'name' is the picker label AND executePlugin's dispatch key - same
        name-doubles-as-key contract PickerEntrySpec already uses. Same-
        plugin name reuse (two register_builtin_plugin calls with equal
        'name' from ONE plugin) is an error, raised here; cross-plugin
        collisions (against every other plugin's register_picker_entry AND
        register_builtin_plugin names) are checked once, globally, by
        _merge_into_registry - not here, since a single HostContext never
        sees another plugin's declarations."""
        if name in self._builtin_actions:
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": builtin action "{name}" already registered by '
                f"this plugin"
            )
        self._builtin_actions[name] = BuiltinActionSpec(
            plugin_id=self.plugin_id, name=name, description=description,
            category=category, handler=handler,
        )

    def register_intent(
        self, name: str, handler: PluginIntentHandler, *,
        args_schema: "type | None" = None,
        args_json_schema: "dict | None" = None,
    ) -> None:
        """Declares one custom action beyond node creation. NOT used by the
        trivial demo plugin (node creation alone proves the loop) - included
        because ADR-014's own stage description names "node kind/intent/
        picker" as the three registration surfaces this stage's host API
        must offer. Stored in PluginRegistry.intents, namespaced at the
        REGISTRY layer as f"plugin:{plugin_id}:{name}" so two plugins each
        naming their own intent "run" can never collide. 'handler' is
        called as handler(document, run_ctx, *args) and may be sync or
        async.

        DEVIATION from the design's own sketch: live SessionBus wiring
        (actually dispatching this over the wire) is deliberately NOT done
        in stage 14.1 - see this module's own docstring for why (collides
        with tests/test_undo_classification_gate.py's hard-locked
        literal-(topic,intent) invariant). This method's registration-time
        behavior is fully real and tested regardless.

        ADR-014 review-fix: rejects a same-name duplicate from THIS SAME
        plugin, mirroring register_picker_entry/register_builtin_plugin's
        own duplicate-name guards immediately above - self._intents is a
        list (not a dict keyed by name) purely because two DIFFERENT
        plugins may legitimately each declare an intent named "run" (the
        registry namespaces by plugin_id, not this class), but a single
        plugin declaring "run" twice is always an authoring mistake, never
        legitimate. Left unguarded, a duplicate here would resolve
        (silently, to whichever entry happened first) at TWO downstream
        chokepoints: _invoke_discovered_plugin_intent's own next(...)
        lookup, and register_plugin_tools' ToolRegistry.register(), where
        it previously raised "Tool ... is already registered" and aborted
        registration for every plugin enumerated after the offender -
        see register_plugin_tools' own docstring (backend/plugins.py) for
        the belt-and-suspenders per-item isolation added there too."""
        if any(spec.name == name for spec in self._intents):
            raise PluginRegistrationError(
                f'plugin "{self.plugin_id}": intent "{name}" already registered by '
                f"this plugin"
            )
        self._intents.append(PluginIntentSpec(
            plugin_id=self.plugin_id, name=name, handler=handler,
            args_schema=args_schema, args_json_schema=args_json_schema,
        ))


_NO_ARGS_SCHEMA = {"type": "object", "properties": {}}


def intent_input_schema(spec: PluginIntentSpec) -> dict:
    """ADR-021 stage 21.4: the JSON Schema describing one intent's arguments,
    for the Builder tool spec that exposes it.

    Before 21.4 this was hardcoded to the empty object at the one call site
    (backend/plugins.py's register_plugin_tools) and `args_schema` was stored
    but read by nothing at all - so a plugin's action was invocable by the
    model but never parameterizable, which made a plugin tool close to
    useless as a tool.

    Three cases, in priority order: a pre-generated schema (out-of-process,
    where this process has no access to the plugin's own dataclass), a
    dataclass type (in-process, generated with the SAME ADR-003 machinery
    every wire payload uses), or no declared arguments at all - which stays
    exactly the empty object it always was, so an intent that never declared
    a schema behaves identically to before this stage."""
    if spec.args_json_schema is not None:
        return spec.args_json_schema
    if spec.args_schema is not None:
        return json_schema_for(spec.args_schema)
    return dict(_NO_ARGS_SCHEMA)


def build_intent_arguments(spec: PluginIntentSpec, arguments: dict) -> "tuple[tuple, list[str]]":
    """Validate a caller's raw argument dict against `spec` and turn it into
    the positional tail its handler is called with.

    Returns (args_tuple, errors). On success with a declared schema the tail
    is a single constructed dataclass instance, so a plugin author works with
    their own typed object rather than a bare dict. An intent with NO
    declared schema gets an empty tail - handler(document, run_ctx), byte-
    identical to the pre-21.4 call - and any arguments a model invented for
    it are rejected rather than silently dropped, since accepting them would
    teach the model that a call it got wrong had worked.

    Validation is only ever performed where the real dataclass lives: this
    function is called host-side for in-process specs and worker-side for
    out-of-process ones. It deliberately does NOT try to validate against a
    bare args_json_schema - a host holding only the schema forwards the raw
    dict to the worker, which owns the type and validates there."""
    if spec.args_schema is None:
        if arguments:
            return (), [
                f'intent "{spec.name}" takes no arguments, got '
                f"{sorted(arguments)}"
            ]
        return (), []
    errors = validate_payload(arguments, spec.args_schema)
    if errors:
        return (), errors
    return (spec.args_schema(**arguments),), []


def resolve_intent_call_args(
    spec: PluginIntentSpec, arguments: dict,
) -> "tuple[tuple, list[str]]":
    """The one place the in-process/out-of-process split is decided, so no
    caller has to re-derive it (and get the ORDER wrong: an out-of-process
    spec has args_schema=None, which build_intent_arguments would otherwise
    read as "declares no arguments" and reject every real call).

    Out-of-process: the host holds only the JSON schema, so the caller's raw
    dict is forwarded and the worker validates it against the real dataclass
    on its own side. In-process: validated and constructed here."""
    if spec.args_schema is None and spec.args_json_schema is not None:
        return (dict(arguments),), []
    return build_intent_arguments(spec, arguments)


@dataclass
class PluginLoadError:
    plugin_dir: str
    message: str


class PluginRegistry:
    def __init__(self) -> None:
        self.node_kinds: dict[str, NodeKindSpec] = {}  # namespaced kind -> spec
        self.picker_entries: dict[str, PickerEntrySpec] = {}  # display name -> entry
        self.builtin_actions: dict[str, BuiltinActionSpec] = {}  # display name -> spec
        self.intents: list[PluginIntentSpec] = []
        self.load_errors: list[PluginLoadError] = []
        # ADR-014 stage 14.4: plugin_id -> its own parsed manifest, populated
        # ONLY for a plugin that finished loading successfully (same
        # all-or-nothing posture as every other registry dict here - a
        # plugin whose merge collided never gets an entry either). The
        # Settings grants payload (backend/plugins.py's plugins_payload)
        # reads this for each non-built-in plugin's display name and
        # declared scopes list - the registry held no manifest at all before
        # this stage, since nothing needed one past discovery time.
        self.manifests: dict[str, PluginManifest] = {}
        # ADR-014 stage 14.5: plugin_id -> its RESIDENT worker subprocess
        # client, populated ONLY for a successfully-discovered
        # out-of-process plugin (see _discover_out_of_process_plugin's own
        # docstring for the spawn-once-at-discovery,
        # kept-alive-for-process-lifetime v1 lifecycle choice - a crashed
        # worker's plugin fails until the app restarts, not a live-reload
        # story). Empty for every registry whose plugins are all in-process
        # (the overwhelming common case, including every built-in and both
        # original demo plugins - neither hello_node nor counter_node opts
        # in). A caller that wants to release these subprocesses explicitly
        # (tests; a future app-shutdown hook) iterates this dict and calls
        # .close() on each - production code today has no such caller,
        # matching how backend/agents.py's own self._mcp_clients are never
        # explicitly closed either (both rely on OS process-exit cleanup, an
        # accepted gap ADR-007 stage 8.5 already ships with, not new here).
        self.worker_clients: dict[str, "PluginWorkerClient"] = {}

    def resolve_picker_name(self, name: str) -> "tuple[NodeKindSpec, PickerEntrySpec] | None":
        entry = self.picker_entries.get(name)
        if entry is None:
            return None
        return self.node_kinds[entry.node_kind], entry

    def resolve_builtin_action(self, name: str) -> "BuiltinActionSpec | None":
        """ADR-014 stage 14.3's lookup mirror of resolve_picker_name above -
        None for any name that isn't a registered builtin action (including
        one that IS a real picker_entries name; the two dicts are checked
        by the caller in whichever order it prefers, since _merge_into_
        registry already guarantees no name is ever in both)."""
        return self.builtin_actions.get(name)


# ---------------------------------------------------------------------------
# ADR-014 stage 14.5: out-of-process plugin worker client.
#
# PluginWorkerClient is the HOST side of a plugin worker subprocess - one
# instance per out-of-process plugin, spawned once at discovery time and
# kept resident for the process's lifetime (see PluginRegistry.
# worker_clients' own comment). It closely mirrors backend/mcp_client.py's
# McpStdioClient: newline-delimited JSON-RPC 2.0 over stdio, a reader thread
# pushing parsed dicts onto a queue.Queue, auto-incrementing integer id
# correlation, a _READER_CLOSED sentinel for "the worker's stdout closed
# unexpectedly." Reused as a direct template, not reinvented - the ONE real
# difference is that `call()` is a synchronous, blocking method (matching
# McpStdioClient's own private `_call`), never wrapped in `async def` here:
# the caller this stage actually has (an out-of-process NodeKindSpec's
# `factory` field, invoked synchronously inside backend/plugins.py's
# `_execute_discovered_plugin` -> `record_command` -> a plain zero-arg
# `_mutator` closure, never awaited) is itself fully synchronous by
# construction (NodeFactory = Callable[[SceneDocument, PluginRunContext,
# str], PluginNodeSeed], no `async` in that signature) - wrapping `call()`
# in `asyncio.to_thread` the way backend/mcp_client.py's own
# `_make_mcp_handler` does would require also making the wrapper factory
# `async def` and awaiting it, which would in turn require making
# `_mutator`/`record_command` async too. That is a real, deliberate
# DEVIATION from this stage's own design sketch (which described the
# wrapper closure as doing `asyncio.to_thread(...)`) - see this module's own
# stage-14.5 status notes for why the sketch's assumption did not match the
# real, already-landed `record_command` contract. The practical consequence:
# an out-of-process plugin's node-creation RPC round-trip blocks the event
# loop for its duration, exactly like an IN-PROCESS third-party plugin's
# factory already can today (that call is ALSO synchronous, ALSO inside the
# same `_mutator`) - this is not a new regression, just the same
# already-accepted architecture extended to a second call mechanism.
# ---------------------------------------------------------------------------

# A reader-thread sentinel distinct from any real JSON-RPC payload (a plain
# dict) - see McpStdioClient's own _READER_CLOSED for the identical
# reasoning; a fresh instance here rather than importing backend/
# mcp_client.py's, so this module has no import-time dependency on that one.
_WORKER_READER_CLOSED = object()

_DEFAULT_WORKER_TIMEOUT = 30.0


class PluginWorkerError(RuntimeError):
    """Raised for any plugin-worker-level failure: the subprocess failed to
    start, closed its output unexpectedly (crashed, or exited before
    responding), returned a JSON-RPC error response, or didn't respond
    within the configured timeout. Callers (discover_plugins() at discovery
    time, an RPC-backed factory/intent wrapper at call time) let this
    propagate as a plain exception - discover_plugins()'s existing per-
    plugin `except Exception` catches it at discovery time exactly like any
    other PluginRegistrationError; backend/app.py's existing WS intent
    dispatch catch-all (`except Exception` around `session.dispatch_intent`)
    catches it at call time exactly like any other handler bug - NEITHER
    needed a single new line of exception-handling code to cover this,
    since both were already generic, pre-existing infrastructure."""


class PluginWorkerClient:
    """One resident worker subprocess for ONE out-of-process plugin -
    construct, connect() once, then call() as needed, close() when done
    (app shutdown; a test's own teardown). Not reentrant across concurrent
    callers beyond the one in-flight-request-at-a-time serialization
    `_call_lock` already provides internally - matching McpStdioClient's own
    concurrency contract exactly."""

    def __init__(self, *, plugin_id: str, source_dir: Path, timeout: float = _DEFAULT_WORKER_TIMEOUT):
        self.plugin_id = plugin_id
        self.source_dir = source_dir
        self.timeout = timeout
        self._process: subprocess.Popen | None = None
        self._guard = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        # A bounded tail of the worker's stderr, drained continuously by its
        # own thread. Without a dedicated drainer, a worker that writes more
        # than the OS pipe buffer (~4 KiB on Windows, measured) to stderr
        # before we read it blocks on its own stderr write - and since stderr
        # is only ever read after stdout already closed, its stdout response
        # never arrives and call() hangs until the timeout on every request.
        # A traceback from an uncaught exception in plugin code is easily
        # that large. Mirrors McpStdioClient's own stderr drainer.
        self._stderr_tail: "collections.deque[str]" = collections.deque(maxlen=200)
        self._responses: "queue.Queue[Any]" = queue.Queue()
        self._next_id = 0
        self._write_lock = threading.Lock()
        self._call_lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def connect(self) -> None:
        """Spawns the worker subprocess. Call order, matching
        graphlink_execution_guard.create_execution_guard's own documented
        contract exactly: (1) create the guard, (2) Popen with
        guard.popen_kwargs() merged in - POSIX applies its rlimits between
        fork and exec, so the guard must exist BEFORE the spawn, (3)
        guard.assign(process.pid) once the real pid exists. `env=
        safe_subprocess_env()` (graphlink_process_env.py's allowlist, NOT a
        blocklist of secret names) is the load-bearing containment
        primitive for this whole stage - the worker never inherits the
        host's GRAPHLINK_*_API_KEY/etc. environment variables, so a plugin's
        own os.environ.get(...) call structurally cannot recover them. `cwd=
        _REPO_ROOT` makes `-m backend.plugin_worker` resolve regardless of
        the host process's own current directory - see _REPO_ROOT's own
        comment."""
        if self._process is not None:
            return
        self._guard = create_execution_guard()
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "backend.plugin_worker", self.plugin_id, str(self.source_dir)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=safe_subprocess_env(),
                cwd=str(_REPO_ROOT),
                text=True,
                bufsize=1,
                **self._guard.popen_kwargs(),
            )
        except OSError as exc:
            raise PluginWorkerError(
                f'Failed to start plugin worker for "{self.plugin_id}": {exc}'
            ) from exc
        self._guard.assign(self._process.pid)
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        # Drain stderr continuously (see _stderr_tail's own comment): a
        # worker that fills the stderr pipe buffer would otherwise block on
        # its own stderr write and never send its stdout response.
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except Exception:
                pass
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        # Exactly-once close, whether or not a process was ever spawned -
        # create_execution_guard()'s own close() is itself idempotent
        # (double-close-safe, see graphlink_execution_guard.py's own
        # adversarial-review fix comment), but this guards the ATTRIBUTE
        # (None-out after) so a second PluginWorkerClient.close() call is
        # unambiguously a no-op too.
        guard, self._guard = self._guard, None
        if guard is not None:
            guard.close()

    def call(self, method: str, params: dict) -> dict:
        """Synchronous, blocking - see this section's own module-level
        comment for why. Returns the JSON-RPC response's "result" object
        (or {} if absent), matching McpStdioClient._call's own return
        contract exactly.

        ADR-014 review-fix: an explicit is_connected check up front, so a
        call on an already-dead worker (crashed since a PRIOR call - the
        reader thread already pushed _WORKER_READER_CLOSED and self._process
        has exited, but the attribute itself is only cleared by close(),
        which nothing calls automatically here) fails immediately with the
        documented PluginWorkerError rather than reaching _write() and
        racing whatever raw OSError the dead pipe happens to produce (empirically
        confirmed: `OSError: [Errno 22] Invalid argument` writing to a dead
        process's stdin on Windows - not a BrokenPipeError, so it silently
        escaped _write()'s prior narrower except clause). _write() itself
        also now catches OSError as defense in depth for the remaining race
        (the process dying between this check and the actual write)."""
        if not self.is_connected:
            raise PluginWorkerError(f'Plugin worker "{self.plugin_id}" is not connected.')
        with self._call_lock:
            self._next_id += 1
            request_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PluginWorkerError(
                        f'Plugin worker "{self.plugin_id}" timed out after {self.timeout}s '
                        f"calling {method!r}."
                    )
                try:
                    payload = self._responses.get(timeout=remaining)
                except queue.Empty:
                    raise PluginWorkerError(
                        f'Plugin worker "{self.plugin_id}" timed out after {self.timeout}s '
                        f"calling {method!r}."
                    )
                if payload is _WORKER_READER_CLOSED:
                    stderr_tail = self._stderr_tail_text()
                    raise PluginWorkerError(
                        f'Plugin worker "{self.plugin_id}" closed its output unexpectedly '
                        f"while calling {method!r}."
                        + (f" stderr: {stderr_tail}" if stderr_tail.strip() else "")
                    )
                if not isinstance(payload, dict) or payload.get("id") != request_id:
                    # Malformed/unsolicited line - can't happen from a
                    # well-behaved worker (one call in flight at a time
                    # under _call_lock), but a corrupted or unexpected line
                    # must not be mistaken for our response. Matches
                    # McpStdioClient._call's own tolerance exactly.
                    continue
                if "error" in payload:
                    error = payload["error"]
                    message_text = error.get("message", error) if isinstance(error, dict) else error
                    raise PluginWorkerError(f'plugin worker "{self.plugin_id}" {method} failed: {message_text}')
                return payload.get("result", {}) or {}

    # -- JSON-RPC framing (mirrors McpStdioClient's own private methods) ----

    def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue  # not a JSON-RPC line - skip, don't crash the reader
                self._responses.put(payload)
        finally:
            self._responses.put(_WORKER_READER_CLOSED)

    def _drain_stderr(self) -> None:
        """Continuously read the worker's stderr into a bounded ring buffer,
        so a worker writing a large traceback to stderr can't fill the pipe
        and block its own stdout response - see _stderr_tail's own comment.
        Runs for the life of the process; ends at stderr EOF."""
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                self._stderr_tail.append(line)
        except (ValueError, OSError):
            # stderr closed out from under us (close() race) - nothing left
            # to drain.
            pass

    def _stderr_tail_text(self, limit: int = 2000) -> str:
        """The most recent stderr output, bounded to `limit` chars, for the
        unexpected-close diagnostic. Reads the ring buffer the drainer
        thread fills, never the raw pipe (which the drainer owns)."""
        return "".join(self._stderr_tail)[-limit:]

    def _write(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise PluginWorkerError(f'Plugin worker "{self.plugin_id}" is not connected.')
        with self._write_lock:
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                # ADR-014 review-fix: OSError (BrokenPipeError's own actual
                # base class) replaces the narrower (BrokenPipeError,
                # ValueError) this used to catch - empirically, writing to a
                # crashed worker's stdin raises a PLAIN OSError on Windows
                # (`[Errno 22] Invalid argument`), not BrokenPipeError, so it
                # previously escaped uncaught as a raw platform-specific
                # exception instead of the documented PluginWorkerError every
                # other failure mode here raises. BrokenPipeError still
                # matches first where it's the real cause (POSIX); this is a
                # strict widening, not a behavior change for the case that
                # already worked.
                raise PluginWorkerError(
                    f'Plugin worker "{self.plugin_id}" is not accepting input: {exc}'
                ) from exc


def _worker_parent_snapshot(document: SceneDocument, parent_id: str) -> dict:
    """The data-in half of the "data-in/data-out only" factory contract an
    out-of-process plugin's factory gets, instead of a live SceneDocument -
    see this module's own stage-14.5 status notes ("Why host-initiated-only
    RPC is sufficient") for the full reasoning. Exactly the parent node's
    public fields (id/title/content/kind) - NOT the whole graph."""
    parent = document.nodes[parent_id]
    return {"id": parent.id, "title": parent.title, "content": parent.content, "kind": parent.kind}


def _make_worker_factory(worker_client: PluginWorkerClient, local_kind: str) -> NodeFactory:
    """One RPC-backed wrapper closure per out-of-process node kind - the
    SAME NodeFactory shape (Callable[[SceneDocument, PluginRunContext, str],
    PluginNodeSeed]) an in-process plugin's own factory function has, so it
    slots into backend/plugins.py's `_execute_discovered_plugin` dispatch
    with ZERO changes needed there (verified against the real landed code,
    not just asserted - see this module's own stage-14.5 status notes)."""

    def _factory(document: SceneDocument, run_ctx: "PluginRunContext", parent_id: str) -> PluginNodeSeed:
        result = worker_client.call(
            "invoke_factory",
            {"kind": local_kind, "parent_snapshot": _worker_parent_snapshot(document, parent_id)},
        )
        raw_state = result.get("state")
        state = GenericPluginState(data=dict(raw_state)) if isinstance(raw_state, dict) else None
        return PluginNodeSeed(
            title=str(result.get("title", "")), content=str(result.get("content", "")), state=state,
        )

    return _factory


def _make_worker_intent_handler(worker_client: PluginWorkerClient, name: str) -> PluginIntentHandler:
    """One RPC-backed wrapper closure per out-of-process custom intent - a
    plain sync callable, the same shape HostContext.register_intent already
    accepts for an in-process handler. `document`/`run_ctx` are accepted
    (matching PluginIntentHandler's own signature) but deliberately not
    forwarded across the RPC boundary - handing a sandboxed plugin the live
    document would defeat the isolation the worker exists for.

    ADR-021 stage 21.4: arguments ARE now forwarded, as a plain JSON dict.
    The host cannot construct the plugin's own dataclass (it never imports
    the plugin's module - that is the whole point of out-of-process), so the
    raw dict crosses the boundary and the WORKER validates and constructs it
    against the real type on its own side. `arguments` is the caller's dict
    rather than a built dataclass instance for exactly that reason."""

    def _handler(document: SceneDocument, run_ctx: "PluginRunContext", arguments: dict | None = None):
        result = worker_client.call(
            "invoke_intent", {"name": name, "args": dict(arguments or {})},
        )
        return result.get("result")

    return _handler


def _discover_out_of_process_plugin(
    manifest: PluginManifest, plugin_dir: Path,
) -> "tuple[HostContext, PluginWorkerClient]":
    """The out-of-process counterpart of discover_plugins()'s existing
    in-process branch (import module, construct HostContext, call
    register(host) directly) - spawns a resident PluginWorkerClient, asks it
    for its plugin's own registrations via "get_registrations", and
    reconstructs them into a REAL HostContext using HostContext's OWN
    PUBLIC register_node_kind/register_picker_entry/register_intent methods
    (never hand-built dataclass instances) - so namespacing/validation is
    byte-identical to the in-process path, and _merge_into_registry (the
    caller) needs no special-casing at all for an out-of-process host's
    declarations.

    The worker's "get_registrations" response reports each node kind's
    LOCAL (non-namespaced) name - re-namespacing it here via
    host.register_node_kind(local_kind, ...) mints the exact same
    f"{manifest.id}.{local_kind}" string the worker's own HostContext
    already minted internally, since manifest.id == the worker's own
    plugin_id by construction (_load_manifest already enforces
    plugin_id == plugin_dir.name on both sides of the RPC boundary
    independently).

    On ANY failure (connect failure, a malformed/erroring RPC response, a
    registration collision inside the reconstructed HostContext itself) the
    worker subprocess is closed here and the exception re-raised - the
    caller's own per-plugin `except Exception` records one load_errors entry
    and moves on to the next plugin, and no half-alive worker is left
    resident for a plugin that never actually finished loading.

    Returns `(host, worker_client)` rather than registering the worker into
    a PluginRegistry itself - the CALLER (discover_plugins()) is what adds
    it to `registry.worker_clients`, and only AFTER `_merge_into_registry`
    has ALSO succeeded, so a plugin that spawns a working worker but then
    loses a picker-name collision against a sibling plugin does not leave
    an orphaned resident subprocess behind for a plugin that never actually
    finished loading."""
    worker_client = PluginWorkerClient(plugin_id=manifest.id, source_dir=plugin_dir)
    try:
        worker_client.connect()
        response = worker_client.call("get_registrations", {})
        host = HostContext(manifest.id)
        for entry in response.get("node_kinds", []):
            host.register_node_kind(
                str(entry["local_kind"]),
                _make_worker_factory(worker_client, str(entry["local_kind"])),
                requires_parent=True,
                serialize=_out_of_process_state_serialize,
                deserialize=_out_of_process_state_deserialize,
            )
        for entry in response.get("picker_entries", []):
            host.register_picker_entry(
                node_kind=str(entry["local_kind"]),
                name=str(entry["name"]),
                description=str(entry.get("description", "")),
                category=str(entry.get("category", "More Plugins")),
            )
        for entry in response.get("intents", []):
            name = str(entry["name"])
            host.register_intent(
                name, _make_worker_intent_handler(worker_client, name),
                # stage 21.4: the worker generated this from its own
                # dataclass; the host stores it purely to describe the tool.
                args_json_schema=entry.get("args_schema"),
            )
    except Exception:
        worker_client.close()
        raise
    return host, worker_client


_REGISTRY_CACHE: dict[Path, PluginRegistry] = {}
# ADR-014 review-fix: guards the whole check-then-scan-then-set sequence
# below against a concurrent-first-caller race. discover_plugins() is
# UNREACHABLE via any real production call path today with more than one OS
# thread in play (backend/agents.py:519, backend/plugins.py:499, backend/
# session_load.py, backend/session_save.py all call it from plain
# synchronous code on the event-loop thread, never inside asyncio.to_thread
# or a second thread) - this lock is prevention against a future regression,
# not a fix for an active leak. Without it, two threads racing a
# never-before-seen plugins_root could both miss the cache, both scan, and
# (for an out-of-process plugin) both spawn a REAL resident worker
# subprocess - the loser's PluginRegistry (and its live PluginWorkerClient)
# is then simply discarded by whichever thread's `_REGISTRY_CACHE[resolved]
# = registry` assignment runs last, orphaning the winner's subprocess with
# no reference anywhere ever able to close() it. Held across the ENTIRE
# scan (not just the get/set), which also serializes two genuinely
# DIFFERENT plugins_root paths against each other - an accepted, harmless
# cost (discovery is a rare, one-time-per-path operation) for the simplest
# correct fix.
_REGISTRY_CACHE_LOCK = threading.Lock()


def _load_manifest(manifest_path: Path, plugin_dir: Path) -> PluginManifest:
    try:
        with manifest_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise PluginRegistrationError(f"malformed TOML in {manifest_path}: {exc}") from exc

    plugin_table = raw.get("plugin")
    if not isinstance(plugin_table, dict):
        raise PluginRegistrationError(f"{manifest_path}: missing required [plugin] table")

    required = ("id", "name", "version", "sdk_api_version", "entry_point")
    missing = [key for key in required if key not in plugin_table]
    if missing:
        raise PluginRegistrationError(
            f"{manifest_path}: [plugin] table missing required field(s): {', '.join(missing)}"
        )

    plugin_id = plugin_table["id"]
    if not isinstance(plugin_id, str) or not _ID_PATTERN.fullmatch(plugin_id):
        raise PluginRegistrationError(
            f"{manifest_path}: [plugin].id must match [a-z0-9_]+, got {plugin_id!r}"
        )
    if plugin_id != plugin_dir.name:
        raise PluginRegistrationError(
            f'{manifest_path}: [plugin].id "{plugin_id}" must equal this plugin\'s own '
            f'directory name "{plugin_dir.name}"'
        )

    sdk_api_version = plugin_table["sdk_api_version"]
    if not isinstance(sdk_api_version, int) or isinstance(sdk_api_version, bool):
        raise PluginRegistrationError(
            f"{manifest_path}: [plugin].sdk_api_version must be an integer"
        )

    entry_point = plugin_table["entry_point"]
    if not isinstance(entry_point, str) or entry_point.count(":") != 1:
        raise PluginRegistrationError(
            f'{manifest_path}: [plugin].entry_point must be "<module>:<callable>", got '
            f"{entry_point!r}"
        )
    module_name, _, fn_name = entry_point.partition(":")
    if not module_name or not fn_name:
        raise PluginRegistrationError(
            f'{manifest_path}: [plugin].entry_point must be "<module>:<callable>", got '
            f"{entry_point!r}"
        )
    # Both halves must be plain identifiers - see _ENTRY_POINT_PART_PATTERN's
    # own comment for the path-traversal-into-code-execution this blocks.
    if not _ENTRY_POINT_PART_PATTERN.fullmatch(module_name):
        raise PluginRegistrationError(
            f"{manifest_path}: [plugin].entry_point module {module_name!r} must be a plain "
            "module name (letters, digits and underscores) - it names a file inside this "
            "plugin's own directory, never a path"
        )
    if not _ENTRY_POINT_PART_PATTERN.fullmatch(fn_name):
        raise PluginRegistrationError(
            f"{manifest_path}: [plugin].entry_point callable {fn_name!r} must be a plain "
            "Python identifier"
        )

    frontend_table = raw.get("frontend", {})
    view = "generic"
    if isinstance(frontend_table, dict) and "view" in frontend_table:
        view = frontend_table["view"]
        if view != "generic":
            raise PluginRegistrationError(
                f'{manifest_path}: [frontend].view "{view}" is not supported before stage '
                f'14.5 - "generic" is the only legal value'
            )

    # ADR-014 stage 14.5: optional [runtime] table - see PluginManifest.
    # runtime_isolation's own field comment. Absent entirely -> "in-process",
    # byte-identical to every plugin's discovery-time behavior before this
    # stage existed.
    runtime_table = raw.get("runtime", {})
    runtime_isolation = "in-process"
    if isinstance(runtime_table, dict) and "isolation" in runtime_table:
        runtime_isolation = str(runtime_table["isolation"])
        if runtime_isolation not in KNOWN_RUNTIME_ISOLATION:
            raise PluginRegistrationError(
                f'{manifest_path}: [runtime].isolation "{runtime_isolation}" is not a known '
                f"isolation mode - must be one of {sorted(KNOWN_RUNTIME_ISOLATION)}"
            )

    # ADR-014 stage 14.4: optional [scopes] table - see PluginManifest.
    # scopes_grants' own field comment for the full "self-reported checklist,
    # not an enforced boundary" contract. Absent entirely -> frozenset()
    # (still requires an explicit Settings grant to run at all - that gate
    # is independent of what a plugin declares here).
    scopes_table = raw.get("scopes", {})
    scopes_grants: frozenset[str] = frozenset()
    if isinstance(scopes_table, dict) and "grants" in scopes_table:
        raw_grants = scopes_table["grants"]
        if not isinstance(raw_grants, list) or not all(isinstance(g, str) for g in raw_grants):
            raise PluginRegistrationError(
                f"{manifest_path}: [scopes].grants must be a list of strings"
            )
        scopes_grants = frozenset(raw_grants)
        unknown_scopes = scopes_grants - KNOWN_SCOPES
        if unknown_scopes:
            raise PluginRegistrationError(
                f"{manifest_path}: [scopes].grants contains unknown scope(s) "
                f"{sorted(unknown_scopes)} - must be a subset of {sorted(KNOWN_SCOPES)}"
            )

    return PluginManifest(
        id=plugin_id,
        name=str(plugin_table["name"]),
        version=str(plugin_table["version"]),
        sdk_api_version=int(sdk_api_version),
        entry_point=entry_point,
        description=str(plugin_table.get("description", "")),
        view=view,
        source_dir=plugin_dir,
        scopes_grants=scopes_grants,
        runtime_isolation=runtime_isolation,
    )


def _check_sdk_api_version(manifest: PluginManifest) -> None:
    if not (MIN_COMPATIBLE_SDK_API_VERSION <= manifest.sdk_api_version <= SDK_API_VERSION):
        raise PluginRegistrationError(
            f'plugin "{manifest.id}": sdk_api_version {manifest.sdk_api_version} is outside '
            f"the supported range [{MIN_COMPATIBLE_SDK_API_VERSION}, {SDK_API_VERSION}]"
        )


def _import_entry_module(manifest: PluginManifest, plugin_dir: Path):
    """Imports the entry_point's module via a SYNTHETIC unique module name
    registered in sys.modules - never a bare sys.path insert - so two
    plugins each shipping their own "plugin.py" can never collide or shadow
    each other."""
    module_name, _, fn_name = manifest.entry_point.partition(":")
    module_path = plugin_dir / f"{module_name}.py"
    if not module_path.is_file():
        raise PluginRegistrationError(
            f'plugin "{manifest.id}": entry_point module not found: {module_path}'
        )
    synthetic_name = f"_graphlink_plugin__{manifest.id}__{module_name}"
    spec = importlib.util.spec_from_file_location(synthetic_name, module_path)
    if spec is None or spec.loader is None:
        raise PluginRegistrationError(
            f'plugin "{manifest.id}": could not load entry_point module {module_path}'
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[synthetic_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(synthetic_name, None)
        raise
    if not hasattr(module, fn_name):
        raise PluginRegistrationError(
            f'plugin "{manifest.id}": entry_point callable "{fn_name}" not found in {module_path}'
        )
    return module


def _merge_into_registry(
    registry: PluginRegistry, host: HostContext, builtin_names: frozenset[str]
) -> None:
    """Folds one plugin's HostContext declarations into the shared registry.
    Node-kind collisions are structurally impossible (namespaced per
    plugin_id, checked already within HostContext itself) so need no check
    here. Picker NAME collisions - against 'builtin_names' AND against
    every already-merged plugin's picker_entries/builtin_actions - ARE
    checked here, raising rather than silently overwriting.

    ADR-014 stage 14.3: 'builtin_names' has no real caller-supplied argument
    left in this repo (the 8 pre-SDK built-ins are now real discovered
    plugins themselves, checked against every other plugin the same way any
    two plugins are), but stays a real, generic, tested SDK mechanism - any
    host embedding this SDK can still reserve a name that has no registry
    entry of its own yet. 'builtin_actions' is the ADR-014 stage 14.3 sibling
    of 'picker_entries' - a name must be globally unique across BOTH dicts,
    checked below, so the picker's displayed/dispatched name space is one
    flat namespace regardless of which registration mechanism produced an
    entry."""
    for name in host._picker_entries:
        if name in builtin_names:
            raise PluginRegistrationError(
                f'plugin "{host.plugin_id}": picker entry "{name}" collides with a '
                f"built-in plugin name"
            )
        existing = registry.picker_entries.get(name) or registry.builtin_actions.get(name)
        if existing is not None:
            raise PluginRegistrationError(
                f'plugin "{host.plugin_id}": picker entry "{name}" collides with the same '
                f'entry already registered by plugin "{existing.plugin_id}"'
            )
    for name in host._builtin_actions:
        if name in builtin_names:
            raise PluginRegistrationError(
                f'plugin "{host.plugin_id}": builtin action "{name}" collides with a '
                f"built-in plugin name"
            )
        existing = registry.picker_entries.get(name) or registry.builtin_actions.get(name)
        if existing is not None:
            raise PluginRegistrationError(
                f'plugin "{host.plugin_id}": builtin action "{name}" collides with the same '
                f'entry already registered by plugin "{existing.plugin_id}"'
            )
    # Two passes (validate-all-then-merge-all) so a collision on the SECOND
    # picker entry/builtin action a plugin declares doesn't leave the FIRST
    # one partially merged into the shared registry.
    for kind, spec in host._node_kinds.items():
        registry.node_kinds[kind] = spec
    for name, entry in host._picker_entries.items():
        registry.picker_entries[name] = entry
    for name, spec in host._builtin_actions.items():
        registry.builtin_actions[name] = spec
    registry.intents.extend(host._intents)


def discover_plugins(
    plugins_root: Path = DEFAULT_PLUGINS_ROOT, *, builtin_names: frozenset[str] = frozenset(),
) -> PluginRegistry:
    """Real filesystem scan + import, memoized by resolved path so a
    process that calls this many times (every create_app() in a pytest
    run, via register_plugins) only pays the real cost once. Tests that
    need isolation pass a distinct tmp_path-derived plugins_root - a fresh
    key, never touching the shared cache entry for the real repo path.

    ADR-014 review-fix: the whole check-then-scan-then-set body now runs
    under _REGISTRY_CACHE_LOCK - see that lock's own module-level comment
    for the concurrent-first-caller race this closes and why holding it for
    the full scan (not just the dict access) is the right, simplest fix."""
    resolved = plugins_root.resolve()
    with _REGISTRY_CACHE_LOCK:
        cached = _REGISTRY_CACHE.get(resolved)
        if cached is not None:
            return cached

        registry = PluginRegistry()
        if resolved.is_dir():
            for manifest_path in sorted(resolved.glob(f"*/{MANIFEST_FILENAME}")):
                plugin_dir = manifest_path.parent
                try:
                    manifest = _load_manifest(manifest_path, plugin_dir)
                    _check_sdk_api_version(manifest)
                    # ADR-014 stage 14.5: the ONLY branch point discovery
                    # gained this stage. "in-process" (every plugin before
                    # this stage, and every plugin that omits [runtime]
                    # entirely) keeps the EXACT pre-14.5 path - import the
                    # module into the host process, call its real
                    # register(host) directly - byte-identical to before.
                    # "out-of-process" never imports the plugin's module
                    # into this process at all; see
                    # _discover_out_of_process_plugin's own docstring.
                    worker_client = None
                    if manifest.runtime_isolation == "out-of-process":
                        host, worker_client = _discover_out_of_process_plugin(manifest, plugin_dir)
                    else:
                        module = _import_entry_module(manifest, plugin_dir)
                        _module_name, _, fn_name = manifest.entry_point.partition(":")
                        register_fn = getattr(module, fn_name)
                        host = HostContext(manifest.id)
                        register_fn(host)
                    try:
                        _merge_into_registry(registry, host, builtin_names)
                    except Exception:
                        if worker_client is not None:
                            worker_client.close()
                        raise
                    registry.manifests[manifest.id] = manifest
                    if worker_client is not None:
                        registry.worker_clients[manifest.id] = worker_client
                except Exception as exc:
                    registry.load_errors.append(
                        PluginLoadError(plugin_dir.name, f"{type(exc).__name__}: {exc}")
                    )
                    logger.warning("plugin discovery: skipping %s (%s)", plugin_dir.name, exc)
        _REGISTRY_CACHE[resolved] = registry
        return registry
