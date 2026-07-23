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

from typing import Any

from backend.canvas import MESSAGE_VERTICAL_SPACING, SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState

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
    ("Execution Sandbox", "Runs Python inside an isolated virtualenv with your full user-account privileges (isolates installed packages, not the operating system) and lets you declare per-node requirements.txt dependencies.", "Build & Execution"),
    ("HTML Renderer", "Adds a node to render HTML code from a parent node.", "Build & Execution"),
    ("Artifact / Drafter", "A split-pane node for iteratively drafting and refining living documents (Markdown).", "Workflow & Drafting"),
]


def get_plugin_categories() -> list[dict[str, Any]]:
    """Reproduces PluginPortal.get_plugin_categories()'s exact algorithm."""
    categorized_names: set[str] = set()
    grouped: list[dict[str, Any]] = []

    for category in _CATEGORY_META:
        plugins = [
            {"name": name, "description": description}
            for name, description, category_name in _PLUGINS
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
        for name, description, category_name in _PLUGINS
        if name not in categorized_names
    ]
    if uncategorized:
        grouped.append({
            "name": "More Plugins",
            "description": "Additional plugins that do not yet belong to a dedicated flyout category.",
            "plugins": uncategorized,
        })

    return grouped


def plugins_payload() -> dict[str, Any]:
    return {"categories": get_plugin_categories()}


def register_plugins(
    bus: SessionBus, notifications: NotificationState, canvas_document: SceneDocument
) -> None:
    # Topic name "app-plugins" (matching the codegen artifact's derived
    # name - same reasoning as "app-composer"/"app-about"): no existing
    # "plugins" schema collision today, but the pattern is now consistent
    # across every R2.3-R2.5 topic that has a distinct SPA payload.
    bus.register_topic("app-plugins", plugins_payload)

    async def execute_plugin(plugin_name: str, parent_node_id: str | None = None):
        name = str(plugin_name).strip()
        valid_names = {p[0] for p in _PLUGINS}
        if name not in valid_names:
            notifications.show(f'Unknown plugin: "{name}"', "warning")
            await bus.publish("notification")
            return None

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
            node = canvas_document.add_web_research_node(
                parent.x, parent.y + MESSAGE_VERTICAL_SPACING, parent_node_id
            )
            await bus.publish("scene")
            return node.id

        notifications.show(f'"{name}" node creation lands in R3/R5.', "info")
        await bus.publish("notification")
        return None

    bus.register_intent("app-plugins", "executePlugin", execute_plugin)
