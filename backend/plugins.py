"""Plugin picker listing for the new architecture (Qt-removal plan R2.5,
migrated onto the ADR-014 Plugin SDK at stage 14.3).

ADR-014 stage 14.3: the 8 built-in picker actions that used to be a
hardcoded if-chain here (System Prompt, Conversation Node, Web Research,
Gitlink, Py-Coder, Virtual Environment Runner, HTML Renderer, Artifact /
Drafter) are now real discovered plugin packages under plugins/ - see
plugins/web_research/plugin.py (and its 7 siblings) for the migrated
handler bodies. Each registers via
`backend.plugin_sdk.HostContext.register_builtin_plugin` rather than
`register_node_kind`/`register_picker_entry`: their kind strings
(web_research, gitlink, pycoder, code_sandbox, html, artifact,
conversation, note) are already baked into web_ui's NODE_TYPES map, the
wire contract, and session_save.py/session_load.py's hand-written per-kind
serializers - routing them through the generic, auto-namespaced
PluginNodeSeed/add_plugin_node path would rename every one of those kinds
and be an invasive, unnecessary breaking change for zero benefit. See
HostContext.register_builtin_plugin's own docstring (backend/plugin_sdk.py)
for the full escape-hatch rationale.

This means EVERY name executePlugin sees today - built-in or third-party -
flows through the exact same generic dispatch path
(_execute_discovered_plugin below): look up plugin_registry.builtin_actions
first (the migrated built-ins' escape hatch), then
plugin_registry.picker_entries (the generic PluginNodeSeed path). There is
no longer a separate hardcoded fast path for the built-ins, and no
"_PLUGINS" static list - discover_plugins() is the single source of truth
for every picker entry's existence, and get_plugin_categories' grouping
below reflects whatever it found.

_CATEGORY_META is NOT built-in-specific scaffolding - it is the general,
fixed 5-category taxonomy any discovered plugin's `category` (built-in or
third-party) joins; it stays exactly as it was pre-migration. A category
with zero entries is skipped; anything landing in the HostContext default
"More Plugins" (or naming an unrecognized category) falls into a synthetic
catch-all appended last."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from backend.canvas import MESSAGE_VERTICAL_SPACING, SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import PluginRegistry, PluginRunContext, discover_plugins
# ADR-014 stage 14.5: register_plugin_tools' own ToolRegistry-facing
# dependencies - ToolSpec is the SAME provider-neutral shape every other
# tool family (backend/tools_graph.py, backend/mcp_client.py) builds its
# specs from; ToolResult is what a handler must return.
from backend.providers.base import ToolSpec
from backend.tools import ToolResult
from graphlink_settings_store import SettingsManager


@dataclass
class ExecutePluginArgs:
    """ADR-003 stage 3.2: args schema for executePlugin - mirrors
    execute_plugin's own signature below exactly (dataclass field order is
    the positional mapping dispatch_intent validates against)."""

    plugin_name: str
    parent_node_id: str | None = None


@dataclass
class InvokePluginIntentArgs:
    """ADR-014 stage 14.4: args schema for invokePluginIntent - mirrors
    invoke_plugin_intent's own signature below exactly (dataclass field
    order is the positional mapping dispatch_intent validates against).
    v1 supports zero-argument custom intents only: the two demo plugins
    (hello_node/counter_node) don't call HostContext.register_intent at
    all, and no production caller needs a richer variadic payload yet -
    extending this to forward real positional arguments to a plugin's own
    handler is a natural follow-up once a real third-party plugin needs
    it, not a gap this addition's own purpose (closing stage 14.1's
    deferred custom-intent-wiring gap, see backend/plugin_sdk.py's module
    docstring) requires closing now."""

    plugin_id: str
    name: str


@dataclass
class SetPluginGrantArgs:
    """ADR-014 stage 14.4: args schema for setPluginGrant - the Settings >
    Plugins page's one write path (a single checkbox per row, mirroring
    McpServersPage's own per-field setters rather than setMcpServers' whole-
    collection replace - see SettingsManager.set_plugin_grant's own
    docstring for why this intent is a single-plugin write, not a bulk
    one)."""

    plugin_id: str
    granted: bool


_CATEGORY_META = [
    {
        "name": "Branch Foundations",
        "description": "Core branch scaffolding, prompt shaping, and focused conversation structures.",
    },
    {
        "name": "Reasoning & Research",
        "description": "Deep thinking and web retrieval for exploring complex questions and grounding decisions.",
    },
    {
        "name": "Validation & Delivery",
        "description": "Acceptance reviews, branch comparison, and delivery-focused checks that harden work before release.",
    },
    {
        "name": "Build & Execution",
        "description": "Code generation, isolated execution, and rendering tools for turning ideas into working artifacts.",
    },
    {
        "name": "Workflow & Drafting",
        "description": "Agentic orchestration and structured drafting surfaces for multi-step work.",
    },
]


def get_plugin_categories(plugin_registry: "PluginRegistry | None" = None) -> list[dict[str, Any]]:
    """Groups every discovered picker entry (both `picker_entries` - the
    generic PluginNodeSeed path - and `builtin_actions` - the ADR-014 stage
    14.3 first-party escape hatch the 8 migrated built-ins use) by
    _CATEGORY_META, in that fixed order, skipping any category with zero
    entries; anything uncategorized (including every entry whose category
    doesn't match a _CATEGORY_META name) falls into a synthetic "More
    Plugins" catch-all appended last. `plugin_registry=None` (the default)
    yields an empty listing - unlike the pre-14.3 hardcoded 8, there is no
    picker entry that exists independent of a real discovered registry
    anymore."""
    entries: list[tuple[str, str, str]] = []
    if plugin_registry is not None:
        entries += [
            (entry.name, entry.description, entry.category)
            for entry in plugin_registry.picker_entries.values()
        ]
        entries += [
            (spec.name, spec.description, spec.category)
            for spec in plugin_registry.builtin_actions.values()
        ]

    categorized_names: set[str] = set()
    grouped: list[dict[str, Any]] = []

    for category in _CATEGORY_META:
        plugins = [
            {"name": name, "description": description}
            for name, description, category_name in entries
            if category_name == category["name"]
        ]
        if not plugins:
            continue
        categorized_names.update(p["name"] for p in plugins)
        grouped.append({
            "name": category["name"],
            "description": category["description"],
            "plugins": plugins,
        })

    uncategorized = [
        {"name": name, "description": description}
        for name, description, category_name in entries
        if name not in categorized_names
    ]
    if uncategorized:
        grouped.append({
            "name": "More Plugins",
            "description": "Additional plugins that do not yet belong to a dedicated flyout category.",
            "plugins": uncategorized,
        })

    return grouped


def _plugin_grants_payload(
    plugin_registry: "PluginRegistry | None", settings_manager: "SettingsManager | None",
) -> list[dict[str, Any]]:
    """ADR-014 stage 14.4: one row per DISTINCT non-built-in plugin_id - NOT
    one row per picker entry (a plugin with multiple picker entries still
    gets exactly one grants row). Built-ins never appear here at all: this
    walks `plugin_registry.picker_entries` (the generic PluginNodeSeed path
    every third-party plugin, and the two demo plugins, register through),
    never `plugin_registry.builtin_actions` (the ADR-014 stage 14.3 escape
    hatch the 8 first-party built-ins use) - the same distinction backend/
    plugins.py's own module docstring draws between the two dispatch
    mechanisms. `settings_manager=None` (a bare plugins_payload() call with
    no manager available) yields every discovered plugin as ungranted -
    the same deny-by-default answer a real manager with no stored entry for
    that plugin_id would give, just without a store to read from."""
    if plugin_registry is None:
        return []
    grants_state = settings_manager.get_plugin_grants() if settings_manager is not None else {}
    plugin_ids = sorted({entry.plugin_id for entry in plugin_registry.picker_entries.values()})
    rows = []
    for plugin_id in plugin_ids:
        manifest = plugin_registry.manifests.get(plugin_id)
        rows.append({
            "pluginId": plugin_id,
            "name": manifest.name if manifest is not None else plugin_id,
            "scopes": sorted(manifest.scopes_grants) if manifest is not None else [],
            "granted": bool(grants_state.get(plugin_id, False)),
        })
    return rows


def plugins_payload(
    plugin_registry: "PluginRegistry | None" = None,
    settings_manager: "SettingsManager | None" = None,
) -> dict[str, Any]:
    return {
        "categories": get_plugin_categories(plugin_registry),
        # ADR-014 stage 14.4: the Settings > Plugins grants list - see
        # _plugin_grants_payload's own docstring.
        "grants": _plugin_grants_payload(plugin_registry, settings_manager),
    }


async def _execute_discovered_plugin(
    bus: SessionBus,
    notifications: NotificationState,
    canvas_document: SceneDocument,
    plugin_registry: "PluginRegistry",
    settings_manager: SettingsManager,
    name: str,
    parent_node_id: str | None,
):
    """The single executePlugin dispatch path for EVERY name - built-in or
    third-party alike, since ADR-014 stage 14.3 migrated the last hardcoded
    branch off this file. Extracted to a top-level function (not nested
    inside register_plugins) so register_plugins itself stays under
    tests/test_register_function_length.py's 300-line register* cap.

    Two registration mechanisms, checked in this order:

    1. `plugin_registry.builtin_actions` - ADR-014 stage 14.3's first-party
       escape hatch (HostContext.register_builtin_plugin). 'handler' is
       SYNC and does its own record_command/parent-validation internally,
       exactly like the pre-migration hardcoded branch it replaces - this
       uniform post-handler publish rule ("scene" if the handler returned a
       real id, else "notification") matches every one of the 8 migrated
       branches, including System Prompt's dedup path, which publishes
       "scene" and returns an EXISTING id without creating anything new.
       ADR-014 stage 14.4 deliberately does NOT gate this branch on any
       grant - built-ins are first-party, ship with the app, and are never
       subject to install-time consent (see this module's own docstring).
    2. `plugin_registry.picker_entries` (via resolve_picker_name) - the
       generic PluginNodeSeed/add_plugin_node path every third-party plugin
       (and the two demo plugins, hello_node/counter_node) uses. ADR-014
       stage 14.4: gated on SettingsManager.get_plugin_grants() BEFORE the
       parent-node validation and BEFORE `kind_spec.factory(...)` ever runs -
       the same "cheap, static check first" ordering ToolRegistry.invoke()
       already uses for its own scope gate (backend/tools.py) - so an
       ungranted plugin is denied regardless of what parent was selected,
       never even reaching the factory that would otherwise mutate the
       document. This is a COARSE, binary, install-time gate ("has the user
       consented to this plugin acting at all"), not a per-scope-string
       enforcement boundary - see PluginManifest.scopes_grants' own field
       comment for the honest limit of what this actually verifies.

    A name matching neither shows the same "Unknown plugin" warning
    regardless of which mechanism a real match would have used."""
    builtin_spec = plugin_registry.builtin_actions.get(name)
    if builtin_spec is not None:
        run_ctx = PluginRunContext(plugin_id=builtin_spec.plugin_id, notifications=notifications)
        result = builtin_spec.handler(canvas_document, run_ctx, parent_node_id)
        await bus.publish("scene" if result is not None else "notification")
        return result

    resolved = plugin_registry.resolve_picker_name(name)
    if resolved is None:
        notifications.show(f'Unknown plugin: "{name}"', "warning")
        await bus.publish("notification")
        return None
    kind_spec, picker_entry = resolved

    if not settings_manager.get_plugin_grants().get(kind_spec.plugin_id, False):
        notifications.show(
            f'"{picker_entry.name}" needs your approval before it can create nodes - '
            f'grant it in Settings > Plugins.', "warning",
        )
        await bus.publish("notification")
        return None

    if not parent_node_id or parent_node_id not in canvas_document.nodes:
        notifications.show(
            f'Please select a valid node to branch from before adding a '
            f'{picker_entry.name} node.', "warning",
        )
        await bus.publish("notification")
        return None
    parent = canvas_document.nodes[parent_node_id]
    run_ctx = PluginRunContext(plugin_id=kind_spec.plugin_id, notifications=notifications)

    def _mutator():
        seed = kind_spec.factory(canvas_document, run_ctx, parent_node_id)
        return canvas_document.add_plugin_node(
            kind_spec.kind, parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id,
            title=seed.title, content=seed.content, state=seed.state,
        )

    node, _command = canvas_document.record_command(
        f"plugin:{kind_spec.kind}", "user", _mutator, node_ids=[parent_node_id],
    )
    await bus.publish("scene")
    return node.id


async def _invoke_discovered_plugin_intent(
    bus: SessionBus,
    notifications: NotificationState,
    canvas_document: SceneDocument,
    plugin_registry: "PluginRegistry",
    settings_manager: SettingsManager,
    plugin_id: str,
    name: str,
):
    """The single invokePluginIntent dispatch path - ADR-014 stage 14.4's
    resolution of stage 14.1's own deferred gap (see backend/plugin_sdk.py's
    module docstring): a plugin's HostContext.register_intent()-declared
    custom intent is inherently dynamic (unknown until a third-party plugin,
    living outside backend/, is discovered at runtime), so it can never be a
    fixed tests/undo_classification.py table entry the way a literal
    (topic, intent) pair can. Rather than wire each discovered intent onto
    the bus individually (which tests/test_undo_classification_gate.py's
    hard-locked literal-(topic, intent) invariant structurally forbids), this
    is the ONE static chokepoint every dynamically-discovered plugin intent
    funnels through - "invokePluginIntent" itself IS the literal (topic,
    intent) pair the gate sees; what it dispatches to at runtime is this
    function's own business, exactly the same relationship executePlugin
    already has to whichever picker entry/builtin action a given call
    resolves to.

    Looks up the matching PluginIntentSpec by (plugin_id, name), then checks
    the SAME grant _execute_discovered_plugin does BEFORE calling
    spec.handler - mirroring ToolRegistry.invoke()'s own "scope check runs
    before any approval/handler" ordering (backend/tools.py): an ungranted
    plugin's custom intent can never fire, a structural property of this one
    chokepoint rather than something every future plugin author has to
    remember to check themselves.

    Deliberately does NOT itself call record_command - if a plugin's own
    handler mutates the document, IT calls record_command (same "handler
    is trusted to do exactly what it needs, including its own
    record_command call" contract HostContext.register_builtin_plugin's own
    docstring already documents for the built-in escape hatch); this
    function's job ends at "was this dispatch allowed to happen at all"."""
    plugin_id = str(plugin_id).strip()
    name = str(name).strip()
    spec = next(
        (s for s in plugin_registry.intents if s.plugin_id == plugin_id and s.name == name),
        None,
    )
    if spec is None:
        notifications.show(f'Unknown plugin intent: "{plugin_id}:{name}"', "warning")
        await bus.publish("notification")
        return None

    if not settings_manager.get_plugin_grants().get(plugin_id, False):
        notifications.show(
            f'"{plugin_id}" needs your approval before it can run this action - '
            f'grant it in Settings > Plugins.', "warning",
        )
        await bus.publish("notification")
        return None

    run_ctx = PluginRunContext(plugin_id=plugin_id, notifications=notifications)
    if inspect.iscoroutinefunction(spec.handler):
        return await spec.handler(canvas_document, run_ctx)
    return spec.handler(canvas_document, run_ctx)


def register_plugin_tools(
    tool_registry, plugin_registry: "PluginRegistry", settings_manager: SettingsManager,
    canvas_document: SceneDocument,
) -> tuple[str, ...]:
    """ADR-014 stage 14.5: "tools flow to the registry" - registers every
    discovered plugin's HostContext.register_intent()-declared custom action
    as a real Builder-loop tool, namespaced `plugin:<plugin_id>:<name>`.
    Mirrors backend/mcp_client.py's own register_mcp_server_tools exactly
    (list, namespace, register with scopes + approval) - the SAME pattern,
    a different source of specs.

    Works uniformly for BOTH in-process and out-of-process plugins:
    plugin_registry.intents' PluginIntentSpec.handler is a plain Python
    callable either way - a direct function for an in-process plugin, an
    RPC-backed wrapper closure for an out-of-process one (backend/
    plugin_sdk.py's _make_worker_intent_handler) - this function never knows
    or cares which, verified against the real landed
    _invoke_discovered_plugin_intent (stage 14.4), which already treats
    every PluginIntentSpec.handler identically regardless of origin.

    `scopes=manifest.scopes_grants` - the SAME self-reported [scopes].grants
    checklist Settings > Plugins already shows (see PluginManifest.
    scopes_grants' own field comment for the "self-reported, not itself
    enforced BY ITSELF" honesty note) - ToolRegistry.invoke() DOES enforce
    it here, the same pre-handler scope gate every other tool gets.
    `approval="always"` - untrusted third-party code, the SAME posture
    register_mcp_server_tools' own McpServerConfig default takes for a
    configured MCP server (backend/mcp_client.py) - a plugin is exactly as
    trusted as a user-configured MCP server: neither is first-party.

    ALSO enforces the SAME install-time SettingsManager.get_plugin_grants()
    gate _invoke_discovered_plugin_intent already enforces for the bus-level
    invokePluginIntent path - checked INSIDE the handler below, since
    ToolRegistry itself has no install-time-consent concept of its own, only
    scopes/approval. Without this, a Builder tool call would be a SECOND,
    ungated way to fire a plugin's custom action the user never approved in
    Settings > Plugins - this closes that gap rather than leaving it open."""
    registered: list[str] = []
    for spec in plugin_registry.intents:
        manifest = plugin_registry.manifests.get(spec.plugin_id)
        namespaced_name = f"plugin:{spec.plugin_id}:{spec.name}"
        tool_spec = ToolSpec(
            name=namespaced_name,
            description=f'Invokes plugin "{spec.plugin_id}"\'s "{spec.name}" action.',
            input_schema={"type": "object", "properties": {}},
        )
        tool_registry.register(
            tool_spec,
            _make_plugin_tool_handler(plugin_registry, settings_manager, canvas_document, spec.plugin_id, spec.name),
            scopes=manifest.scopes_grants if manifest is not None else frozenset(),
            approval="always",
        )
        registered.append(namespaced_name)
    return tuple(registered)


def _make_plugin_tool_handler(
    plugin_registry: "PluginRegistry", settings_manager: SettingsManager,
    canvas_document: SceneDocument, plugin_id: str, name: str,
):
    """One handler per (plugin_id, name) - re-resolves the PluginIntentSpec
    fresh from plugin_registry.intents on every call (rather than closing
    over the spec found at registration time) so a plugin's handler is
    never called from a stale reference, mirroring
    _invoke_discovered_plugin_intent's own fresh-lookup-per-call pattern.

    NotificationState() is constructed fresh, throwaway, per call - nothing
    outside this function ever observes it. ToolRegistry's own RunContext
    has no notification channel at all (a handler's ToolResult.content is
    the ONLY thing the caller ever sees back), so a plugin handler that
    calls run_ctx.notifications.show(...) when invoked through THIS path
    has no visible effect - an honest, documented limitation, not a silent
    bug: the bus-level invokePluginIntent path (backend/plugins.py's
    _invoke_discovered_plugin_intent) is what threads a REAL, observed
    NotificationState through, for a plugin author who needs one."""

    async def _handler(call, ctx) -> ToolResult:
        if not settings_manager.get_plugin_grants().get(plugin_id, False):
            return ToolResult(
                content=f'Plugin "{plugin_id}" needs your approval in Settings > Plugins before '
                f'this action can run.',
                is_error=True,
            )
        spec = next(
            (s for s in plugin_registry.intents if s.plugin_id == plugin_id and s.name == name), None,
        )
        if spec is None:
            return ToolResult(content=f'Unknown plugin intent: "{plugin_id}:{name}".', is_error=True)
        run_ctx = PluginRunContext(plugin_id=plugin_id, notifications=NotificationState())
        result = (
            await spec.handler(canvas_document, run_ctx)
            if inspect.iscoroutinefunction(spec.handler)
            else spec.handler(canvas_document, run_ctx)
        )
        return ToolResult(content="" if result is None else str(result), is_error=False)

    return _handler


def register_plugins(
    bus: SessionBus,
    notifications: NotificationState,
    canvas_document: SceneDocument,
    settings_manager: SettingsManager,
    plugin_registry: "PluginRegistry | None" = None,
) -> None:
    # ADR-014 stage 14.1: real discovery is triggered HERE, lazily, the
    # first time a session activates - never from create_app()/backend/
    # app.py, which is constructed "dozens of times per pytest run" (its
    # own comment) and would re-glob/re-import/re-run every plugin's
    # register() on every test's app construction otherwise. discover_
    # plugins() itself is memoized by resolved plugins_root path, so every
    # session after the first pays no real cost. `plugin_registry` stays
    # injectable (None triggers the real scan) purely for test isolation -
    # the signature and every existing call site
    # (register_plugins(bus, notifications_state, canvas_document)) are
    # otherwise UNCHANGED.
    #
    # ADR-014 stage 14.3: no more `builtin_names=` argument here - the 8
    # built-ins are real discovered plugins now, checked for name
    # collisions against every OTHER plugin the same way any two plugins
    # are (PluginRegistry.picker_entries/builtin_actions, via
    # _merge_into_registry in backend/plugin_sdk.py), not via a
    # separately-supplied reserved-name set.
    if plugin_registry is None:
        plugin_registry = discover_plugins()

    # ADR-014 stage 14.2: populate the live-wire half of the Plugin SDK's
    # generic persistence seam - see SceneDocument.plugin_node_serializers'
    # own field comment (backend/domain/graph.py) for why this dict of bare
    # callables, rather than the domain layer importing PluginRegistry
    # itself, is what _node_wire's pluginState field actually reads.
    # Generic against whatever discover_plugins() found - adding a NEW
    # plugin with its own serialize hook needs no edit here.
    for kind, kind_spec in plugin_registry.node_kinds.items():
        if kind_spec.serialize is not None:
            canvas_document.plugin_node_serializers[kind] = kind_spec.serialize

    # Topic name "app-plugins" (matching the codegen artifact's derived
    # name - same reasoning as "app-composer"/"app-about"): no existing
    # "plugins" schema collision today, but the pattern is now consistent
    # across every R2.3-R2.5 topic that has a distinct SPA payload. ADR-014
    # stage 14.4: now also carries the "grants" array (settings_manager
    # threaded through so it can answer the current granted/not-granted
    # state for every non-built-in discovered plugin) - see
    # _plugin_grants_payload's own docstring.
    bus.register_topic("app-plugins", lambda: plugins_payload(plugin_registry, settings_manager))

    async def execute_plugin(plugin_name: str, parent_node_id: str | None = None):
        name = str(plugin_name).strip()
        # ADR-014 stage 14.3: every name - built-in or third-party alike -
        # flows through the same generic dispatch helper now. See
        # _execute_discovered_plugin's own docstring for the two
        # registration mechanisms it checks, in order, and (stage 14.4) the
        # grant gate the generic path now enforces.
        return await _execute_discovered_plugin(
            bus, notifications, canvas_document, plugin_registry, settings_manager, name, parent_node_id,
        )

    bus.register_intent("app-plugins", "executePlugin", execute_plugin, args_schema=ExecutePluginArgs)

    # ADR-014 stage 14.4: closes stage 14.1's own deferred gap (see this
    # module's own former comment here, and backend/plugin_sdk.py's module
    # docstring) - ONE new static (topic, intent) pair,
    # ("app-plugins", "invokePluginIntent"), whose handler looks up its real
    # target DYNAMICALLY at call time and grant-checks before dispatch. This
    # satisfies tests/test_undo_classification_gate.py's literal-(topic,
    # intent) requirement trivially (one more entry, same as "executePlugin"
    # already is) while making every dynamically-discovered plugin intent
    # name gate-compliant by construction, regardless of how many plugins/
    # intents exist at runtime - see _invoke_discovered_plugin_intent's own
    # docstring for the full mechanism.
    async def invoke_plugin_intent(plugin_id: str, name: str):
        return await _invoke_discovered_plugin_intent(
            bus, notifications, canvas_document, plugin_registry, settings_manager, plugin_id, name,
        )

    bus.register_intent(
        "app-plugins", "invokePluginIntent", invoke_plugin_intent, args_schema=InvokePluginIntentArgs,
    )

    async def set_plugin_grant(plugin_id: str, granted: bool):
        # ADR-014 stage 14.4: the Settings > Plugins page's one write path -
        # persists via SettingsManager.set_plugin_grant (single-plugin write,
        # not a whole-collection replace - see that method's own docstring),
        # then re-publishes "app-plugins" so both the picker and any
        # Settings UI observing the same topic pick up the new granted/not-
        # granted state immediately, same "mutate then re-publish this
        # topic" shape every other settings-store write in this codebase
        # already uses (e.g. setMcpServers).
        settings_manager.set_plugin_grant(str(plugin_id).strip(), bool(granted))
        await bus.publish("app-plugins")

    bus.register_intent("app-plugins", "setPluginGrant", set_plugin_grant, args_schema=SetPluginGrantArgs)
