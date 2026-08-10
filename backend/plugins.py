"""Plugin picker listing for the new architecture (Qt-removal plan R2.5).

An INDEPENDENT Qt-free reimplementation of PluginPortal.get_plugin_categories()
- not an import - because graphlink_plugin_portal.py imports PySide6.QtCore
at module scope AND every node_cls it registers (ChatNode, PyCoderNode,
WebNode, GitlinkNode, ArtifactNode, CodeSandboxNode, HtmlViewNode,
ConversationNode) is itself transitively Qt-coupled through 8+ modules,
invisible to test_no_qt_anywhere.py's single-file scan. Same reimplement-
not-import precedent as backend/composer.py.

The category/plugin metadata below (names, descriptions, grouping) is
hand-ported VERBATIM from PLUGIN_CATEGORY_META and the 8 _register_plugin()
call sites in graphlink_plugin_portal.py, reproducing get_plugin_categories()'s
exact algorithm: iterate categories in order, skip empty ones, append a
synthetic "More Plugins" catch-all only if any plugin is uncategorized
(today: none are). Icons are dropped everywhere in this migration, per the
established About/Help precedent - no icon-library dependency exists in the
web layer.

executePlugin's real effect - instantiating a typed QGraphicsItem node - is
NOT reimplemented here: it is out of scope until R3 (real node types in the
scene model) and R5 (a redesigned, replayable plugin-portal-v2 intent
contract), per recon. Selecting a plugin in R2.5 surfaces a real, honest
notification via the already-shipped notifications topic rather than
silently doing nothing or fabricating node creation.
"""

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

# (name, description, category) - registration order matches
# PluginPortal._discover_plugins() exactly.
_PLUGINS = [
    ("System Prompt", "Adds a special node to override the default system prompt for a conversation branch.", "Branch Foundations"),
    ("Conversation Node", "Adds a node for a self-contained, linear chat conversation.", "Branch Foundations"),
    ("Web Research", "Searches, retrieves, and summarizes cited web sources under a bounded network policy.", "Reasoning & Research"),
    ("Gitlink", "Loads a GitHub repository into structured XML context, prepares file-level changes, and only writes after explicit approval.", "Build & Execution"),
    ("Py-Coder", "Opens a Python execution environment to run code and get AI analysis.", "Build & Execution"),
    ("Virtual Environment Runner", "Runs Python inside an isolated virtualenv with your full user-account privileges (isolates installed packages, not the operating system) and lets you declare per-node requirements.txt dependencies.", "Build & Execution"),
    ("HTML Renderer", "Adds a node to render HTML code from a parent node.", "Build & Execution"),
    ("Artifact / Drafter", "A split-pane node for iteratively drafting and refining living documents (Markdown).", "Workflow & Drafting"),
]


def get_plugin_categories(plugin_registry: "PluginRegistry | None" = None) -> list[dict[str, Any]]:
    """Reproduces PluginPortal.get_plugin_categories()'s exact algorithm.

    ADR-014 stage 14.1: `plugin_registry`, when given, contributes every
    discovered plugin's picker entries alongside the 8 built-ins BEFORE the
    existing category-grouping loop runs, unchanged - a discovered entry's
    `category` joining one of _CATEGORY_META's 5 fixed names lands in that
    flyout exactly like a built-in would; any other category (including the
    HostContext default "More Plugins") falls into the same synthetic
    catch-all uncategorized entries already fall into."""
    entries = list(_PLUGINS)
    if plugin_registry is not None:
        entries += [
            (entry.name, entry.description, entry.category)
            for entry in plugin_registry.picker_entries.values()
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
    """ADR-014 stage 14.1: the generic executePlugin path for anything
    discover_plugins() found that is NOT a built-in _PLUGINS name. Kept as
    a top-level function (not nested inside register_plugins) so
    register_plugins itself stays under tests/test_register_function_length.py's
    300-line register* cap."""
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
    if plugin_registry is None:
        plugin_registry = discover_plugins(builtin_names=frozenset(p[0] for p in _PLUGINS))

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
        valid_names = {p[0] for p in _PLUGINS}
        if name not in valid_names:
            # ADR-014 stage 14.1: not a built-in name - the generic plugin-SDK
            # path (a discovered plugin's picker entry), extracted to a
            # top-level helper so register_plugins itself stays under the
            # 300-line register* cap (tests/test_register_function_length.py).
            return await _execute_discovered_plugin(
                bus, notifications, canvas_document, plugin_registry, name, parent_node_id,
            )

        if name == "Web Research":
            # R5.1: the first real node-creation plugin - every other plugin
            # name below is still an honest deferred notice. A Web Research
            # node is a branch-point child (same posture as thinking/html/
            # image/conversation nodes), so it always requires a real, valid
            # parent to branch from - there is no unparented/root form.
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding a Web Node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginWebResearch", "user",
                lambda: canvas_document.add_web_research_node(
                    parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
                ),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        if name == "Gitlink":
            # R5.3: the third real node-creation plugin, same posture as Web
            # Research/Artifact above - Gitlink is a branch-point child (same
            # as thinking/html/image/conversation/web_research/artifact
            # nodes), so it always requires a real, valid parent to branch
            # from - there is no unparented/root form (confirmed against
            # graphlink_plugin_portal.py's own no_selection_message/
            # invalid_parent_message for Gitlink).
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding a Gitlink node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginGitlink", "user",
                lambda: canvas_document.add_gitlink_node(
                    parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
                ),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        if name == "Py-Coder":
            # R5.4: the fourth real node-creation plugin, same posture as
            # Web Research/Artifact/Gitlink above - Py-Coder is a
            # branch-point child (same as thinking/html/image/conversation/
            # web_research/artifact/gitlink nodes), so it always requires a
            # real, valid parent to branch from - there is no unparented/
            # root form.
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding a Py-Coder node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginPyCoder", "user",
                lambda: canvas_document.add_pycoder_node(
                    parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
                ),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        if name == "Virtual Environment Runner":
            # R5.4: the fifth real node-creation plugin, same posture as
            # every prior real node-creation plugin above.
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding a Virtual Environment Runner node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginCodeSandbox", "user",
                lambda: canvas_document.add_code_sandbox_node(
                    parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
                ),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        if name == "Artifact / Drafter":
            # R5.2: the second real node-creation plugin, same posture as
            # Web Research above - an Artifact node is a branch-point child
            # (same as thinking/html/image/conversation/web_research nodes),
            # so it always requires a real, valid parent to branch from -
            # there is no unparented/root form.
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding an Artifact node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginArtifact", "user",
                lambda: canvas_document.add_artifact_node(
                    parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
                ),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        if name == "System Prompt":
            # R6.1: the sixth real node-creation plugin, same "requires a
            # real, valid parent_node_id" posture as every real
            # node-creation plugin above - legacy places a System Prompt
            # note near/above a branch root, which only makes sense relative
            # to a selected node. UNLIKE the branch-point-child plugins
            # above (which attach as a CHILD of parent_node_id, one
            # MESSAGE_VERTICAL_SPACING below it), this note attaches to
            # parent_node_id's BRANCH ROOT (SceneDocument.get_branch_root -
            # the same parent-edge walk backend/agents.py's
            # _resolve_branch_system_prompt uses at send time), positioned
            # roughly 150px ABOVE that root, and connects note -> root (the
            # edge DIRECTION _resolve_branch_system_prompt looks for -
            # reversed from the child-plugins' root -> child edges above).
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding a System Prompt node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            root = canvas_document.get_branch_root(parent_node_id)
            # A root can only ever have ONE effective system-prompt note -
            # backend/agents.py._resolve_branch_system_prompt has no
            # deterministic "which one wins" rule for two at once. Reuse an
            # existing one instead of creating a silently-inert duplicate.
            existing = next(
                (
                    canvas_document.nodes[edge.source]
                    for edge in canvas_document.edges.values()
                    if edge.target == root.id
                    and edge.source in canvas_document.nodes
                    and canvas_document.nodes[edge.source].kind == "note"
                    and canvas_document.nodes[edge.source].state.is_system_prompt
                ),
                None,
            )
            if existing is not None:
                await bus.publish("scene")
                return existing.id
            # The only plugin branch that creates AND connects - both are
            # captured by one command, so undoing it removes the note and
            # its edge together rather than leaving a dangling edge.
            def _create_system_prompt_note():
                created = canvas_document.add_note(root.x, root.y - 150, is_system_prompt=True)
                canvas_document.connect(created.id, root.id)
                return created

            note, _command = canvas_document.record_command(
                "pluginSystemPrompt", "user", _create_system_prompt_note,
                node_ids=[root.id],
            )
            await bus.publish("scene")
            return note.id

        if name == "Conversation Node":
            # R7.5a: ConversationNode has existed since R3.25 with zero
            # creation path - add_conversation_node was only ever reachable
            # from backend tests, never from a real UI action. Same
            # "branch-point child, real valid parent required" posture as
            # every real node-creation plugin above.
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding a Conversation Node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginConversationNode", "user",
                lambda: canvas_document.add_conversation_node(parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        if name == "HTML Renderer":
            # R7.5a: HtmlViewNode has existed since R3.17 with zero creation
            # path, same gap class as Conversation Node above. Starts with
            # empty html_content - the same "create blank, then edit in
            # place" posture add_note's System Prompt branch above and the
            # plain "Add Note" command already use, since the plugin picker
            # has no field to source initial HTML from.
            if not parent_node_id or parent_node_id not in canvas_document.nodes:
                notifications.show(
                    "Please select a valid node to branch from before adding an HTML Renderer node.",
                    "warning",
                )
                await bus.publish("notification")
                return None
            parent = canvas_document.nodes[parent_node_id]
            node, _command = canvas_document.record_command(
                "pluginHtmlRenderer", "user",
                lambda: canvas_document.add_html_node(parent.x, parent.y + MESSAGE_VERTICAL_SPACING, "", parent_node_id),
                node_ids=[parent_node_id],
            )
            await bus.publish("scene")
            return node.id

        notifications.show(f'"{name}" node creation isn\'t available yet.', "info")
        await bus.publish("notification")
        return None

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
