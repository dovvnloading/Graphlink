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

from dataclasses import dataclass
from typing import Any

from backend.canvas import MESSAGE_VERTICAL_SPACING, SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import PluginRegistry, PluginRunContext, discover_plugins


@dataclass
class ExecutePluginArgs:
    """ADR-003 stage 3.2: args schema for executePlugin - mirrors
    execute_plugin's own signature below exactly (dataclass field order is
    the positional mapping dispatch_intent validates against)."""

    plugin_name: str
    parent_node_id: str | None = None

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


def plugins_payload(plugin_registry: "PluginRegistry | None" = None) -> dict[str, Any]:
    return {"categories": get_plugin_categories(plugin_registry)}


async def _execute_discovered_plugin(
    bus: SessionBus,
    notifications: NotificationState,
    canvas_document: SceneDocument,
    plugin_registry: "PluginRegistry",
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
    2. `plugin_registry.picker_entries` (via resolve_picker_name) - the
       generic PluginNodeSeed/add_plugin_node path every third-party plugin
       (and the two demo plugins, hello_node/counter_node) uses.

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


def register_plugins(
    bus: SessionBus,
    notifications: NotificationState,
    canvas_document: SceneDocument,
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
    # across every R2.3-R2.5 topic that has a distinct SPA payload.
    bus.register_topic("app-plugins", lambda: plugins_payload(plugin_registry))

    async def execute_plugin(plugin_name: str, parent_node_id: str | None = None):
        name = str(plugin_name).strip()
        # ADR-014 stage 14.3: every name - built-in or third-party alike -
        # flows through the same generic dispatch helper now. See
        # _execute_discovered_plugin's own docstring for the two
        # registration mechanisms it checks, in order.
        return await _execute_discovered_plugin(
            bus, notifications, canvas_document, plugin_registry, name, parent_node_id,
        )

    bus.register_intent("app-plugins", "executePlugin", execute_plugin, args_schema=ExecutePluginArgs)

    # ADR-014 stage 14.1 DEVIATION from the design sketch, recorded here
    # rather than silently dropped: the design's own text says a plugin's
    # HostContext.register_intent()-declared custom intents are "Wired at
    # SESSION-ACTIVATION time" onto the bus. That collides with a real,
    # pre-existing, deliberately hard-locked invariant this repo already
    # enforces - tests/test_undo_classification_gate.py (ADR-010 close-out)
    # raises AssertionError on ANY backend/ register_intent() call whose
    # (topic, intent) isn't a source-literal string pair, specifically so
    # every mutating action can be enumerated in a static, hand-reviewed
    # A/B undo-classification table ("no more wandering or patchwork...
    # all doors closed on this matter" - that file's own docstring). A
    # plugin-declared intent name (f"plugin:{plugin_id}:{name}") is
    # necessarily dynamic - it does not exist until a THIRD-PARTY plugin,
    # living entirely outside backend/ (so invisible to that gate's own
    # SCAN_DIR regardless), is discovered at runtime - so it can never be
    # a fixed entry in that closed table by construction, not just today.
    # HostContext.register_intent()/PluginIntentSpec/PluginRegistry.intents
    # are still fully real and populated (a plugin's register() call
    # collecting one is exercised and tested) - only the LIVE bus wiring is
    # deferred, pending a real decision on how undo-classification should
    # treat a dynamically-sized, third-party-declared action surface. That
    # decision belongs with stage 14.4 (scope/consent enforcement for
    # plugin actions), a natural adjacent-governance companion, not a call
    # this stage should make unilaterally by quietly loosening someone
    # else's explicit gate.
