from graphlink_canvas_items import NavigationPin, Note
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_node import ChatNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkNode
from graphlink_pycoder import PyCoderNode
from graphlink_web import WebNode

# Bumped whenever the saved chat payload's shape changes in a way future load code needs
# to branch on. No migration logic reads this yet (see
# doc/ARCHITECTURE_REVIEW_FINDINGS.md #49) - it's the version marker itself, not a
# migration framework. Payloads saved before this field existed simply have no
# "schema_version" key; deserializers.py already tolerates missing/legacy shapes
# (nodes/items/nested data.nodes) so this is purely additive.
CURRENT_CHAT_SCHEMA_VERSION = 1

NODE_LIST_NAMES = (
    "nodes",
    "code_nodes",
    "document_nodes",
    "image_nodes",
    "thinking_nodes",
    "pycoder_nodes",
    "code_sandbox_nodes",
    "web_nodes",
    "conversation_nodes",
    "html_view_nodes",
    "artifact_nodes",
    "gitlink_nodes",
)

SAVE_GUARD_NODE_LIST_NAMES = (
    "nodes",
    "conversation_nodes",
    "artifact_nodes",
    "pycoder_nodes",
    "code_sandbox_nodes",
    "web_nodes",
    "html_view_nodes",
    "gitlink_nodes",
)

CHILD_LINK_NODE_TYPES = (
    ChatNode,
    PyCoderNode,
    CodeSandboxNode,
    WebNode,
    ConversationNode,
    HtmlViewNode,
    ArtifactNode,
    GitlinkNode,
)


def get_all_nodes(scene):
    all_nodes = []
    for list_name in NODE_LIST_NAMES:
        all_nodes.extend(getattr(scene, list_name, []))
    return all_nodes


def get_scene_notes(scene):
    return [item for item in scene.items() if isinstance(item, Note)]


def get_scene_pins(scene):
    if hasattr(scene, "ordered_navigation_pins"):
        return scene.ordered_navigation_pins()
    return [item for item in scene.items() if isinstance(item, NavigationPin)]


def get_all_serializable_items(scene, all_nodes, notes, charts):
    return all_nodes + notes + charts + list(scene.frames) + list(scene.containers)


def build_item_index(items):
    return {item: index for index, item in enumerate(items)}


def has_saveable_nodes(scene):
    return any(getattr(scene, list_name, []) for list_name in SAVE_GUARD_NODE_LIST_NAMES)
