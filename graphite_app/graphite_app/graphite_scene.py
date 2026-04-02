from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsScene, QMessageBox, QGraphicsLineItem
)
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPen, QTransform

from graphite_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphite_connections import (
    ConnectionItem, ContentConnectionItem, SystemPromptConnectionItem,
    DocumentConnectionItem, ImageConnectionItem, PyCoderConnectionItem,
    ConversationConnectionItem, ReasoningConnectionItem, GroupSummaryConnectionItem,
    HtmlConnectionItem, ThinkingConnectionItem
)
from graphite_canvas_items import Frame, Note, NavigationPin, ChartItem, Container
from graphite_pycoder import PyCoderNode
from graphite_plugin_code_sandbox import CodeSandboxNode
from graphite_web import WebNode, WebConnectionItem
from graphite_conversation_node import ConversationNode
from graphite_reasoning import ReasoningNode
from graphite_html_view import HtmlViewNode
from graphite_plugin_artifact import ArtifactNode, ArtifactConnectionItem
from graphite_plugin_workflow import WorkflowNode, WorkflowConnectionItem
from graphite_plugin_graph_diff import GraphDiffNode, GraphDiffConnectionItem
from graphite_plugin_quality_gate import QualityGateNode, QualityGateConnectionItem
from graphite_plugin_code_review import CodeReviewNode, CodeReviewConnectionItem
from graphite_plugin_gitlink import GitlinkNode, GitlinkConnectionItem
from graphite_memory import clone_history, resolve_branch_parent

class ChatScene(QGraphicsScene):
    BRANCH_DIM_OPACITY = 0.18

    """
    The core data model and controller for the Graphite canvas.

    This class manages all graphical items, including nodes, connections, frames,
    and notes. It handles the logic for adding, removing, and arranging these items,
    as well as implementing features like snapping, smart guides, and organizing the
    layout. It acts as the central hub for all canvas-related operations.
    """
    scene_changed = Signal()

    def __init__(self, window):
        """
        Initializes the ChatScene.

        Args:
            window (QMainWindow): A reference to the main application window.
        """
        super().__init__()
        self.window = window
        # Lists to track all items of a specific type in the scene.
        self.nodes = []
        self.connections = []
        self.frames = []
        self.containers = []
        self.pins = []
        self.notes = []
        self.code_nodes = []
        self.document_nodes = []
        self.image_nodes = []
        self.thinking_nodes = []
        self.pycoder_nodes = []
        self.code_sandbox_nodes = []
        self.web_nodes = []
        self.conversation_nodes = []
        self.reasoning_nodes = []
        self.html_view_nodes = []
        self.artifact_nodes = []
        self.workflow_nodes = []
        self.graph_diff_nodes = []
        self.quality_gate_nodes = []
        self.code_review_nodes = []
        self.gitlink_nodes = []
        self.chart_nodes = []
        
        self.content_connections = []
        self.document_connections = []
        self.image_connections = []
        self.thinking_connections = []
        self.system_prompt_connections = []
        self.pycoder_connections = []
        self.code_sandbox_connections = []
        self.web_connections = []
        self.conversation_connections = []
        self.reasoning_connections = []
        self.group_summary_connections = []
        self.html_connections = []
        self.artifact_connections = []
        self.workflow_connections = []
        self.graph_diff_connections = []
        self.quality_gate_connections = []
        self.code_review_connections = []
        self.gitlink_connections = []

        self.setBackgroundBrush(QColor("#252526"))
        
        # Parameters for the auto-layout algorithm.
        self.horizontal_spacing = 150
        self.vertical_spacing = 60
        self.is_branch_hidden = False
        
        # Properties for alignment, snapping, and routing.
        self.snap_to_grid = False
        self.orthogonal_routing = False
        self.smart_guides = False
        self.fade_connections_enabled = False
        self.is_dragging_item = False
        self.smart_guide_lines = []
        self.is_rubber_band_dragging = False

        # Global font properties for nodes that support them.
        self.font_family = "Segoe UI"
        self.font_size = 10
        self.font_color = QColor("#dddddd")
        
    def setFontFamily(self, family):
        """
        Sets the font family for all applicable nodes in the scene.

        Args:
            family (str): The name of the font family (e.g., "Arial").
        """
        if self.font_family != family:
            self.font_family = family
            self._update_all_node_fonts()

    def setFontSize(self, size):
        """
        Sets the font size for all applicable nodes in the scene.

        Args:
            size (int): The new font size in points.
        """
        if self.font_size != size:
            self.font_size = size
            self._update_all_node_fonts()
    
    def setFontColor(self, color):
        """
        Sets the font color for all applicable nodes in the scene.

        Args:
            color (QColor): The new font color.
        """
        if self.font_color != color:
            self.font_color = color
            self._update_all_node_fonts()

    def setFadeConnectionsEnabled(self, enabled):
        enabled = bool(enabled)
        if self.fade_connections_enabled != enabled:
            self.fade_connections_enabled = enabled
            self.update_connection_visibility()
            self.update()

    def update_connection_visibility(self):
        connection_lists = self._all_connection_lists()

        for conn_list in connection_lists:
            for conn in conn_list:
                if hasattr(conn, "sync_visibility_mode"):
                    conn.sync_visibility_mode()
                else:
                    conn.update()

    def _all_connection_lists(self):
        return [
            self.connections,
            self.content_connections,
            self.document_connections,
            self.image_connections,
            self.thinking_connections,
            self.system_prompt_connections,
            self.pycoder_connections,
            self.code_sandbox_connections,
            self.web_connections,
            self.conversation_connections,
            self.reasoning_connections,
            self.group_summary_connections,
            self.html_connections,
            self.artifact_connections,
            self.workflow_connections,
            self.graph_diff_connections,
            self.quality_gate_connections,
            self.code_review_connections,
            self.gitlink_connections,
        ]

    def _remove_connections_for_node(self, node, connection_lists=None):
        lists_to_scan = connection_lists if connection_lists is not None else self._all_connection_lists()
        for conn_list in lists_to_scan:
            for conn in conn_list[:]:
                try:
                    if node not in (conn.start_node, conn.end_node):
                        continue
                except RuntimeError:
                    # If the underlying C++ object is already gone, remove the wrapper.
                    pass

                if conn.scene() == self:
                    self.removeItem(conn)
                if conn in conn_list:
                    conn_list.remove(conn)

    def _update_all_node_fonts(self):
        """Iterates through all nodes that support font changes and applies the current settings."""
        nodes_to_update = self.nodes + self.document_nodes + self.thinking_nodes
        for node in nodes_to_update:
            if hasattr(node, 'update_font_settings'):
                node.update_font_settings(self.font_family, self.font_size, self.font_color)
            if hasattr(node, '_recalculate_geometry'):
                node._recalculate_geometry()

        self.update_connections()
        self.scene_changed.emit()

    def find_items(self, text):
        """
        Searches all nodes for a given text string.

        Args:
            text (str): The text to search for (case-insensitive).

        Returns:
            list: A list of nodes that contain the search text, sorted by position.
        """
        if not text:
            return []

        text = text.lower()
        matches = []
        
        all_nodes = (self.nodes + self.code_nodes + self.document_nodes + self.image_nodes +
                     self.thinking_nodes + self.pycoder_nodes + self.code_sandbox_nodes + self.web_nodes +
                     self.conversation_nodes + self.reasoning_nodes + self.html_view_nodes +
                     self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes + self.chart_nodes)
        for node in all_nodes:
            content = ""
            if isinstance(node, ChatNode):
                content = node.text
            elif isinstance(node, CodeNode):
                content = node.code
            elif isinstance(node, DocumentNode):
                content = node.content
            elif isinstance(node, ImageNode):
                content = node.prompt
            elif isinstance(node, ThinkingNode):
                content = node.thinking_text
            elif isinstance(node, ConversationNode):
                content = "\n".join([msg.get('content', '') for msg in node.conversation_history])
            elif isinstance(node, ReasoningNode):
                content = node.prompt + "\n" + node.thought_process
            elif isinstance(node, ArtifactNode):
                content = node.get_artifact_content() + "\n" + node.chat_html_cache
            elif isinstance(node, WorkflowNode):
                content = node.blueprint_markdown + "\n" + node.get_goal() + "\n" + node.get_constraints()
            elif isinstance(node, GraphDiffNode):
                content = node.comparison_markdown + "\n" + node.note_summary
            elif isinstance(node, QualityGateNode):
                content = node.review_markdown + "\n" + node.note_summary + "\n" + node.get_goal() + "\n" + node.get_criteria()
            elif isinstance(node, CodeReviewNode):
                content = node.review_markdown + "\n" + node.get_review_context() + "\n" + node.source_editor.toPlainText()
            elif isinstance(node, GitlinkNode):
                content = node.get_task_prompt() + "\n" + node.context_xml + "\n" + node.proposal_markdown + "\n" + node.preview_text
            elif isinstance(node, PyCoderNode):
                content = node.get_prompt() + "\n" + node.get_code() + "\n" + node.output_display.toPlainText()
            elif isinstance(node, CodeSandboxNode):
                content = node.get_prompt() + "\n" + node.get_requirements() + "\n" + node.get_code() + "\n" + node.output_display.toPlainText()
            elif isinstance(node, ChartItem):
                labels = node.data.get("labels", []) if isinstance(node.data, dict) else []
                flows = node.data.get("flows", []) if isinstance(node.data, dict) else []
                flow_text = "\n".join(
                    f"{flow.get('source', '')} -> {flow.get('target', '')}: {flow.get('value', '')}"
                    for flow in flows if isinstance(flow, dict)
                )
                content = "\n".join(
                    part for part in (
                        str(node.data.get("title", "")) if isinstance(node.data, dict) else "",
                        str(node.data.get("type", "")) if isinstance(node.data, dict) else "",
                        "\n".join(str(label) for label in labels),
                        flow_text,
                    ) if part
                )

            if text in content.lower():
                matches.append(node)

        # Sort matches by their Y, then X position for consistent navigation.
        matches.sort(key=lambda n: (n.pos().y(), n.pos().x()))
        return matches

    def update_search_highlight(self, matched_nodes):
        """
        Updates the visual search highlight state for all nodes.

        Args:
            matched_nodes (list): A list of nodes that should be highlighted.
        """
        all_nodes = (self.nodes + self.code_nodes + self.document_nodes + self.image_nodes +
                     self.thinking_nodes + self.pycoder_nodes + self.code_sandbox_nodes + self.web_nodes +
                     self.conversation_nodes + self.reasoning_nodes + self.html_view_nodes +
                     self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes)
        for node in all_nodes:
            is_match = node in matched_nodes
            if getattr(node, 'is_search_match', False) != is_match:
                node.is_search_match = is_match
                node.update()

    def add_chat_node(self, text, is_user=True, parent_node=None, conversation_history=None):
        """
        Creates and adds a new ChatNode to the scene.

        Args:
            text (str): The text content for the node.
            is_user (bool, optional): True if it's a user node, False for an AI node.
            parent_node (QGraphicsItem, optional): The parent node to connect to.
            conversation_history (list, optional): The conversation history for this node.

        Returns:
            ChatNode: The newly created node.
        """
        try:
            # Validate the parent node if provided.
            if parent_node is not None:
                valid_parent_types = (
                    self.nodes + self.pycoder_nodes + self.code_sandbox_nodes + self.web_nodes +
                    self.conversation_nodes + self.reasoning_nodes + self.html_view_nodes +
                    self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes
                )
                if parent_node not in valid_parent_types or not parent_node.scene():
                    print("Warning: Parent node is invalid or no longer in the scene.")
                    parent_node = None
            
            node = ChatNode(text, is_user)
            if conversation_history:
                node.conversation_history = clone_history(conversation_history)
            
            # If there's a parent, position the new node relative to it and create a connection.
            if parent_node:
                parent_pos = parent_node.pos()
                parent_node.children.append(node)
                node.parent_node = parent_node
                
                # Find an open position to the right of the parent.
                base_pos = QPointF(parent_pos.x() + self.horizontal_spacing, parent_pos.y())
                node.setPos(self.find_free_position(base_pos, node))
                
                connection = ConnectionItem(parent_node, node)
                node.incoming_connection = connection
                self.addItem(connection)
                self.connections.append(connection)
            else:
                # Default position for root nodes.
                node.setPos(50, 150)
            
            self.addItem(node)
            self.nodes.append(node)
                
            self.scene_changed.emit()
            return node
            
        except Exception as e:
            print(f"Error adding chat node: {str(e)}")
            if 'node' in locals() and node.scene() == self:
                self.removeItem(node)
            return None

    def _get_next_content_node_y(self, parent_node):
        """Calculates the Y position for a new content node below its parent."""
        last_y = parent_node.pos().y() + parent_node.height
        
        # Check for existing content nodes to stack below them
        all_content_nodes = self.code_nodes + self.document_nodes + self.image_nodes + self.thinking_nodes
        for node in all_content_nodes:
            if hasattr(node, 'parent_content_node') and node.parent_content_node == parent_node:
                last_y = max(last_y, node.pos().y() + node.height)
                
        return last_y + 50

    def add_code_node(self, code, language, parent_content_node):
        """
        Creates and adds a new CodeNode, positioning it below its parent ChatNode.

        Args:
            code (str): The code content for the node.
            language (str): The programming language for syntax highlighting.
            parent_content_node (ChatNode): The ChatNode this code block belongs to.

        Returns:
            CodeNode: The newly created node.
        """
        node = CodeNode(code, language, parent_content_node)
        y_pos = self._get_next_content_node_y(parent_content_node)
        node.setPos(QPointF(parent_content_node.pos().x(), y_pos))
        
        self.addItem(node)
        self.code_nodes.append(node)
        
        connection = ContentConnectionItem(parent_content_node, node)
        self.addItem(connection)
        self.content_connections.append(connection)
        
        self.scene_changed.emit()
        return node

    def add_image_node(self, image_bytes, parent_chat_node, prompt=""):
        """
        Creates and adds a new ImageNode.

        Args:
            image_bytes (bytes): The raw image data.
            parent_chat_node (ChatNode): The ChatNode this image belongs to.
            prompt (str, optional): The prompt used to generate the image.

        Returns:
            ImageNode: The newly created node.
        """
        node = ImageNode(image_bytes, parent_chat_node, prompt)
        y_pos = self._get_next_content_node_y(parent_chat_node)
        node.setPos(QPointF(parent_chat_node.pos().x(), y_pos))
        
        self.addItem(node)
        self.image_nodes.append(node)
        
        connection = ImageConnectionItem(parent_chat_node, node)
        self.addItem(connection)
        self.image_connections.append(connection)
        
        self.scene_changed.emit()
        return node

    def add_document_node(self, title, content, parent_user_node):
        """
        Creates and adds a new DocumentNode.

        Args:
            title (str): The title of the document (usually the filename).
            content (str): The text content of the document.
            parent_user_node (ChatNode): The user's ChatNode that included the document.

        Returns:
            DocumentNode: The newly created node.
        """
        node = DocumentNode(title, content, parent_user_node)
        y_pos = self._get_next_content_node_y(parent_user_node)
        node.setPos(QPointF(parent_user_node.pos().x(), y_pos))
        
        self.addItem(node)
        self.document_nodes.append(node)
        
        connection = DocumentConnectionItem(parent_user_node, node)
        self.addItem(connection)
        self.document_connections.append(connection)
        
        self.scene_changed.emit()
        return node

    def add_thinking_node(self, thinking_text, parent_chat_node):
        """
        Creates and adds a new ThinkingNode for displaying AI reasoning.

        Args:
            thinking_text (str): The reasoning text from the AI.
            parent_chat_node (ChatNode): The AI's ChatNode this reasoning belongs to.

        Returns:
            ThinkingNode: The newly created node.
        """
        node = ThinkingNode(thinking_text, parent_chat_node)
        y_pos = self._get_next_content_node_y(parent_chat_node)
        node.setPos(QPointF(parent_chat_node.pos().x(), y_pos))
        
        self.addItem(node)
        self.thinking_nodes.append(node)
        
        connection = ThinkingConnectionItem(parent_chat_node, node)
        self.addItem(connection)
        self.thinking_connections.append(connection)
        
        self.scene_changed.emit()
        return node

    def nodeMoved(self, node):
        """
        Callback triggered when a node is moved. Updates all attached connections.

        Args:
            node (QGraphicsItem): The node that was moved.
        """
        # Ensure the node is a valid, tracked item before proceeding.
        valid_types = (
            self.nodes + self.code_nodes + self.document_nodes + self.image_nodes + self.thinking_nodes +
            self.pycoder_nodes + self.code_sandbox_nodes + self.web_nodes + self.conversation_nodes + self.reasoning_nodes +
            self.html_view_nodes + self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes +
            self.chart_nodes
        )
        if not isinstance(node, (Note, Container)) and node not in valid_types or not node.scene():
            return

        for frame in self.frames:
            if node in frame.nodes and not frame.resizing:
                frame.updateGeometry()
            
        # Iterate through all connection types and update any connected to the moved node.
        # Note: Slicing `[:]` creates a copy, allowing safe removal from the list during iteration if a connection is invalid.
        all_connection_lists = [
            self.connections, self.content_connections, self.document_connections, self.image_connections,
            self.thinking_connections, self.system_prompt_connections, self.pycoder_connections, self.code_sandbox_connections, self.web_connections,
            self.conversation_connections, self.reasoning_connections, self.group_summary_connections,
            self.html_connections, self.artifact_connections, self.workflow_connections, self.graph_diff_connections, self.quality_gate_connections, self.code_review_connections, self.gitlink_connections
        ]

        for conn_list in all_connection_lists:
            for conn in conn_list[:]:
                if node in (conn.start_node, conn.end_node):
                    conn.update_path()
        
        self.scene_changed.emit()
                        
    def add_navigation_pin(self, pos):
        """
        Adds a new NavigationPin to the scene at the specified position.

        Args:
            pos (QPointF): The scene position for the new pin.

        Returns:
            NavigationPin: The created pin item.
        """
        pin = NavigationPin()
        pin.setPos(pos)
        self.addItem(pin)
        self.pins.append(pin)
        return pin
    
    def add_chart(self, data, pos, parent_content_node=None):
        """Adds a new ChartItem to the scene."""
        chart = ChartItem(data, pos, parent_content_node=parent_content_node)
        chart.setPos(self.find_free_position(pos, chart))
        self.chart_nodes.append(chart)
        self.addItem(chart)
        self.scene_changed.emit()
        return chart

    def createFrame(self):
        """Creates a Frame around the currently selected nodes."""
        selected_nodes = [item for item in self.selectedItems() 
                         if isinstance(item, (ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode, ChartItem, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, ArtifactNode, WorkflowNode, GraphDiffNode, QualityGateNode, CodeReviewNode, GitlinkNode))]
        
        if not selected_nodes:
            return
            
        # If a selected node is already in a frame, un-parent it first.
        for node in selected_nodes:
            if node.parentItem() and isinstance(node.parentItem(), Frame):
                old_frame = node.parentItem()
                scene_pos = node.scenePos()
                node.setParentItem(None)
                node.setPos(scene_pos)
                old_frame.nodes.remove(node)
                # If the old frame is now empty, remove it.
                if not old_frame.nodes:
                    self.removeItem(old_frame)
                    if old_frame in self.frames:
                        self.frames.remove(old_frame)
                else:
                    old_frame.updateGeometry()
        
        frame = Frame(selected_nodes)
        self.addItem(frame)
        self.frames.append(frame)
        frame.setZValue(-2) # Ensure frames are drawn behind nodes.
        
        # Trigger connection updates for all affected nodes.
        for node in selected_nodes:
            self.nodeMoved(node)
        
        self.scene_changed.emit()

    def createContainer(self):
        """Creates a Container around the currently selected items."""
        selected_items = [item for item in self.selectedItems() 
                         if isinstance(item, (ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode, Note, ChartItem, Frame, Container, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, ArtifactNode, WorkflowNode, GraphDiffNode, QualityGateNode, CodeReviewNode, GitlinkNode))]
        
        if not selected_items:
            return

        # Un-parent selected items from any existing containers or frames.
        for item in selected_items:
            if item.parentItem() and isinstance(item.parentItem(), (Frame, Container)):
                old_parent = item.parentItem()
                scene_pos = item.scenePos()
                item.setParentItem(None)
                item.setPos(scene_pos)
                
                # Clean up the old parent if it becomes empty.
                if isinstance(old_parent, Frame):
                    old_parent.nodes.remove(item)
                    if not old_parent.nodes: self.deleteFrame(old_parent)
                elif isinstance(old_parent, Container):
                    old_parent.contained_items.remove(item)
                    if not old_parent.contained_items: self.deleteContainer(old_parent)
        
        container = Container(selected_items)
        self.addItem(container)
        self.containers.append(container)
        container.setZValue(-3) # Ensure containers are drawn behind frames and nodes.
        
        for item in selected_items:
            self.nodeMoved(item)
        
        self.scene_changed.emit()
            
    def add_note(self, pos):
        """Adds a new Note item to the scene."""
        note = Note(pos)
        self.addItem(note)
        self.notes.append(note)
        self.scene_changed.emit()
        return note
    
    def deleteSelectedNotes(self):
        """Deletes all currently selected Note items."""
        for item in list(self.selectedItems()):
            if isinstance(item, Note):
                self.removeItem(item)
    
    def deleteFrame(self, frame):
        """
        Deletes a Frame, releasing its contained nodes.

        Args:
            frame (Frame): The frame to delete.
        """
        if hasattr(frame, 'dispose'):
            frame.dispose()
        release_parent = frame.parentItem()

        # Un-parent all nodes from the frame, restoring their scene positions.
        for node in frame.nodes:
            scene_pos = node.scenePos()
            if node.parentItem() is frame:
                node.setParentItem(release_parent)
                if release_parent:
                    node.setPos(release_parent.mapFromScene(scene_pos))
                else:
                    node.setPos(scene_pos)
            node.setVisible(True)
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            self.nodeMoved(node)
        
        self.removeItem(frame)
        if frame in self.frames:
            self.frames.remove(frame)
        self.scene_changed.emit()

    def deleteContainer(self, container):
        """
        Deletes a Container, releasing its contained items.

        Args:
            container (Container): The container to delete.
        """
        if hasattr(container, 'dispose'):
            container.dispose()
        for item in container.contained_items:
            scene_pos = item.scenePos()
            item.setParentItem(None)
            item.setPos(scene_pos)
            item.setVisible(True)
            self.nodeMoved(item)
        
        self.removeItem(container)
        if container in self.containers:
            self.containers.remove(container)
        self.scene_changed.emit()

    def keyPressEvent(self, event):
        """Handles key press events for scene-wide shortcuts."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_A:
            self.selectAllNodes()
        elif event.key() == Qt.Key.Key_Delete:
            self.deleteSelectedItems()
        super().keyPressEvent(event)

    def _all_conversational_nodes(self):
        return (
            self.nodes + self.pycoder_nodes + self.code_sandbox_nodes + self.web_nodes +
            self.conversation_nodes + self.reasoning_nodes + self.html_view_nodes +
            self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes
        )

    def _all_content_nodes(self):
        return self.code_nodes + self.document_nodes + self.image_nodes + self.thinking_nodes + self.chart_nodes

    def _all_layout_nodes(self):
        return self._all_conversational_nodes() + self._all_content_nodes()

    def _all_branch_visibility_nodes(self):
        """Returns every node-like item that should participate in branch focus mode."""
        return self._all_layout_nodes()

    def _branch_anchor_nodes(self, item):
        """
        Returns the conversational nodes that determine branch membership for an item.

        Conversational/plugin nodes participate directly in the branch tree. Content
        nodes such as code, images, charts, and CoT panels inherit membership from
        their parent conversational node. Branch diff nodes can belong to either of
        the compared source branches, so both sources are considered.
        """
        if item is None:
            return set()

        anchors = set()

        if hasattr(item, "left_source_node") or hasattr(item, "right_source_node"):
            for attr_name in ("left_source_node", "right_source_node"):
                source_node = getattr(item, attr_name, None)
                if source_node is not None:
                    anchors.add(resolve_branch_parent(source_node) or source_node)
            return {anchor for anchor in anchors if anchor is not None}

        parent_content_node = getattr(item, "parent_content_node", None)
        if parent_content_node is not None:
            anchors.add(parent_content_node)

        branch_parent = resolve_branch_parent(item)
        if branch_parent is not None:
            anchors.add(branch_parent)

        if not anchors and hasattr(item, "children"):
            anchors.add(item)

        return {anchor for anchor in anchors if anchor is not None}

    def _set_branch_focus_state(self, item, is_active):
        """Applies branch focus styling to any node-like scene item."""
        if hasattr(item, "is_dimmed"):
            item.is_dimmed = not is_active

        item.setOpacity(1.0 if is_active else self.BRANCH_DIM_OPACITY)
        item.update()
        
    def selectAllNodes(self):
        """Selects all node-like items in the scene, including plugins and charts."""
        for node in self._all_layout_nodes():
            node.setSelected(True)

    def calculate_node_rect(self, node, pos):
        """Calculates a padded bounding rectangle for collision detection."""
        PADDING = 30
        return QRectF(pos.x() - PADDING, pos.y() - PADDING, node.width + (PADDING * 2), node.height + (PADDING * 2))

    def check_collision(self, test_rect, ignore_node=None):
        """Checks if a given rectangle intersects with any existing nodes."""
        for node in self._all_layout_nodes() + self.notes:
            if node == ignore_node: continue
            node_rect = self.calculate_node_rect(node, node.pos())
            if test_rect.intersects(node_rect):
                return True
        return False

    def find_free_position(self, base_pos, node, max_attempts=50):
        """
        Finds an unoccupied position for a new node, searching in a spiral pattern
        outward from a base position.
        """
        # Generator for spiral positions.
        def spiral_positions():
            x, y = base_pos.x(), base_pos.y()
            layer = 1
            while True:
                for i in range(layer): yield QPointF(x, y); x += self.horizontal_spacing // 2
                for i in range(layer): yield QPointF(x, y); y += self.vertical_spacing
                for i in range(layer): yield QPointF(x, y); x -= self.horizontal_spacing // 2
                for i in range(layer): yield QPointF(x, y); y -= self.vertical_spacing
                layer += 1

        for pos in spiral_positions():
            if max_attempts <= 0: break
            test_rect = self.calculate_node_rect(node, pos)
            if not self.check_collision(test_rect, node):
                return pos
            max_attempts -= 1

        # Fallback if no free position is found.
        return QPointF(base_pos.x(), base_pos.y() + len(self._all_layout_nodes()) * self.vertical_spacing)

    def mousePressEvent(self, event):
        """Handles mouse press events for adding/removing connection pins."""
        # If clicking on an empty area, clear connection selections.
        clicked_item = self.itemAt(event.scenePos(), QTransform())
        if not clicked_item:
            for item in self.items():
                if isinstance(item, ConnectionItem):
                    item.is_selected = False
                    item.stopArrowAnimation()
                    item.update()
                
        # Ctrl+Click on a connection to add a pin.
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            item = self.itemAt(event.scenePos(), self.views()[0].transform())
            if isinstance(item, ConnectionItem) and event.button() == Qt.MouseButton.LeftButton:
                item.add_pin(event.scenePos())
                event.accept()
                return
                
        super().mousePressEvent(event)
    
        # If no modifiers and clicking on an empty area, clear the selection.
        if not event.modifiers and not self.itemAt(event.scenePos(), self.views()[0].transform()):
            self.clearSelection()
            
    def update_connections(self):
        """
        Updates the paths of all connections and removes any invalid connections
        (e.g., those connected to deleted nodes).
        """
        all_nodes = (self.nodes + self.code_nodes + self.document_nodes + self.image_nodes + self.thinking_nodes +
                     self.pycoder_nodes + self.code_sandbox_nodes + self.web_nodes +
                     self.conversation_nodes + self.reasoning_nodes + self.html_view_nodes +
                     self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes)
        
        # Validate and update primary connections.
        valid_connections = []
        for conn in self.connections[:]:
            try:
                start_node_valid = conn.start_node in all_nodes and conn.start_node.scene() == self
                end_node_valid = conn.end_node in all_nodes and conn.end_node.scene() == self

                if start_node_valid and end_node_valid and hasattr(conn.start_node, 'children') and conn.end_node in conn.start_node.children:
                    valid_connections.append(conn)
                    conn.setZValue(-1)
                    conn.update_path()
                    if hasattr(conn, 'sync_visibility_mode'):
                        conn.sync_visibility_mode()
                    conn.show()
                else:
                    self.removeItem(conn)
            except RuntimeError:
                if conn.scene() == self: self.removeItem(conn)
        self.connections = valid_connections

        # Update all other types of connections.
        for conn_list in [self.content_connections, self.document_connections, self.image_connections, self.thinking_connections,
                          self.system_prompt_connections, self.pycoder_connections, self.code_sandbox_connections, self.web_connections,
                          self.conversation_connections, self.reasoning_connections, self.group_summary_connections,
                          self.html_connections, self.artifact_connections, self.workflow_connections, self.graph_diff_connections, self.quality_gate_connections, self.code_review_connections, self.gitlink_connections]:
            for conn in conn_list:
                conn.update_path()
                if hasattr(conn, 'sync_visibility_mode'):
                    conn.sync_visibility_mode()

    def toggle_branch_visibility(self, originating_node):
        """
        Toggles the visibility of conversation branches, either isolating the
        current branch or showing all branches.
        """
        visibility_nodes = self._all_branch_visibility_nodes()

        if self.is_branch_hidden:
            for node in visibility_nodes:
                self._set_branch_focus_state(node, True)
            self.is_branch_hidden = False
            return

        active_branch = set()

        # Helper to find all ancestors of a node.
        def get_ancestors(node):
            ancestors = set()
            current = node
            while current:
                ancestors.add(current)
                current = current.parent_node
            return ancestors

        # Helper to find all descendants of a node.
        def get_descendants(node):
            descendants = set()
            nodes_to_visit = [node]
            while nodes_to_visit:
                current = nodes_to_visit.pop(0)
                if current not in descendants:
                    descendants.add(current)
                    nodes_to_visit.extend(current.children)
            return descendants

        branch_origins = self._branch_anchor_nodes(originating_node)
        if not branch_origins:
            branch_origins = {originating_node}

        # The active branch includes all ancestors and descendants of every
        # conversational anchor associated with the originating item.
        for branch_origin in branch_origins:
            active_branch.update(get_ancestors(branch_origin))
            active_branch.update(get_descendants(branch_origin))

        for node in visibility_nodes:
            is_active = bool(self._branch_anchor_nodes(node) & active_branch)
            self._set_branch_focus_state(node, is_active)
        
        self.is_branch_hidden = True

    def _get_node_dimensions(self, node):
        """Helper to get a consistent width and height for any node type."""
        if hasattr(node, 'width') and hasattr(node, 'height'):
            return node.width, node.height
        bounds = node.boundingRect()
        return bounds.width(), bounds.height()

    def _position_subtree(self, node, x, y):
        """
        Recursively positions a node and its entire subtree in a horizontal layout.
        This is a post-order traversal algorithm.

        Args:
            node (QGraphicsItem): The current root of the subtree to position.
            x (float): The target starting X coordinate for this node.
            y (float): The target starting Y coordinate for this subtree.

        Returns:
            QRectF: The total bounding box of the positioned subtree in scene coordinates.
        """
        node_width, node_height = self._get_node_dimensions(node)

        # Base case: A leaf node is simply positioned.
        if not hasattr(node, 'children') or not node.children:
            node.setPos(x, y)
            return QRectF(x, y, node_width, node_height)

        # Recursive step: Position all child subtrees first.
        child_bounds = []
        current_child_y = y
        child_x = x + node_width + self.horizontal_spacing
        
        for child in node.children:
            bounds = self._position_subtree(child, child_x, current_child_y)
            child_bounds.append(bounds)
            current_child_y = bounds.bottom() + self.vertical_spacing

        # Calculate the total bounding box of all child subtrees.
        total_children_bounds = QRectF()
        if child_bounds:
            total_children_bounds = child_bounds[0]
            for bounds in child_bounds[1:]:
                total_children_bounds = total_children_bounds.united(bounds)

        # Position the parent node vertically centered against its children's block.
        parent_y = total_children_bounds.center().y() - node_height / 2
        node.setPos(x, parent_y)

        # The final bounding box is the union of the parent's new position and the children's block.
        parent_rect = QRectF(x, parent_y, node_width, node_height)
        return parent_rect.united(total_children_bounds)

    def _infer_chart_parent_node(self, chart):
        candidates = [node for node in self._all_conversational_nodes() if node.scene() == self]
        if not candidates:
            return None

        best_match = None
        best_score = None
        chart_pos = chart.pos()
        for candidate in candidates:
            expected_x = candidate.pos().x() + 450
            expected_y = candidate.pos().y()
            score = abs(chart_pos.x() - expected_x) + (abs(chart_pos.y() - expected_y) * 1.5)
            if best_score is None or score < best_score:
                best_score = score
                best_match = candidate

        return best_match if best_score is not None and best_score < 900 else None

    def organize_nodes(self):
        """
        Automatically arranges all nodes into a non-overlapping, horizontal tree layout.
        """
        all_conversational_nodes = self._all_conversational_nodes()
        if not all_conversational_nodes:
            return

        # Identify all root nodes (nodes without a parent).
        root_nodes = [node for node in all_conversational_nodes if not (hasattr(node, 'parent_node') and node.parent_node)]
        # Sort roots by their current Y position to maintain a stable vertical order for separate trees.
        root_nodes.sort(key=lambda n: n.pos().y())

        current_y_offset = 50.0
        # Process each tree independently, stacking them vertically at the start of the scene.
        for root in root_nodes:
            # Position the entire tree and get its total bounding box.
            tree_bounds = self._position_subtree(root, 50.0, current_y_offset)
            # Update the Y offset for the next tree, adding extra spacing.
            current_y_offset = tree_bounds.bottom() + self.vertical_spacing * 2
        
        # After main tree layout, position associated content nodes (code, docs, etc.)
        # directly below their respective parent nodes.
        for chart in self.chart_nodes:
            if getattr(chart, 'parent_content_node', None) is None:
                chart.parent_content_node = self._infer_chart_parent_node(chart)

        all_content_nodes = self._all_content_nodes()
        for parent_node in all_conversational_nodes:
            associated_content = sorted(
                [cn for cn in all_content_nodes if hasattr(cn, 'parent_content_node') and cn.parent_content_node == parent_node],
                key=lambda n: n.pos().y()
            )
            
            if associated_content:
                parent_width, parent_height = self._get_node_dimensions(parent_node)
                current_content_y = parent_node.pos().y() + parent_height + 50
                for content_node in associated_content:
                    content_node_height = self._get_node_dimensions(content_node)[1]
                    content_node.setPos(QPointF(parent_node.pos().x(), current_content_y))
                    current_content_y += content_node_height + 20

        self.update_connections()
        self.scene_changed.emit()

    def _remove_associated_chart_nodes(self, parent_node):
        charts_to_remove = [chart for chart in self.chart_nodes if getattr(chart, 'parent_content_node', None) == parent_node]
        for chart in charts_to_remove:
            chart_parent = chart.parentItem()
            if isinstance(chart_parent, Frame) and chart in chart_parent.nodes:
                chart_parent.nodes.remove(chart)
                if chart_parent.nodes:
                    chart_parent.updateGeometry()
                else:
                    self.deleteFrame(chart_parent)
            elif isinstance(chart_parent, Container) and chart in chart_parent.contained_items:
                chart_parent.contained_items.remove(chart)
                if chart_parent.contained_items:
                    chart_parent.updateGeometry()
                else:
                    self.deleteContainer(chart_parent)
            if hasattr(chart, "dispose"):
                chart.dispose()
            if chart.scene() == self:
                self.removeItem(chart)
            if chart in self.chart_nodes:
                self.chart_nodes.remove(chart)

    def remove_associated_content_nodes(self, chat_node):
        """
        Finds and removes all Code, Document, and Image nodes linked to a given ChatNode.

        Args:
            chat_node (ChatNode): The parent ChatNode.
        """
        # Remove associated code nodes.
        nodes_to_remove = [cn for cn in self.code_nodes if cn.parent_content_node == chat_node]
        for node in nodes_to_remove:
            for conn in self.content_connections[:]:
                if conn.end_node == node:
                    self.removeItem(conn); self.content_connections.remove(conn)
            self.removeItem(node)
            if node in self.code_nodes: self.code_nodes.remove(node)

        # Remove associated document nodes.
        docs_to_remove = [dn for dn in self.document_nodes if dn.parent_content_node == chat_node]
        for node in docs_to_remove:
            for conn in self.document_connections[:]:
                if conn.end_node == node:
                    self.removeItem(conn); self.document_connections.remove(conn)
            self.removeItem(node)
            if node in self.document_nodes: self.document_nodes.remove(node)
        
        # Remove associated image nodes.
        images_to_remove = [im for im in self.image_nodes if im.parent_content_node == chat_node]
        for node in images_to_remove:
            for conn in self.image_connections[:]:
                if conn.end_node == node:
                    self.removeItem(conn); self.image_connections.remove(conn)
            self.removeItem(node)
            if node in self.image_nodes: self.image_nodes.remove(node)

        # Remove associated thinking nodes.
        thinking_to_remove = [tn for tn in self.thinking_nodes if tn.parent_content_node == chat_node]
        for node in thinking_to_remove:
            for conn in self.thinking_connections[:]:
                if conn.end_node == node:
                    self.removeItem(conn); self.thinking_connections.remove(conn)
            self.removeItem(node)
            if node in self.thinking_nodes: self.thinking_nodes.remove(node)

        self._remove_associated_chart_nodes(chat_node)
        
        self.scene_changed.emit()

    def delete_chat_node(self, node_to_delete):
        """
        Safely deletes a ChatNode, re-parenting its children and cleaning up all connections.

        Args:
            node_to_delete (ChatNode): The node to be deleted.
        """
        try:
            if not self or not node_to_delete.scene(): return

            # Atomically remove all attached content nodes first.
            self.remove_associated_content_nodes(node_to_delete)
            self._remove_graph_diffs_for_source(node_to_delete)

            children, parent_node = node_to_delete.children[:], node_to_delete.parent_node

            # Remove the node from any frame it might be in.
            for frame in self.frames[:]:
                if node_to_delete in frame.nodes:
                    frame.nodes.remove(node_to_delete)
                    if not frame.nodes: self.removeItem(frame); self.frames.remove(frame)
                    else: frame.updateGeometry()

            # Re-parent the children to the deleted node's parent.
            if parent_node:
                if node_to_delete in parent_node.children:
                    parent_node.children.remove(node_to_delete)
                for child in children:
                    child.parent_node = parent_node
                    if child not in parent_node.children:
                        parent_node.children.append(child)
                    
                    new_conn = ConnectionItem(parent_node, child)
                    child.incoming_connection = new_conn
                    self.addItem(new_conn)
                    self.connections.append(new_conn)
            else:
                for child in children:
                    child.parent_node = None
            
            # Remove all connections attached to the deleted node.
            self._remove_connections_for_node(node_to_delete)

            node_to_delete.children.clear()
            node_to_delete.parent_node = None

            if node_to_delete in self.nodes: self.nodes.remove(node_to_delete)
            self.removeItem(node_to_delete)
            self.update_connections()
            
            if self.window and self.window.current_node == node_to_delete:
                self.window.current_node = None
                self.window.message_input.setPlaceholderText("Type your message...")
            
            self.scene_changed.emit()
        except Exception as e:
            QMessageBox.critical(None, "Error", f"An error occurred while deleting the node: {str(e)}")

    def _delete_graph_diff_node(self, diff_node):
        if not diff_node:
            return

        worker_thread = getattr(diff_node, "worker_thread", None)

        for conn in self.graph_diff_connections[:]:
            if diff_node in (conn.start_node, conn.end_node):
                if conn.scene() == self:
                    self.removeItem(conn)
                self.graph_diff_connections.remove(conn)

        if hasattr(diff_node, "dispose"):
            diff_node.dispose()

        if diff_node.scene() == self:
            self.removeItem(diff_node)
        if diff_node in self.graph_diff_nodes:
            self.graph_diff_nodes.remove(diff_node)
        if self.window and self.window.current_node == diff_node:
            self.window.current_node = None
            self.window.message_input.setPlaceholderText("Type your message...")
        if self.window and getattr(self.window, "graph_diff_thread", None) is worker_thread:
            self.window.graph_diff_thread = None

    def _remove_graph_diffs_for_source(self, source_node):
        for diff_node in self.graph_diff_nodes[:]:
            if source_node in (getattr(diff_node, 'left_source_node', None), getattr(diff_node, 'right_source_node', None)):
                self._delete_graph_diff_node(diff_node)


    def deleteSelectedItems(self):
        """
        Deletes all currently selected items, handling each type appropriately.
        """
        for item in list(self.selectedItems()):
            if isinstance(item, ChatNode): self.delete_chat_node(item)
            elif isinstance(item, CodeNode):
                for conn in self.content_connections[:]:
                    if conn.end_node == item: self.removeItem(conn); self.content_connections.remove(conn)
                self.removeItem(item)
                if item in self.code_nodes: self.code_nodes.remove(item)
            elif isinstance(item, DocumentNode):
                for conn in self.document_connections[:]:
                    if conn.end_node == item: self.removeItem(conn); self.document_connections.remove(conn)
                self.removeItem(item)
                if item in self.document_nodes: self.document_nodes.remove(item)
            elif isinstance(item, ImageNode):
                for conn in self.image_connections[:]:
                    if conn.end_node == item: self.removeItem(conn); self.image_connections.remove(conn)
                self.removeItem(item)
                if item in self.image_nodes: self.image_nodes.remove(item)
            elif isinstance(item, ThinkingNode):
                for conn in self.thinking_connections[:]:
                    if conn.end_node == item: self.removeItem(conn); self.thinking_connections.remove(conn)
                self.removeItem(item)
                if item in self.thinking_nodes: self.thinking_nodes.remove(item)
            elif isinstance(item, PyCoderNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.pycoder_nodes: self.pycoder_nodes.remove(item)
            elif isinstance(item, CodeSandboxNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.code_sandbox_nodes: self.code_sandbox_nodes.remove(item)
            elif isinstance(item, WebNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.web_nodes: self.web_nodes.remove(item)
            elif isinstance(item, ConversationNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.conversation_nodes: self.conversation_nodes.remove(item)
            elif isinstance(item, ReasoningNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.reasoning_nodes: self.reasoning_nodes.remove(item)
            elif isinstance(item, HtmlViewNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.html_view_nodes: self.html_view_nodes.remove(item)
            elif isinstance(item, ArtifactNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.artifact_nodes: self.artifact_nodes.remove(item)
            elif isinstance(item, WorkflowNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.workflow_nodes: self.workflow_nodes.remove(item)
            elif isinstance(item, QualityGateNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                worker_thread = getattr(item, "worker_thread", None)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.quality_gate_nodes: self.quality_gate_nodes.remove(item)
                if self.window and getattr(self.window, "quality_gate_thread", None) is worker_thread:
                    self.window.quality_gate_thread = None
                if self.window and self.window.current_node == item:
                    self.window.current_node = None
                    self.window.message_input.setPlaceholderText("Type your message...")
            elif isinstance(item, CodeReviewNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                worker_thread = getattr(item, "worker_thread", None)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.code_review_nodes: self.code_review_nodes.remove(item)
                if self.window and getattr(self.window, "code_review_thread", None) is worker_thread:
                    self.window.code_review_thread = None
                if self.window and self.window.current_node == item:
                    self.window.current_node = None
                    self.window.message_input.setPlaceholderText("Type your message...")
            elif isinstance(item, GitlinkNode):
                self._remove_associated_chart_nodes(item)
                self._remove_graph_diffs_for_source(item)
                worker_thread = getattr(item, "worker_thread", None)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.gitlink_nodes: self.gitlink_nodes.remove(item)
                if self.window and getattr(self.window, "gitlink_thread", None) is worker_thread:
                    self.window.gitlink_thread = None
                if self.window and self.window.current_node == item:
                    self.window.current_node = None
                    self.window.message_input.setPlaceholderText("Type your message...")
            elif isinstance(item, GraphDiffNode):
                self._remove_associated_chart_nodes(item)
                self._delete_graph_diff_node(item)
            elif isinstance(item, Frame): self.deleteFrame(item)
            elif isinstance(item, Container): self.deleteContainer(item)
            elif isinstance(item, Note):
                for conn_list in [self.system_prompt_connections, self.group_summary_connections]:
                    for conn in conn_list[:]:
                        if item in (conn.start_node, conn.end_node): self.removeItem(conn); conn_list.remove(conn)
                self.removeItem(item)
                if item in self.notes: self.notes.remove(item)
            elif isinstance(item, ChartItem):
                parent = item.parentItem()
                if isinstance(parent, Frame) and item in parent.nodes:
                    parent.nodes.remove(item)
                    if not parent.nodes:
                        self.deleteFrame(parent)
                    else:
                        parent.updateGeometry()
                elif isinstance(parent, Container) and item in parent.contained_items:
                    parent.contained_items.remove(item)
                    if not parent.contained_items:
                        self.deleteContainer(parent)
                    else:
                        parent.updateGeometry()
                if hasattr(item, "dispose"):
                    item.dispose()
                self.removeItem(item)
                if item in self.chart_nodes: self.chart_nodes.remove(item)
            elif isinstance(item, NavigationPin):
                if hasattr(self.window, 'pin_overlay') and self.window.pin_overlay: self.window.pin_overlay.remove_pin(item)
                if item in self.pins: self.pins.remove(item)
                self.removeItem(item)
        self.update_connections()
        self.scene_changed.emit()

    def _clear_smart_guides(self):
        """Removes all smart guide lines from the scene."""
        for line in self.smart_guide_lines:
            self.removeItem(line)
        self.smart_guide_lines.clear()

    def _calculate_smart_guide_snap(self, moving_item, new_pos):
        """
        Calculates a new position for a moving item by checking for alignment
        with other static items in the scene (smart guides).

        Args:
            moving_item (QGraphicsItem): The item being moved.
            new_pos (QPointF): The proposed new position.

        Returns:
            QPointF: The snapped position, or the original position if no snap occurred.
        """
        ALIGNMENT_TOLERANCE = 5
        snapped_pos = QPointF(new_pos)
        snapped_x, snapped_y = False, False

        # Calculate the future bounding rect of the moving item.
        moving_rect = moving_item.sceneBoundingRect()
        offset = new_pos - moving_item.pos()
        moving_rect.translate(offset)
        
        # Define the key alignment points for the moving item.
        moving_points = {
            'v_left': moving_rect.left(), 'v_center': moving_rect.center().x(), 'v_right': moving_rect.right(),
            'h_top': moving_rect.top(), 'h_middle': moving_rect.center().y(), 'h_bottom': moving_rect.bottom(),
        }

        static_items = [item for item in self.items() if isinstance(item, (ChatNode, CodeNode, Note, Frame, ChartItem, DocumentNode, ImageNode, ThinkingNode, Container, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, ArtifactNode, WorkflowNode, GraphDiffNode, QualityGateNode, CodeReviewNode, GitlinkNode)) and item != moving_item and not item.isSelected()]

        for static_item in static_items:
            static_rect = static_item.sceneBoundingRect()
            static_points = {
                'v_left': static_rect.left(), 'v_center': static_rect.center().x(), 'v_right': static_rect.right(),
                'h_top': static_rect.top(), 'h_middle': static_rect.center().y(), 'h_bottom': static_rect.bottom(),
            }

            # Check for vertical alignment (left, center, right edges).
            if not snapped_x:
                for m_key, m_val in moving_points.items():
                    if m_key.startswith('v_'):
                        for s_key, s_val in static_points.items():
                            if s_key == m_key and abs(m_val - s_val) < ALIGNMENT_TOLERANCE:
                                snapped_pos.setX(snapped_pos.x() + (s_val - m_val))
                                # Draw a visual guide line.
                                y1, y2 = min(moving_rect.top(), static_rect.top()), max(moving_rect.bottom(), static_rect.bottom())
                                line = QGraphicsLineItem(s_val, y1, s_val, y2)
                                line.setPen(QPen(QColor(255, 0, 100, 200), 1, Qt.PenStyle.DashLine))
                                self.addItem(line)
                                self.smart_guide_lines.append(line)
                                snapped_x = True
                                break
                    if snapped_x: break
            
            # Check for horizontal alignment (top, middle, bottom edges).
            if not snapped_y:
                for m_key, m_val in moving_points.items():
                    if m_key.startswith('h_'):
                        for s_key, s_val in static_points.items():
                            if s_key == m_key and abs(m_val - s_val) < ALIGNMENT_TOLERANCE:
                                snapped_pos.setY(snapped_pos.y() + (s_val - m_val))
                                x1, x2 = min(moving_rect.left(), static_rect.left()), max(moving_rect.right(), static_rect.right())
                                line = QGraphicsLineItem(x1, s_val, x2, s_val)
                                line.setPen(QPen(QColor(255, 0, 100, 200), 1, Qt.PenStyle.DashLine))
                                self.addItem(line)
                                self.smart_guide_lines.append(line)
                                snapped_y = True
                                break
                    if snapped_y: break
            
            if snapped_x and snapped_y:
                return snapped_pos
        
        return snapped_pos

    def snap_position(self, item, new_pos):
        """
        Determines the final snapped position of an item, prioritizing smart guides
        over the grid.

        Args:
            item (QGraphicsItem): The item being moved.
            new_pos (QPointF): The proposed new position.

        Returns:
            QPointF: The final, snapped position.
        """
        self._clear_smart_guides()
        snapped_pos = QPointF(new_pos)
        
        x_was_snapped, y_was_snapped = False, False
        if self.smart_guides and self.is_dragging_item:
            guide_snapped_pos = self._calculate_smart_guide_snap(item, new_pos)
            x_was_snapped = abs(guide_snapped_pos.x() - new_pos.x()) > 0.1
            y_was_snapped = abs(guide_snapped_pos.y() - new_pos.y()) > 0.1
            snapped_pos = guide_snapped_pos

        if self.snap_to_grid:
            grid_size = self.views()[0].grid_control.grid_size
            if not x_was_snapped:
                snapped_pos.setX(round(new_pos.x() / grid_size) * grid_size)
            if not y_was_snapped:
                snapped_pos.setY(round(new_pos.y() / grid_size) * grid_size)
                
        return snapped_pos

    def clear(self):
        """
        Clears the entire scene, removing all items and resetting all tracking lists.
        """
        super().clear()
        self.nodes.clear()
        self.connections.clear()
        self.frames.clear()
        self.containers.clear()
        self.pins.clear()
        self.notes.clear()
        self.code_nodes.clear()
        self.document_nodes.clear()
        self.image_nodes.clear()
        self.thinking_nodes.clear()
        self.pycoder_nodes.clear()
        self.code_sandbox_nodes.clear()
        self.web_nodes.clear()
        self.conversation_nodes.clear()
        self.reasoning_nodes.clear()
        self.html_view_nodes.clear()
        self.artifact_nodes.clear()
        self.workflow_nodes.clear()
        self.graph_diff_nodes.clear()
        self.quality_gate_nodes.clear()
        self.code_review_nodes.clear()
        self.gitlink_nodes.clear()
        self.chart_nodes.clear()
        
        self.content_connections.clear()
        self.document_connections.clear()
        self.image_connections.clear()
        self.thinking_connections.clear()
        self.system_prompt_connections.clear()
        self.pycoder_connections.clear()
        self.code_sandbox_connections.clear()
        self.web_connections.clear()
        self.conversation_connections.clear()
        self.reasoning_connections.clear()
        self.group_summary_connections.clear()
        self.html_connections.clear()
        self.artifact_connections.clear()
        self.workflow_connections.clear()
        self.graph_diff_connections.clear()
        self.quality_gate_connections.clear()
        self.code_review_connections.clear()
        self.gitlink_connections.clear()
        
        if hasattr(self, 'window') and self.window:
            self.window.current_node = None
        self.scene_changed.emit()
