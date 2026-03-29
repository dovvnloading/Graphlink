"""
Compatibility facade for the core graph node types.

The concrete implementations now live in node-specific modules so each
node family owns its own rendering, interaction, and context-menu logic.
"""

from graphite_nodes.graphite_node_chat import ChatNode
from graphite_nodes.graphite_node_chat_menu import ChatNodeContextMenu
from graphite_nodes.graphite_node_code import CodeHighlighter, CodeNode
from graphite_nodes.graphite_node_code_menu import CodeNodeContextMenu
from graphite_nodes.graphite_node_document import DocumentNode
from graphite_nodes.graphite_node_document_menu import DocumentNodeContextMenu
from graphite_nodes.graphite_node_image import ImageNode
from graphite_nodes.graphite_node_image_menu import ImageNodeContextMenu
from graphite_nodes.graphite_node_thinking import ThinkingNode
from graphite_nodes.graphite_node_thinking_menu import ThinkingNodeContextMenu

__all__ = [
    "CodeHighlighter",
    "ChatNode",
    "CodeNode",
    "ThinkingNode",
    "ImageNode",
    "DocumentNode",
    "ThinkingNodeContextMenu",
    "DocumentNodeContextMenu",
    "CodeNodeContextMenu",
    "ImageNodeContextMenu",
    "ChatNodeContextMenu",
]
