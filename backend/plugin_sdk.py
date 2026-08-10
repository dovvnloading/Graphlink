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

DEVIATION from the design's own sketch, recorded here: `HostContext.
register_intent()` is real and fully functional (a plugin's `register()`
call populates `PluginRegistry.intents`), but backend/plugins.py does NOT
yet wire a discovered intent onto a session's live SessionBus. That
activation step collides with a real, pre-existing, deliberately
hard-locked invariant - tests/test_undo_classification_gate.py (ADR-010
close-out) requires every `bus.register_intent()` call under backend/ to
use a source-literal `(topic, intent)` pair, so every mutating action can
be enumerated in one static, hand-reviewed undo A/B table. A
plugin-declared intent name is inherently dynamic (unknown until a
third-party plugin - living outside backend/, invisible to that gate's
own scan regardless - is discovered at runtime), so it can never be a
fixed table entry by construction. See backend/plugins.py's own comment,
right where the design's session-activation loop would have gone, for the
full reasoning. This is scoped to stage 14.4 (plugin scope/consent), an
adjacent governance decision, rather than made unilaterally here.

See doc/adr/ADR-014 stage 14.1's design for the full rationale (private,
not part of this repo)."""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
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

logger = logging.getLogger(__name__)

SDK_API_VERSION = 1
MIN_COMPATIBLE_SDK_API_VERSION = 1
MANIFEST_FILENAME = "plugin.toml"
DEFAULT_PLUGINS_ROOT = Path(__file__).resolve().parent.parent / "plugins"

_ID_PATTERN = re.compile(r"[a-z0-9_]+")


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
    in v1: no granted_scopes/approval fields yet - a later stage adds those
    as NEW FIELDS here rather than plugins needing a second, later-invented
    context object."""

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
        self, name: str, handler: PluginIntentHandler, *, args_schema: "type | None" = None,
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
        behavior is fully real and tested regardless."""
        self._intents.append(PluginIntentSpec(
            plugin_id=self.plugin_id, name=name, handler=handler, args_schema=args_schema,
        ))


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


_REGISTRY_CACHE: dict[Path, PluginRegistry] = {}


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

    frontend_table = raw.get("frontend", {})
    view = "generic"
    if isinstance(frontend_table, dict) and "view" in frontend_table:
        view = frontend_table["view"]
        if view != "generic":
            raise PluginRegistrationError(
                f'{manifest_path}: [frontend].view "{view}" is not supported before stage '
                f'14.5 - "generic" is the only legal value'
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
    key, never touching the shared cache entry for the real repo path."""
    resolved = plugins_root.resolve()
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
                module = _import_entry_module(manifest, plugin_dir)
                _module_name, _, fn_name = manifest.entry_point.partition(":")
                register_fn = getattr(module, fn_name)
                host = HostContext(manifest.id)
                register_fn(host)
                _merge_into_registry(registry, host, builtin_names)
            except Exception as exc:
                registry.load_errors.append(
                    PluginLoadError(plugin_dir.name, f"{type(exc).__name__}: {exc}")
                )
                logger.warning("plugin discovery: skipping %s (%s)", plugin_dir.name, exc)
    _REGISTRY_CACHE[resolved] = registry
    return registry
