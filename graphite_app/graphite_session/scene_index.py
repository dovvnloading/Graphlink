from graphite_canvas_items import NavigationPin, Note
from graphite_conversation_node import ConversationNode
from graphite_html_view import HtmlViewNode
from graphite_node import ChatNode
from graphite_plugin_artifact import ArtifactNode
from graphite_plugin_code_review import CodeReviewNode
from graphite_plugin_code_sandbox import CodeSandboxNode
from graphite_plugin_gitlink import GitlinkNode
from graphite_plugin_graph_diff import GraphDiffNode
from graphite_plugin_quality_gate import QualityGateNode
from graphite_plugin_workflow import WorkflowNode
from graphite_pycoder import PyCoderNode
from graphite_reasoning import ReasoningNode
from graphite_web import WebNode

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
    "reasoning_nodes",
    "html_view_nodes",
    "artifact_nodes",
    "workflow_nodes",
    "graph_diff_nodes",
    "quality_gate_nodes",
    "code_review_nodes",
    "gitlink_nodes",
)

SAVE_GUARD_NODE_LIST_NAMES = (
    "nodes",
    "conversation_nodes",
    "reasoning_nodes",
    "artifact_nodes",
    "workflow_nodes",
    "pycoder_nodes",
    "code_sandbox_nodes",
    "web_nodes",
    "html_view_nodes",
    "quality_gate_nodes",
    "code_review_nodes",
    "graph_diff_nodes",
    "gitlink_nodes",
)

CHILD_LINK_NODE_TYPES = (
    ChatNode,
    PyCoderNode,
    CodeSandboxNode,
    WebNode,
    ConversationNode,
    ReasoningNode,
    HtmlViewNode,
    ArtifactNode,
    WorkflowNode,
    GraphDiffNode,
    QualityGateNode,
    CodeReviewNode,
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
    return [item for item in scene.items() if isinstance(item, NavigationPin)]


def get_all_serializable_items(scene, all_nodes, notes, charts):
    return all_nodes + notes + charts + list(scene.frames) + list(scene.containers)


def build_item_index(items):
    return {item: index for index, item in enumerate(items)}


def has_saveable_nodes(scene):
    return any(getattr(scene, list_name, []) for list_name in SAVE_GUARD_NODE_LIST_NAMES)
