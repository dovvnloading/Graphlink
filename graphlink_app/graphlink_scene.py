from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsScene, QMessageBox, QGraphicsLineItem
)
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import QColor, QPen, QTransform

from graphlink_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphlink_connections import (
    ConnectionItem, ContentConnectionItem, SystemPromptConnectionItem,
    DocumentConnectionItem, ImageConnectionItem, PyCoderConnectionItem,
    ConversationConnectionItem, GroupSummaryConnectionItem,
    HtmlConnectionItem, ThinkingConnectionItem
)
from graphlink_canvas_items import Frame, Note, NavigationPin, ChartItem, Container
from graphlink_navigation_pins import NavigationPinStore
from graphlink_pycoder import PyCoderNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_web import WebNode, WebConnectionItem
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode, ArtifactConnectionItem
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkNode, GitlinkConnectionItem
from graphlink_memory import clone_history, resolve_branch_parent

class ChatScene(QGraphicsScene):
    BRANCH_DIM_OPACITY = 0.18

    """
    The core data model and controller for the Graphlink canvas.

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
        # NavigationPinStore is the authoritative record/order source. ``pins``
        # remains a compatibility projection for scene code during migration.
        self.pin_store = NavigationPinStore()
        self.notes = []
        self.code_nodes = []
        self.document_nodes = []
        self.image_nodes = []
        self.thinking_nodes = []
        self.pycoder_nodes = []
        self.code_sandbox_nodes = []
        self.web_nodes = []
        self.conversation_nodes = []
        self.html_view_nodes = []
        self.artifact_nodes = []
        self.gitlink_nodes = []
        self.chart_nodes = []
        self.chart_connections = []
        self.transient_layout_items = []
        self._connection_index = {}
        self._scene_change_pending = False
        
        self.content_connections = []
        self.document_connections = []
        self.image_connections = []
        self.thinking_connections = []
        self.system_prompt_connections = []
        self.pycoder_connections = []
        self.code_sandbox_connections = []
        self.web_connections = []
        self.conversation_connections = []
        self.group_summary_connections = []
        self.html_connections = []
        self.artifact_connections = []
        self.gitlink_connections = []

        self.setBackgroundBrush(QColor("#252525"))
        
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
        self.font_color = QColor("#DDDDDD")
        
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

    def update_view_lod(self, view_rect=None, zoom=None):
        for item in self.items():
            sync_lod = getattr(item, "sync_view_lod", None)
            if callable(sync_lod):
                sync_lod(view_rect, zoom)

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
            self.group_summary_connections,
            self.html_connections,
            self.artifact_connections,
            self.gitlink_connections,
            self.chart_connections,
        ]

    def register_connection(self, connection):
        """Add a connection to the endpoint index used during node drags."""
        if connection is None:
            return
        for endpoint in (getattr(connection, "start_node", None), getattr(connection, "end_node", None)):
            if endpoint is not None:
                self._connection_index.setdefault(endpoint, set()).add(connection)

    def unregister_connection(self, connection):
        for endpoint in (getattr(connection, "start_node", None), getattr(connection, "end_node", None)):
            connections = self._connection_index.get(endpoint)
            if not connections:
                continue
            connections.discard(connection)
            if not connections:
                self._connection_index.pop(endpoint, None)

    def rebuild_connection_index(self):
        self._connection_index.clear()
        for conn_list in self._all_connection_lists():
            for connection in conn_list:
                self.register_connection(connection)

    def connections_for_node(self, node):
        return tuple(
            connection for connection in self._connection_index.get(node, ())
            if connection.scene() == self
        )

    def _schedule_scene_changed(self):
        if self._scene_change_pending:
            return
        self._scene_change_pending = True
        QTimer.singleShot(0, self._emit_scheduled_scene_changed)

    def _emit_scheduled_scene_changed(self):
        self._scene_change_pending = False
        self.scene_changed.emit()

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
                self.unregister_connection(conn)
                if conn in conn_list:
                    conn_list.remove(conn)

    def _update_all_node_fonts(self):
        """Iterates through all nodes that support font changes and applies the current settings."""
        nodes_to_update = list(dict.fromkeys(
            self._all_layout_nodes() + self.notes + self.frames + self.containers
        ))
        for node in nodes_to_update:
            if hasattr(node, 'update_font_settings'):
                node.update_font_settings(self.font_family, self.font_size, self.font_color)
            if hasattr(node, '_recalculate_geometry'):
                node._recalculate_geometry()

        self.update_connections()
        self.scene_changed.emit()

    def resolve_chart_parent(self, node):
        """Return the nearest conversational owner for a chart source."""
        conversational = set(self._all_conversational_nodes())
        current = node
        visited = set()
        while current is not None and id(current) not in visited:
            if current in conversational:
                return current
            visited.add(id(current))
            current = (
                getattr(current, "parent_content_node", None)
                or getattr(current, "parent_node", None)
            )
        return None

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
        
        all_nodes = self._all_layout_nodes()
        utility_items = self.notes + self.frames + self.containers
        for node in all_nodes + utility_items:
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
            elif isinstance(node, ArtifactNode):
                content = node.get_artifact_content() + "\n" + node.chat_html_cache
            elif isinstance(node, GitlinkNode):
                content = node.get_task_prompt() + "\n" + node.context_xml + "\n" + node.proposal_markdown + "\n" + node.preview_text
            elif isinstance(node, PyCoderNode):
                content = node.get_prompt() + "\n" + node.get_code() + "\n" + node.get_output()
            elif isinstance(node, CodeSandboxNode):
                content = node.get_prompt() + "\n" + node.get_requirements() + "\n" + node.get_code() + "\n" + node.get_output()
            elif isinstance(node, ChartItem):
                content = node.to_context_text() if hasattr(node, "to_context_text") else str(node.data)
            elif isinstance(node, Note):
                content = getattr(node, "content", "")
            elif isinstance(node, Frame):
                content = getattr(node, "note", "")
            elif isinstance(node, Container):
                content = getattr(node, "title", "")

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
        all_nodes = self._all_layout_nodes() + self.notes + self.frames + self.containers
        for node in all_nodes:
            is_match = node in matched_nodes
            if getattr(node, 'is_search_match', False) != is_match:
                node.is_search_match = is_match
                node.update()

    def add_chat_node(self, text, is_user=True, parent_node=None, conversation_history=None, preferred_pos=None):
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
                valid_parent_types = self._all_conversational_nodes()
                if parent_node not in valid_parent_types or not parent_node.scene():
                    print("Warning: Parent node is invalid or no longer in the scene.")
                    parent_node = None
            
            node = ChatNode(text, is_user)
            if conversation_history:
                node.conversation_history = clone_history(conversation_history)
            
            # If there's a parent, position the new node relative to it and create a connection.
            if parent_node:
                parent_node.children.append(node)
                node.parent_node = parent_node
                
                # Find an open position to the right of the parent.
                if preferred_pos is not None:
                    node.setPos(QPointF(preferred_pos))
                else:
                    node.setPos(self.find_branch_position(parent_node, node))
                
                connection = ConnectionItem(parent_node, node)
                node.incoming_connection = connection
                self.addItem(connection)
                self.connections.append(connection)
                self.register_connection(connection)
            else:
                # Default position for root nodes.
                root_base = QPointF(preferred_pos) if preferred_pos is not None else QPointF(50, 150)
                node.setPos(root_base if preferred_pos is not None else self.find_free_position(root_base, node, strategy="general"))
            
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
        last_y = parent_node.scenePos().y() + parent_node.height
        
        # Check for existing content nodes to stack below them
        all_content_nodes = self.code_nodes + self.document_nodes + self.image_nodes + self.thinking_nodes
        for node in all_content_nodes:
            if hasattr(node, 'parent_content_node') and node.parent_content_node == parent_node:
                last_y = max(last_y, node.scenePos().y() + node.height)
                
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
        node.setPos(self.find_content_position(parent_content_node, node))
        
        self.addItem(node)
        self.code_nodes.append(node)
        
        connection = ContentConnectionItem(parent_content_node, node)
        self.addItem(connection)
        self.content_connections.append(connection)
        self.register_connection(connection)
        
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
        node.setPos(self.find_content_position(parent_chat_node, node))
        
        self.addItem(node)
        self.image_nodes.append(node)
        
        connection = ImageConnectionItem(parent_chat_node, node)
        self.addItem(connection)
        self.image_connections.append(connection)
        self.register_connection(connection)
        
        self.scene_changed.emit()
        return node

    def add_document_node(
        self,
        title,
        content,
        parent_user_node,
        attachment_kind="document",
        file_path="",
        mime_type=None,
        duration_seconds=None,
        byte_size=None,
        preview_label=None,
    ):
        """
        Creates and adds a new DocumentNode.

        Args:
            title (str): The title of the document (usually the filename).
            content (str): The text content of the document.
            parent_user_node (ChatNode): The user's ChatNode that included the document.

        Returns:
            DocumentNode: The newly created node.
        """
        node = DocumentNode(
            title,
            content,
            parent_user_node,
            attachment_kind=attachment_kind,
            file_path=file_path,
            mime_type=mime_type,
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            preview_label=preview_label,
        )
        node.setPos(self.find_content_position(parent_user_node, node))
        
        self.addItem(node)
        self.document_nodes.append(node)
        
        connection = DocumentConnectionItem(parent_user_node, node)
        self.addItem(connection)
        self.document_connections.append(connection)
        self.register_connection(connection)
        
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
        node.setPos(self.find_content_position(parent_chat_node, node))
        
        self.addItem(node)
        self.thinking_nodes.append(node)
        
        connection = ThinkingConnectionItem(parent_chat_node, node)
        self.addItem(connection)
        self.thinking_connections.append(connection)
        self.register_connection(connection)
        
        self.scene_changed.emit()
        return node

    def nodeMoved(self, node):
        """
        Callback triggered when a node is moved. Updates all attached connections.

        Args:
            node (QGraphicsItem): The node that was moved.
        """
        # Ensure the node is a valid, tracked item before proceeding.
        valid_types = self._all_layout_nodes()
        if not isinstance(node, (Note, Container)) and node not in valid_types or not node.scene():
            return

        for frame in self.frames:
            if node in frame.nodes and not frame.resizing:
                frame.updateGeometry()
            
        # Iterate through all connection types and update any connected to the moved node.
        # Note: Slicing `[:]` creates a copy, allowing safe removal from the list during iteration if a connection is invalid.
        for conn in self.connections_for_node(node):
            conn.update_path()

        self._schedule_scene_changed()
                        
    def _navigation_pin_item(self, pin_id):
        return next((pin for pin in self.pins if pin.pin_id == pin_id and pin.scene() == self), None)

    def _on_navigation_pin_edit_requested(self, pin_id):
        pin = self._navigation_pin_item(pin_id)
        if pin is not None and self.window and hasattr(self.window, "edit_navigation_pin"):
            self.window.edit_navigation_pin(pin)

    def _on_navigation_pin_move_committed(self, pin_id, position):
        if self.pin_store.get(pin_id) is None:
            return
        self.pin_store.move(pin_id, position.x(), position.y())
        self._schedule_scene_changed()

    def add_navigation_pin(self, pos, title=None, note="", pin_id=None, anchor_item_id=None):
        """
        Adds a new NavigationPin to the scene at the specified position.

        Args:
            pos (QPointF): The scene position for the new pin.

        Returns:
            NavigationPin: The created pin item.
        """
        if title is None or not str(title).strip():
            title = f"Waypoint {len(self.pin_store.records) + 1}"

        record = self.pin_store.add(
            title=title,
            note=note,
            x=pos.x(),
            y=pos.y(),
            pin_id=pin_id,
            anchor_item_id=anchor_item_id,
        )
        pin = NavigationPin(title=record.title, note=record.note, pin_id=record.pin_id)
        pin.editRequested.connect(self._on_navigation_pin_edit_requested)
        pin.contextMenuRequested.connect(self._on_navigation_pin_context_requested)
        pin.positionCommitted.connect(self._on_navigation_pin_move_committed)
        pin.setPos(pos)
        self.addItem(pin)
        self.pins.append(pin)
        self.scene_changed.emit()
        return pin

    def _on_navigation_pin_context_requested(self, pin_id, screen_pos):
        pin = self._navigation_pin_item(pin_id)
        if pin is not None and self.window and hasattr(self.window, "show_navigation_pin_context_menu"):
            self.window.show_navigation_pin_context_menu(pin, screen_pos)

    def update_navigation_pin(self, pin, *, title=None, note=None):
        """Commit validated metadata through the authoritative pin store."""
        if pin not in self.pins or pin.scene() != self:
            return None
        changes = {}
        if title is not None:
            changes["title"] = title
        if note is not None:
            changes["note"] = note
        if not changes:
            return self.pin_store.get(pin.pin_id)
        record = self.pin_store.update(pin.pin_id, **changes)
        pin.apply_metadata(record.title, record.note)
        self.scene_changed.emit()
        return record

    def remove_navigation_pin(self, pin_or_id):
        """Remove a pin and its record exactly once."""
        pin_id = getattr(pin_or_id, "pin_id", pin_or_id)
        pin = self._navigation_pin_item(pin_id)
        removed = self.pin_store.remove(pin_id)
        if pin is not None:
            pin.setSelected(False)
            self.removeItem(pin)
            if pin in self.pins:
                self.pins.remove(pin)
        if removed is not None or pin is not None:
            self.scene_changed.emit()
        return removed

    def ordered_navigation_pins(self):
        """Return live graphics items in explicit store order."""
        by_id = {pin.pin_id: pin for pin in self.pins if pin.scene() == self}
        return [by_id[record.pin_id] for record in self.pin_store.records if record.pin_id in by_id]

    def clear_navigation_pins(self):
        for pin in list(self.pins):
            if pin.scene() == self:
                self.removeItem(pin)
        self.pins.clear()
        self.pin_store.clear()
        self.scene_changed.emit()
    
    def add_chart(self, data, pos, parent_content_node=None, source_node=None):
        """Adds a new ChartItem to the scene."""
        source_node = source_node or parent_content_node
        resolved_parent = self.resolve_chart_parent(parent_content_node or source_node)
        chart = ChartItem(data, pos, parent_content_node=resolved_parent)
        chart.source_node = source_node if source_node in self._all_layout_nodes() else resolved_parent
        strategy = "content" if resolved_parent is not None else "general"
        chart.setPos(self.find_free_position(pos, chart, strategy=strategy))
        self.chart_nodes.append(chart)
        self.addItem(chart)
        if chart.source_node is not None and chart.source_node is not chart and chart.source_node.scene() == self:
            connection = ContentConnectionItem(chart.source_node, chart)
            self.addItem(connection)
            self.chart_connections.append(connection)
            self.register_connection(connection)
        self.scene_changed.emit()
        return chart

    def _detach_item_from_groups(self, item, remove_empty=True):
        """Detach an item through one ownership path and repair stale indexes."""
        parent = item.parentItem()
        known_parents = []
        if isinstance(parent, (Frame, Container)):
            known_parents.append(parent)
        for group in list(self.frames) + list(self.containers):
            members = group.nodes if isinstance(group, Frame) else group.contained_items
            if item in members and group not in known_parents:
                known_parents.append(group)

        scene_pos = item.scenePos()
        if isinstance(parent, (Frame, Container)):
            item.setParentItem(None)
            item.setPos(scene_pos)

        for group in known_parents:
            members = group.nodes if isinstance(group, Frame) else group.contained_items
            while item in members:
                members.remove(item)
            if group.scene() == self and members:
                group.updateGeometry()
            elif remove_empty and group.scene() == self and not members:
                if isinstance(group, Frame):
                    self.deleteFrame(group)
                else:
                    self.deleteContainer(group)
        return known_parents

    def validate_group_invariants(self):
        """Return human-readable group/index violations for tests and diagnostics."""
        violations = []
        memberships = {}
        for group in self.frames:
            for item in group.nodes:
                memberships.setdefault(item, []).append(group)
                if item.parentItem() is not group:
                    violations.append(f"{type(group).__name__} member has wrong parent")
        for group in self.containers:
            for item in group.contained_items:
                memberships.setdefault(item, []).append(group)
                if item.parentItem() is not group:
                    violations.append(f"{type(group).__name__} member has wrong parent")
        for groups in memberships.values():
            if len(groups) > 1:
                violations.append("item appears in multiple groups")
        return violations

    def createFrame(self):
        """Creates a Frame around the currently selected nodes."""
        selected_nodes = [item for item in self.selectedItems() 
                         if isinstance(item, (ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode, ChartItem, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, HtmlViewNode, ArtifactNode, GitlinkNode))]
        
        if not selected_nodes:
            return
            
        for node in selected_nodes:
            self._detach_item_from_groups(node)
        
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
                         if isinstance(item, (ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode, Note, ChartItem, Frame, Container, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, HtmlViewNode, ArtifactNode, GitlinkNode))]
        
        if not selected_items:
            return

        for item in selected_items:
            self._detach_item_from_groups(item)
        
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
        release_parent = frame.parentItem() if isinstance(frame.parentItem(), Container) else None
        if isinstance(frame.parentItem(), Container) and frame in frame.parentItem().contained_items:
            frame.parentItem().contained_items.remove(frame)

        # Un-parent all nodes from the frame, restoring their scene positions.
        for node in frame.nodes:
            scene_pos = node.scenePos()
            if node.parentItem() is frame:
                node.setParentItem(release_parent)
                if release_parent:
                    node.setPos(release_parent.mapFromScene(scene_pos))
                    if node not in release_parent.contained_items:
                        release_parent.contained_items.append(node)
                else:
                    node.setPos(scene_pos)
            node.setVisible(True)
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            node.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            self.nodeMoved(node)
        
        self.removeItem(frame)
        if frame in self.frames:
            self.frames.remove(frame)
        if release_parent and release_parent.scene() == self:
            release_parent.updateGeometry()
        self.scene_changed.emit()

    def deleteContainer(self, container):
        """
        Deletes a Container, releasing its contained items.

        Args:
            container (Container): The container to delete.
        """
        if hasattr(container, 'dispose'):
            container.dispose()
        release_parent = container.parentItem() if isinstance(container.parentItem(), Container) else None
        if release_parent and container in release_parent.contained_items:
            release_parent.contained_items.remove(container)
        for item in list(container.contained_items):
            scene_pos = item.scenePos()
            item.setParentItem(release_parent)
            item.setPos(release_parent.mapFromScene(scene_pos) if release_parent else scene_pos)
            if release_parent and item not in release_parent.contained_items:
                release_parent.contained_items.append(item)
            item.setVisible(True)
            self.nodeMoved(item)
        
        self.removeItem(container)
        if container in self.containers:
            self.containers.remove(container)
        if release_parent and release_parent.scene() == self:
            release_parent.updateGeometry()
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
            self.conversation_nodes + self.html_view_nodes +
            self.artifact_nodes + self.gitlink_nodes
        )

    def _all_content_nodes(self):
        return self.code_nodes + self.document_nodes + self.image_nodes + self.thinking_nodes + self.chart_nodes

    def _all_layout_nodes(self):
        return self._all_conversational_nodes() + self._all_content_nodes()

    def overview_items(self, include_navigation_pins=False):
        """Return visible graph items suitable for Fit All.

        Navigation pins are orientation aids rather than graph content, so they are
        excluded by default and cannot distort Fit All because of a distant bookmark.
        """
        candidates = self._all_layout_nodes() + self.notes + self.frames + self.containers
        if include_navigation_pins:
            candidates += self.ordered_navigation_pins()
        seen = set()
        items = []
        for item in candidates:
            if item in seen or item.scene() != self or not item.isVisible():
                continue
            seen.add(item)
            items.append(item)
        return items

    def overview_rect(self):
        rect = QRectF()
        for item in self.overview_items():
            item_rect = item.sceneBoundingRect()
            rect = item_rect if not rect.isValid() else rect.united(item_rect)
        return rect

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
        for node in self._all_layout_nodes() + self.notes + self.frames + self.containers:
            node.setSelected(True)

    def register_transient_layout_item(self, item):
        if item and item not in self.transient_layout_items:
            self.transient_layout_items.append(item)

    def unregister_transient_layout_item(self, item):
        if item in self.transient_layout_items:
            self.transient_layout_items.remove(item)

    def _spawn_clearance_for(self, item):
        conversational_types = (
            ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode,
            HtmlViewNode, ArtifactNode, GitlinkNode,
        )
        content_types = (CodeNode, DocumentNode, ImageNode, ThinkingNode, ChartItem)

        if isinstance(item, conversational_types):
            return 48.0, 32.0
        if isinstance(item, content_types):
            return 24.0, 24.0
        if isinstance(item, (Note, Frame, Container)):
            return 24.0, 24.0

        return 24.0, 24.0

    def calculate_node_rect(self, node, pos):
        """Calculates the item's scene rect without extra clearance."""
        width, height = self._get_node_dimensions(node)
        return QRectF(pos.x(), pos.y(), width, height)

    def _rectangles_conflict(self, test_rect, obstacle_rect, clearance_x, clearance_y):
        horizontal_clear = (
            test_rect.right() + clearance_x <= obstacle_rect.left()
            or obstacle_rect.right() + clearance_x <= test_rect.left()
        )
        vertical_clear = (
            test_rect.bottom() + clearance_y <= obstacle_rect.top()
            or obstacle_rect.bottom() + clearance_y <= test_rect.top()
        )
        return not (horizontal_clear or vertical_clear)

    def check_collision(self, node, pos, ignore_nodes=None):
        """Checks if placing a node at pos would violate spawn clearances."""
        ignore_nodes = set(ignore_nodes or [])
        test_rect = self.calculate_node_rect(node, pos)
        test_clearance_x, test_clearance_y = self._spawn_clearance_for(node)
        obstacles = self._all_layout_nodes() + self.notes + self.frames + self.containers + self.transient_layout_items
        for obstacle in obstacles:
            if obstacle in ignore_nodes or not obstacle or obstacle.scene() != self:
                continue
            obstacle_rect = self.calculate_node_rect(obstacle, obstacle.scenePos())
            obstacle_clearance_x, obstacle_clearance_y = self._spawn_clearance_for(obstacle)
            clearance_x = max(test_clearance_x, obstacle_clearance_x)
            clearance_y = max(test_clearance_y, obstacle_clearance_y)
            if self._rectangles_conflict(test_rect, obstacle_rect, clearance_x, clearance_y):
                return True
        return False

    def _candidate_offsets(self, limit):
        offsets = [0]
        distance = 1
        while len(offsets) < limit:
            offsets.append(distance)
            if len(offsets) < limit:
                offsets.append(-distance)
            distance += 1
        return offsets

    def _iter_spawn_candidates(self, base_pos, node, strategy):
        width, height = self._get_node_dimensions(node)
        base_x = float(base_pos.x())
        base_y = float(base_pos.y())

        if strategy == "branch":
            column_step = width + 64.0
            row_step = height + 32.0
            for col in range(0, 14):
                x = base_x + (col * column_step)
                for row_offset in self._candidate_offsets(13):
                    yield QPointF(x, base_y + (row_offset * row_step))
            return

        if strategy == "content":
            row_step = height + 24.0
            lateral_step = width + 32.0
            for row in range(0, 14):
                y = base_y + (row * row_step)
                for col_offset in self._candidate_offsets(11):
                    yield QPointF(base_x + (col_offset * lateral_step), y)
            return

        step_x = max(self.horizontal_spacing + 20.0, width + 64.0)
        step_y = max(self.vertical_spacing + 20.0, height + 24.0)
        for ring in range(0, 14):
            if ring == 0:
                yield QPointF(base_x, base_y)
                continue
            delta_x = ring * step_x
            delta_y = ring * step_y
            for row_offset in self._candidate_offsets((ring * 2) + 1):
                yield QPointF(base_x + delta_x, base_y + (row_offset * step_y))
            for col_offset in self._candidate_offsets(ring * 2):
                yield QPointF(base_x + (col_offset * step_x), base_y + delta_y)
                yield QPointF(base_x + (col_offset * step_x), base_y - delta_y)

    def find_free_position(self, base_pos, node, strategy="general", anchor_node=None):
        """Finds a collision-safe scene position for a new node."""
        ignore_nodes = {node}
        if anchor_node is not None:
            ignore_nodes.add(anchor_node)
        for pos in self._iter_spawn_candidates(base_pos, node, strategy):
            if not self.check_collision(node, pos, ignore_nodes=ignore_nodes):
                return pos

        _, height = self._get_node_dimensions(node)
        _, clearance_y = self._spawn_clearance_for(node)
        fallback_y = base_pos.y() + len(self._all_layout_nodes() + self.transient_layout_items + self.notes) * max(self.vertical_spacing, height + clearance_y)
        return QPointF(base_pos.x(), fallback_y)

    def branch_spawn_base(self, parent_node, node=None):
        parent_width, _ = self._get_node_dimensions(parent_node)
        return QPointF(parent_node.scenePos().x() + parent_width + 48.0, parent_node.scenePos().y())

    def find_branch_position(self, parent_node, node):
        return self.find_free_position(self.branch_spawn_base(parent_node, node), node, strategy="branch", anchor_node=parent_node)

    def find_content_position(self, parent_node, node):
        base_pos = QPointF(parent_node.scenePos().x(), parent_node.scenePos().y() + parent_node.height + 32.0)
        return self.find_free_position(base_pos, node, strategy="content", anchor_node=parent_node)

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
        all_nodes = (self._all_conversational_nodes() + self.code_nodes + self.document_nodes +
                     self.image_nodes + self.thinking_nodes)

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
                          self.conversation_connections, self.group_summary_connections,
                          self.html_connections, self.artifact_connections, self.gitlink_connections,
                          self.chart_connections]:
            for conn in conn_list:
                conn.update_path()
                if hasattr(conn, 'sync_visibility_mode'):
                    conn.sync_visibility_mode()

        self.rebuild_connection_index()

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
        owners = {parent_node}
        charts_to_remove = []
        changed = True
        while changed:
            changed = False
            for chart in self.chart_nodes:
                chart_parent = getattr(chart, "parent_content_node", None)
                chart_source = getattr(chart, "source_node", None)
                if chart in charts_to_remove:
                    continue
                if chart_parent in owners or chart_source in owners:
                    charts_to_remove.append(chart)
                    owners.add(chart)
                    changed = True
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
            self._remove_connections_for_node(chart, [self.chart_connections])
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

    def deleteSelectedItems(self):
        """
        Deletes all currently selected items, handling each type appropriately.
        """
        for item in list(self.selectedItems()):
            if not isinstance(item, (Frame, Container)):
                self._detach_item_from_groups(item, remove_empty=False)
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
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.pycoder_nodes: self.pycoder_nodes.remove(item)
            elif isinstance(item, CodeSandboxNode):
                self._remove_associated_chart_nodes(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.code_sandbox_nodes: self.code_sandbox_nodes.remove(item)
            elif isinstance(item, WebNode):
                self._remove_associated_chart_nodes(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.web_nodes: self.web_nodes.remove(item)
            elif isinstance(item, ConversationNode):
                self._remove_associated_chart_nodes(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.conversation_nodes: self.conversation_nodes.remove(item)
            elif isinstance(item, HtmlViewNode):
                self._remove_associated_chart_nodes(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                self.removeItem(item)
                if item in self.html_view_nodes: self.html_view_nodes.remove(item)
            elif isinstance(item, ArtifactNode):
                self._remove_associated_chart_nodes(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.artifact_nodes: self.artifact_nodes.remove(item)
            elif isinstance(item, GitlinkNode):
                self._remove_associated_chart_nodes(item)
                if item.parent_node and item in item.parent_node.children: item.parent_node.children.remove(item)
                self._remove_connections_for_node(item)
                if hasattr(item, "dispose"): item.dispose()
                self.removeItem(item)
                if item in self.gitlink_nodes: self.gitlink_nodes.remove(item)
                if self.window and self.window.current_node == item:
                    self.window.current_node = None
                    self.window.message_input.setPlaceholderText("Type your message...")
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
                self._remove_connections_for_node(item, [self.chart_connections])
                if hasattr(item, "dispose"):
                    item.dispose()
                self.removeItem(item)
                if item in self.chart_nodes: self.chart_nodes.remove(item)
            elif isinstance(item, NavigationPin):
                self.remove_navigation_pin(item)
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

        static_items = [item for item in self.items() if isinstance(item, (ChatNode, CodeNode, Note, Frame, ChartItem, DocumentNode, ImageNode, ThinkingNode, Container, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, HtmlViewNode, ArtifactNode, GitlinkNode)) and item != moving_item and not item.isSelected()]

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
                                line.setPen(QPen(QColor(128, 128, 128, 200), 1, Qt.PenStyle.DashLine))
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
                                line.setPen(QPen(QColor(128, 128, 128, 200), 1, Qt.PenStyle.DashLine))
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
            grid_size = self.views()[0].grid_settings.grid_size
            if not x_was_snapped:
                snapped_pos.setX(round(new_pos.x() / grid_size) * grid_size)
            if not y_was_snapped:
                snapped_pos.setY(round(new_pos.y() / grid_size) * grid_size)
                
        return snapped_pos

    def _teardown_items_before_clear(self):
        """Stops every tracked item's unparented async timers/animations
        BEFORE super().clear() deletes their C++ objects.

        QGraphicsScene.clear() does NOT call itemChange(ItemSceneHasChanged,
        None) for the items it removes - confirmed empirically against this
        project's PySide6 build: only QGraphicsScene.removeItem() (the
        individual-delete path, e.g. right-click delete) fires that
        notification. clear() deletes each item's C++ side directly. Since
        QGraphicsItem isn't a QObject, nothing else stops a plain QTimer/
        QVariantAnimation created inside one - the itemChange-based teardown
        hooks on these classes are correct and necessary for the
        removeItem() path, but are never invoked for clear(), which is
        exactly the path chat-switching uses (see
        graphlink_session/deserializers.py's restore_chat()). This walk is
        the only way to stop them before clear() destroys the items.
        """
        def _try_teardown(item, method_name):
            method = getattr(item, method_name, None)
            if callable(method):
                try:
                    method()
                except RuntimeError:
                    pass

        connection_lists = (
            self.connections, self.content_connections, self.document_connections,
            self.image_connections, self.thinking_connections, self.system_prompt_connections,
            self.pycoder_connections, self.code_sandbox_connections, self.web_connections,
            self.conversation_connections, self.group_summary_connections, self.html_connections,
            self.artifact_connections, self.gitlink_connections, self.chart_connections,
        )
        for conn_list in connection_lists:
            for conn in conn_list:
                _try_teardown(conn, "_teardown_async_helpers")

        hover_animation_node_lists = (
            self.nodes, self.code_nodes, self.document_nodes, self.image_nodes,
            self.thinking_nodes, self.pycoder_nodes, self.code_sandbox_nodes,
            self.web_nodes, self.conversation_nodes, self.html_view_nodes,
            self.artifact_nodes, self.gitlink_nodes,
        )
        for node_list in hover_animation_node_lists:
            for node in node_list:
                _try_teardown(node, "_stop_hover_animation_timer")

        for node in self.conversation_nodes:
            typing_indicator = getattr(node, "_typing_indicator", None)
            if typing_indicator is not None:
                _try_teardown(typing_indicator, "_teardown_async_helpers")

        for item in list(self.notes) + list(self.containers) + list(self.frames):
            _try_teardown(item, "_teardown_async_helpers")

        for chart in self.chart_nodes:
            _try_teardown(chart, "dispose")  # ChartItem.dispose() is timer/figure-only

        # Bug-scan finding: dispose() (stops the node's background QThread
        # worker, and - for the 4 that wire it via a lambda closing over the
        # node/thread, see each dispose()'s own comment - disconnects the
        # worker's own signals so neither the worker nor the node is pinned
        # alive forever) was only ever invoked from deleteSelectedItems(),
        # never from clear(). New Chat / chat-switching mid-generation left
        # the worker running with nothing to stop it, for every worker-owning
        # node type, not just the one (ArtifactNode) this was first noticed
        # on. GitlinkNode's dispose() already disconnected correctly - it was
        # equally never being called here either.
        worker_owning_node_lists = (
            self.pycoder_nodes, self.code_sandbox_nodes,
            self.conversation_nodes, self.artifact_nodes, self.gitlink_nodes,
        )
        for node_list in worker_owning_node_lists:
            for node in node_list:
                _try_teardown(node, "dispose")

    def clear(self):
        """
        Clears the entire scene, removing all items and resetting all tracking lists.
        """
        self._teardown_items_before_clear()
        super().clear()
        self.nodes.clear()
        self.connections.clear()
        self.frames.clear()
        self.containers.clear()
        self.pins.clear()
        self.pin_store.clear()
        self.notes.clear()
        self.code_nodes.clear()
        self.document_nodes.clear()
        self.image_nodes.clear()
        self.thinking_nodes.clear()
        self.pycoder_nodes.clear()
        self.code_sandbox_nodes.clear()
        self.web_nodes.clear()
        self.conversation_nodes.clear()
        self.html_view_nodes.clear()
        self.artifact_nodes.clear()
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
        self.group_summary_connections.clear()
        self.html_connections.clear()
        self.artifact_connections.clear()
        self.gitlink_connections.clear()
        self.chart_connections.clear()
        
        if hasattr(self, 'window') and self.window:
            self.window.current_node = None
        self.scene_changed.emit()
