import traceback

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform

from graphlink_canvas_items import Container, Frame
from graphlink_connections import (
    ConnectionItem,
    ContentConnectionItem,
    ConversationConnectionItem,
    DocumentConnectionItem,
    GroupSummaryConnectionItem,
    HtmlConnectionItem,
    ImageConnectionItem,
    PyCoderConnectionItem,
    SystemPromptConnectionItem,
    ThinkingConnectionItem,
)
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactConnectionItem, ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxConnectionItem, CodeSandboxNode
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkConnectionItem, GitlinkNode
from graphlink_pycoder import PyCoderMode, PyCoderNode
from graphlink_web import WebConnectionItem, WebNode
from graphlink_navigation_pins import NavigationPinRecord, NavigationPinValidationError

from graphlink_session.content_codec import (
    decode_image_bytes,
    deserialize_history,
    process_content_for_deserialization,
)
from graphlink_session.scene_index import CHILD_LINK_NODE_TYPES


class SceneDeserializer:
    """Rebuild a saved chat payload into the live scene."""

    def __init__(self, window):
        self.window = window

    def _scene(self):
        return self.window.chat_view.scene()

    def _connect_if_available(self, signal, window_handler_name):
        if self.window and hasattr(self.window, window_handler_name):
            signal.connect(getattr(self.window, window_handler_name))

    def _set_incoming_connection(self, end_node, connection):
        if hasattr(end_node, "incoming_connection"):
            end_node.incoming_connection = connection

    def _resolve_node_ref(self, data, id_key, index_key, all_nodes_map):
        """Resolve a serialized node reference, preferring the stable ID (#47).

        IDs survive a node being skipped during load; positional indices are the
        legacy fallback for payloads saved before IDs existed. Both resolutions can
        legitimately return None (the referenced node itself failed to load) - the
        caller drops the reference, which is safe; what IDs prevent is a reference
        silently resolving to the *wrong* node.
        """
        nodes_by_id = getattr(self, "_nodes_by_id", None) or {}
        node_id = data.get(id_key)
        if node_id and node_id in nodes_by_id:
            return nodes_by_id[node_id]
        return all_nodes_map.get(data.get(index_key))

    def _resolve_chart_ref(self, data, id_key, index_key, all_nodes_map):
        node_id = data.get(id_key)
        if node_id:
            for mapping in (
                getattr(self, "_nodes_by_id", None) or {},
                getattr(self, "_charts_by_id", None) or {},
            ):
                if node_id in mapping:
                    return mapping[node_id]
        return all_nodes_map.get(data.get(index_key))

    def _deserialize_basic_connection(self, data, scene, all_nodes_map, connection_cls, target_list_name):
        start_node = self._resolve_node_ref(data, "start_node_id", "start_node_index", all_nodes_map)
        end_node = self._resolve_node_ref(data, "end_node_id", "end_node_index", all_nodes_map)
        if not start_node or not end_node:
            return None
        connection = connection_cls(start_node, end_node)
        self._set_incoming_connection(end_node, connection)
        scene.addItem(connection)
        getattr(scene, target_list_name).append(connection)
        scene.register_connection(connection)
        return connection

    def deserialize_chart(self, data, scene, all_nodes_map):
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise ValueError("Chart record is missing a data object")
        parent_node = self._resolve_chart_ref(data, "parent_node_id", "parent_node_index", all_nodes_map)
        source_node = self._resolve_chart_ref(data, "source_node_id", "parent_node_index", all_nodes_map)
        chart = scene.add_chart(
            data["data"],
            QPointF(data["position"]["x"], data["position"]["y"]),
            parent_content_node=parent_node,
            source_node=source_node,
        )
        chart.persistent_id = data.get("id") or chart.persistent_id
        chart.aspect_ratio_locked = bool(data.get("aspect_ratio_locked", True))

        if "size" in data:
            chart.set_chart_size(
                data["size"]["width"],
                data["size"]["height"],
                preserve_aspect=chart.aspect_ratio_locked,
                rerender=False,
            )
        chart.generate_chart()
        return chart

    def deserialize_pin(self, data, connection):
        pin = connection.add_pin(QPointF(0, 0))
        pin.setPos(data["position"]["x"], data["position"]["y"])
        return pin

    def deserialize_connection(self, data, scene, all_nodes_map):
        connection = self._deserialize_basic_connection(data, scene, all_nodes_map, ConnectionItem, "connections")
        if connection is None:
            return None
        for pin_data in data.get("pins", []):
            self.deserialize_pin(pin_data, connection)
        return connection

    def deserialize_content_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ContentConnectionItem, "content_connections"
        )

    def deserialize_document_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, DocumentConnectionItem, "document_connections"
        )

    def deserialize_image_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ImageConnectionItem, "image_connections"
        )

    def deserialize_thinking_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ThinkingConnectionItem, "thinking_connections"
        )

    def deserialize_system_prompt_connection(self, data, scene, notes_map, nodes_map):
        start_note = notes_map.get(data["start_note_index"])
        end_node = self._resolve_node_ref(data, "end_node_id", "end_node_index", nodes_map)
        if not start_note or not end_node:
            print("Warning: Skipping orphaned system prompt connection during load.")
            return None

        connection = SystemPromptConnectionItem(start_note, end_node)
        scene.addItem(connection)
        scene.system_prompt_connections.append(connection)
        scene.register_connection(connection)
        return connection

    def deserialize_pycoder_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, PyCoderConnectionItem, "pycoder_connections"
        )

    def deserialize_code_sandbox_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, CodeSandboxConnectionItem, "code_sandbox_connections"
        )

    def deserialize_web_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, WebConnectionItem, "web_connections"
        )

    def deserialize_conversation_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ConversationConnectionItem, "conversation_connections"
        )

    def deserialize_html_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, HtmlConnectionItem, "html_connections"
        )

    def deserialize_artifact_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ArtifactConnectionItem, "artifact_connections"
        )

    def deserialize_gitlink_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, GitlinkConnectionItem, "gitlink_connections"
        )

    def deserialize_group_summary_connection(self, data, scene, nodes_map, notes_map):
        start_node = self._resolve_node_ref(data, "start_node_id", "start_node_index", nodes_map)
        end_note = notes_map.get(data["end_note_index"])
        if not start_node or not end_note:
            print("Warning: Skipping orphaned group summary connection.")
            return None

        connection = GroupSummaryConnectionItem(start_node, end_note)
        scene.addItem(connection)
        scene.group_summary_connections.append(connection)
        scene.register_connection(connection)
        return connection

    def deserialize_node(self, index, data, all_nodes_map):
        if not isinstance(data, dict):
            return None

        scene = self._scene()
        node_type = data.get("node_type", "chat")
        node = None

        if node_type == "chat":
            raw_content = process_content_for_deserialization(data.get("raw_content", data.get("text")))
            node = scene.add_chat_node(
                raw_content,
                is_user=data.get("is_user", True),
                parent_node=None,
                conversation_history=deserialize_history(data.get("conversation_history", [])),
            )
            node.setPos(data["position"]["x"], data["position"]["y"])
            node.scroll_value = data.get("scroll_value", 0)
            node.scrollbar.set_value(node.scroll_value)
            if data.get("is_collapsed", False):
                node.set_collapsed(True)

        elif node_type == "code":
            parent_node = all_nodes_map.get(data["parent_content_node_index"])
            if parent_node:
                node = scene.add_code_node(data["code"], data["language"], parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])

        elif node_type == "document":
            parent_node = all_nodes_map.get(data["parent_content_node_index"])
            if parent_node:
                node = scene.add_document_node(
                    data["title"],
                    data["content"],
                    parent_node,
                    attachment_kind=data.get("attachment_kind", "document"),
                    file_path=data.get("file_path", ""),
                    mime_type=data.get("mime_type", ""),
                    duration_seconds=data.get("duration_seconds"),
                    byte_size=data.get("byte_size"),
                    preview_label=data.get("preview_label"),
                )
                node.setPos(data["position"]["x"], data["position"]["y"])
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                if data.get("is_docked", False):
                    node.dock()

        elif node_type == "image":
            parent_node = all_nodes_map.get(data["parent_content_node_index"])
            if parent_node:
                node = scene.add_image_node(
                    decode_image_bytes(data["image_bytes"]),
                    parent_node,
                    prompt=data.get("prompt", ""),
                )
                node.setPos(data["position"]["x"], data["position"]["y"])

        elif node_type == "thinking":
            parent_node = all_nodes_map.get(data["parent_content_node_index"])
            if parent_node:
                node = scene.add_thinking_node(data["thinking_text"], parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                if data.get("is_docked", False):
                    node.dock()

        elif node_type == "pycoder":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = PyCoderNode(parent_node, mode=PyCoderMode[data.get("mode", "AI_DRIVEN")])
                node.setPos(data["position"]["x"], data["position"]["y"])
                # setPlainText, not setText: QTextEdit.setText() auto-detects
                # "might be rich text" and silently strips angle-bracket
                # substrings it mistakes for HTML tags (e.g. a prompt containing
                # "<script>" or "<div>") before textChanged ever populates the
                # prompt mirror - found by adversarial review as a real,
                # pre-existing hole in this increment's round-trip guarantee.
                # CodeSandbox/Artifact's restore paths already use setPlainText.
                node.prompt_input.setPlainText(data.get("prompt", ""))
                node.set_code(data.get("code", ""))
                node.set_output(data.get("output", ""))
                node.set_ai_analysis(data.get("analysis", ""))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                # Phase 7 prerequisite (increment 3): wire the run-request
                # signal on restore, matching every other node type's own
                # _connect_if_available call in this function (pycoder was the
                # one branch connecting nothing).
                self._connect_if_available(node.run_clicked, "execute_pycoder_node")
                scene.addItem(node)
                scene.pycoder_nodes.append(node)

        elif node_type == "code_sandbox":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = CodeSandboxNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.prompt_input.setPlainText(data.get("prompt", ""))
                node.set_requirements(data.get("requirements", ""))
                node.set_code(data.get("code", ""))
                node.set_output(data.get("output", ""))
                node.set_ai_analysis(data.get("analysis", ""))
                node.status = data.get("status", "Idle")
                node.sandbox_id = data.get("sandbox_id", node.sandbox_id)
                tone = "success" if node.status == "Ready" else ("error" if node.status == "Error" else "info")
                node._update_status_pill(tone)
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.sandbox_requested, "execute_code_sandbox_node")
                scene.addItem(node)
                scene.code_sandbox_nodes.append(node)

        elif node_type == "web":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = WebNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.query_input.setText(data.get("query", ""))
                node.set_status(data.get("status", "Idle"))
                summary = data.get("summary", "")
                sources = data.get("sources", [])
                if data.get("research_result"):
                    node.restore_research_result(data["research_result"])
                elif summary:
                    node.set_result(summary, sources)
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                self._connect_if_available(node.run_clicked, "execute_web_node")
                self._connect_if_available(node.cancel_requested, "cancel_web_node")
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                scene.addItem(node)
                scene.web_nodes.append(node)

        elif node_type == "conversation":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = ConversationNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.set_history(data.get("conversation_history", []))
                self._connect_if_available(node.ai_request_sent, "handle_conversation_node_request")
                self._connect_if_available(node.cancel_requested, "handle_conversation_node_cancel")
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                scene.addItem(node)
                scene.conversation_nodes.append(node)

        elif node_type == "html":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = HtmlViewNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.set_html_content(data.get("html_content", ""))
                node.set_splitter_state(data.get("splitter_state"))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                # Phase 7 prerequisite (increment 1): wire the render-request
                # signal on restore, matching every other node type's own
                # _connect_if_available call in this function (html was the one
                # branch that connected nothing).
                self._connect_if_available(node.render_requested, "execute_html_view_node")
                scene.addItem(node)
                scene.html_view_nodes.append(node)

        elif node_type == "artifact":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = ArtifactNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.instruction_input.setPlainText(data.get("instruction", ""))
                node.set_artifact_content(data.get("content", ""))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                node.local_history = deserialize_history(data.get("local_history", []))
                node.chat_html_cache = data.get("chat_html_cache", "")
                node.chat_display.setHtml(node.chat_html_cache)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.artifact_requested, "execute_artifact_node")
                self._connect_if_available(node.stop_requested, "stop_artifact_node")
                scene.addItem(node)
                scene.artifact_nodes.append(node)

        elif node_type == "gitlink":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = GitlinkNode(parent_node, settings_manager=getattr(self.window, "settings_manager", None))
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.restore_saved_state(
                    repo_state=data.get("repo_state", {}),
                    repo_file_paths=data.get("repo_file_paths", []),
                    selected_paths=data.get("selected_paths", []),
                    task_prompt=data.get("task_prompt", ""),
                    context_xml=data.get("context_xml", ""),
                    context_stats=data.get("context_stats", {}),
                    proposal_data=data.get("proposal_data", {}),
                    preview_text=data.get("preview_text", ""),
                )
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.gitlink_requested, "execute_gitlink_node")
                scene.addItem(node)
                scene.gitlink_nodes.append(node)

        if node:
            all_nodes_map[index] = node
        return node

    def deserialize_frame(self, data, scene, all_nodes_map):
        frame_item_indices = data.get("items", data.get("nodes", []))
        nodes = [all_nodes_map[index] for index in frame_item_indices if index in all_nodes_map]

        frame = Frame(nodes)
        if data.get("id"):
            frame.persistent_id = data["id"]
        frame.setPos(data["position"]["x"], data["position"]["y"])
        frame.note = data["note"]

        if "color" in data:
            frame.color = data["color"]
        if "header_color" in data:
            frame.header_color = data["header_color"]
        if "size" in data:
            frame.rect.setWidth(data["size"]["width"])
            frame.rect.setHeight(data["size"]["height"])
        rect_data = data.get("rect")
        if rect_data:
            frame.rect = QRectF(rect_data["x"], rect_data["y"], rect_data["width"], rect_data["height"])
            frame._user_resized = True
        expanded_data = data.get("expanded_rect")
        if expanded_data:
            frame.expanded_rect = QRectF(expanded_data["x"], expanded_data["y"], expanded_data["width"], expanded_data["height"])

        scene.addItem(frame)
        scene.frames.append(frame)
        frame.setZValue(-2)
        frame.is_locked = data.get("is_locked", True)
        frame.is_collapsed = data.get("is_collapsed", False)
        frame._apply_lock_state()
        for node in frame.nodes:
            node.setVisible(not frame.is_collapsed)
        if frame.is_collapsed:
            frame.rect = QRectF(0, 0, frame.COLLAPSED_WIDTH, frame.COLLAPSED_HEIGHT)
        frame._update_title_editor_geometry()
        return frame

    def deserialize_container(self, data, scene, all_items_map):
        items = [all_items_map[index] for index in data["items"] if index in all_items_map]
        container = Container(items)
        if data.get("id"):
            container.persistent_id = data["id"]
        container.setPos(data["position"]["x"], data["position"]["y"])
        container.title = data.get("title", "Container")
        container.color = data.get("color", "#3a3a3a")
        container.header_color = data.get("header_color")

        rect_data = data.get("expanded_rect")
        if rect_data:
            container.expanded_rect = QRectF(
                rect_data["x"], rect_data["y"], rect_data["width"], rect_data["height"]
            )
        rect_data = data.get("rect")
        if rect_data:
            container.rect = QRectF(rect_data["x"], rect_data["y"], rect_data["width"], rect_data["height"])

        container.is_collapsed = data.get("is_collapsed", False)
        for item in container.contained_items:
            item.setVisible(not container.is_collapsed)
        if container.is_collapsed:
            container.rect = QRectF(0, 0, container.COLLAPSED_WIDTH, container.COLLAPSED_HEIGHT)
        container._update_title_editor_geometry()

        scene.addItem(container)
        scene.containers.append(container)
        container.setZValue(-3)
        return container

    def _restore_children(self, node_payloads, all_nodes_map):
        nodes_by_id = getattr(self, "_nodes_by_id", None) or {}
        for index, node_data in enumerate(node_payloads):
            if not isinstance(node_data, dict):
                continue
            node = all_nodes_map.get(index)
            if not node:
                continue
            if not isinstance(node, CHILD_LINK_NODE_TYPES):
                continue
            if "children_indices" not in node_data and "children_ids" not in node_data:
                continue

            # Prefer stable IDs (#47), falling back per-position to the legacy index
            # so pre-ID payloads (and any position whose ID didn't resolve) keep
            # working. Position i in children_ids corresponds to position i in
            # children_indices - both are serialized from the same node.children list.
            child_ids = node_data.get("children_ids") or []
            child_indices = node_data.get("children_indices") or []
            for position in range(max(len(child_ids), len(child_indices))):
                child_node = None
                if position < len(child_ids):
                    child_node = nodes_by_id.get(child_ids[position])
                if child_node is None and position < len(child_indices):
                    child_node = all_nodes_map.get(child_indices[position])
                if child_node:
                    node.children.append(child_node)
                    child_node.parent_node = node

    def _load_notes(self, scene, notes_data):
        notes_map = {}
        for index, note_data in enumerate(notes_data):
            note = scene.add_note(QPointF(note_data["position"]["x"], note_data["position"]["y"]))
            note.width = note_data["size"]["width"]
            note.color = note_data["color"]
            note.header_color = note_data["header_color"]
            note.is_system_prompt = note_data.get("is_system_prompt", False)
            note.is_summary_note = note_data.get("is_summary_note", False)
            if note_data.get("id"):
                note.persistent_id = note_data["id"]
            note.note_role = note_data.get("role", "manual")
            note.source_ids = list(note_data.get("source_ids", []))
            note.operation_id = note_data.get("operation_id", "")
            note.source_revisions = dict(note_data.get("source_revisions", {}))
            note.provider_snapshot = dict(note_data.get("provider_snapshot", {}))
            note.content = note_data["content"]
            notes_map[index] = note
        return notes_map

    def _load_pins(self, scene, pins_data):
        if self.window and hasattr(self.window, "pin_overlay"):
            self.window.pin_overlay.clear_pins()

        valid_records = []
        for index, pin_data in enumerate(pins_data or []):
            try:
                valid_records.append(NavigationPinRecord.from_mapping(pin_data, fallback_order=index))
            except NavigationPinValidationError as error:
                self._set_status_message(f"Skipped invalid navigation pin {index + 1}: {error}", "warning")

        for record in sorted(valid_records, key=lambda item: item.sort_order):
            try:
                pin = scene.add_navigation_pin(
                    QPointF(record.position[0], record.position[1]),
                    title=record.title,
                    note=record.note,
                    pin_id=record.pin_id,
                    anchor_item_id=record.anchor_item_id,
                )
            except NavigationPinValidationError as error:
                self._set_status_message(
                    f"Skipped duplicate navigation pin {record.title!r}: {error}",
                    "warning",
                )
                continue
            if self.window and hasattr(self.window, "pin_overlay"):
                self.window.pin_overlay.add_pin_button(pin)

    def _restore_view_state(self, chat_data):
        view_state = chat_data.get("view_state")
        if not view_state:
            return

        self.window.chat_view._zoom_factor = view_state["zoom_factor"]
        self.window.chat_view.setTransform(
            QTransform().scale(view_state["zoom_factor"], view_state["zoom_factor"])
        )
        self.window.chat_view.horizontalScrollBar().setValue(view_state["scroll_position"]["x"])
        self.window.chat_view.verticalScrollBar().setValue(view_state["scroll_position"]["y"])

    def _set_status_message(self, message, tone="warning"):
        if self.window and hasattr(self.window, "notification_banner"):
            self.window.notification_banner.show_message(message, 6000, tone)
        else:
            print(message)

    def _handle_load_error(self, scene, error):
        print(f"Error loading chat: {str(error)}")
        traceback.print_exc()
        if self.window and hasattr(self.window, "notification_banner"):
            self.window.notification_banner.show_message(
                f"Failed to load the chat session. It may be corrupted.\nError: {error}",
                8000,
                "error",
            )

        scene.clear()
        if self.window:
            self.window.current_node = None
            self.window.message_input.setPlaceholderText("Type your message...")
            self.window.update_title_bar()
            self.window.reset_token_counter()
            if hasattr(self.window, "pin_overlay") and self.window.pin_overlay:
                self.window.pin_overlay.clear_pins()

    def restore_chat(self, chat, notes_data, pins_data):
        scene = self._scene()
        scene.clear()
        self.window.current_node = None
        # payload id -> node, for ID-preferred reference resolution (#47). Reset per
        # restore so a previous chat's ids can never bleed into this one.
        self._nodes_by_id = {}

        try:
            chat_data = chat.get("data")
            if not isinstance(chat_data, dict):
                self._set_status_message("This chat record is missing scene data. Starting with an empty scene.", "warning")
                chat_data = {}
            all_nodes_map = {}
            node_payloads = chat_data.get("nodes")
            if node_payloads is None:
                legacy_nodes = chat_data.get("items")
                if isinstance(legacy_nodes, list):
                    node_payloads = legacy_nodes
            elif not isinstance(node_payloads, list):
                node_payloads = None

            if node_payloads is None:
                nested_payload = chat_data.get("data", {})
                if isinstance(nested_payload, dict):
                    nested_nodes = nested_payload.get("nodes") if isinstance(nested_payload.get("nodes"), list) else None
                    nested_items = nested_payload.get("items") if isinstance(nested_payload.get("items"), list) else None
                    if isinstance(nested_nodes, list):
                        node_payloads = nested_nodes
                    elif isinstance(nested_items, list):
                        node_payloads = nested_items

            if not isinstance(node_payloads, list):
                self._set_status_message(
                    "No node list was found in the chat payload. Loaded as an empty chat."
                )
                node_payloads = []

            # chat_nodes_map keys must be the node's position among chat-type
            # *payloads* (the save-side scene.nodes order), not its position in the
            # loaded scene.nodes - those diverge whenever a chat node is skipped,
            # which used to attach system-prompt/group-summary connections to the
            # wrong chat node (#47).
            chat_nodes_map = {}
            chat_payload_position = 0
            for index, node_data in enumerate(node_payloads):
                node = self.deserialize_node(index, node_data, all_nodes_map)
                if not isinstance(node_data, dict):
                    continue
                payload_id = node_data.get("id")
                if node is not None and payload_id:
                    # Restore the stable identity so re-saving this chat keeps the
                    # same ids instead of minting new ones every load/save cycle.
                    node.persistent_id = payload_id
                    self._nodes_by_id[payload_id] = node
                if node_data.get("node_type", "chat") == "chat":
                    if node is not None:
                        chat_nodes_map[chat_payload_position] = node
                    chat_payload_position += 1

            self._restore_children(node_payloads, all_nodes_map)

            notes_map = self._load_notes(scene, notes_data)

            charts_map = {}
            self._charts_by_id = {}
            for index, chart_data in enumerate(chat_data.get("charts", [])):
                try:
                    chart = self.deserialize_chart(chart_data, scene, all_nodes_map)
                except Exception as exc:
                    self._set_status_message(f"Skipped invalid chart {index + 1}: {exc}", "warning")
                    continue
                charts_map[index] = chart
                if isinstance(chart_data, dict) and chart_data.get("id"):
                    self._charts_by_id[chart_data["id"]] = chart

            # Frame/container item references are positions in the SAVE-side item
            # space (all payload nodes, then notes, then charts, then frames - see
            # scene_index.get_all_serializable_items). Offsets must therefore come
            # from the original payload counts, not from how many nodes survived the
            # load: deriving them from the survivor count shifted every later slot
            # whenever any node was skipped, silently attaching frames/containers to
            # the wrong items (#47).
            node_slot_count = len(node_payloads)
            note_slot_count = len(notes_data)
            chart_slot_count = len(chat_data.get("charts", []))

            frame_source_map = dict(all_nodes_map)
            for index, chart in charts_map.items():
                frame_source_map[node_slot_count + index] = chart

            frames_map = {}
            for index, frame_data in enumerate(chat_data.get("frames", [])):
                frames_map[index] = self.deserialize_frame(frame_data, scene, frame_source_map)

            all_items_map = dict(all_nodes_map)
            for index, note in notes_map.items():
                all_items_map[node_slot_count + index] = note
            for index, chart in charts_map.items():
                all_items_map[node_slot_count + note_slot_count + index] = chart
            for index, frame in frames_map.items():
                all_items_map[node_slot_count + note_slot_count + chart_slot_count + index] = frame

            for container_data in chat_data.get("containers", []):
                self.deserialize_container(container_data, scene, all_items_map)

            connection_groups = (
                ("connections", self.deserialize_connection),
                ("content_connections", self.deserialize_content_connection),
                ("document_connections", self.deserialize_document_connection),
                ("image_connections", self.deserialize_image_connection),
                ("thinking_connections", self.deserialize_thinking_connection),
                ("pycoder_connections", self.deserialize_pycoder_connection),
                ("code_sandbox_connections", self.deserialize_code_sandbox_connection),
                ("web_connections", self.deserialize_web_connection),
                ("conversation_connections", self.deserialize_conversation_connection),
                ("html_connections", self.deserialize_html_connection),
                ("artifact_connections", self.deserialize_artifact_connection),
                ("gitlink_connections", self.deserialize_gitlink_connection),
            )

            for payload_name, loader in connection_groups:
                for connection_data in chat_data.get(payload_name, []):
                    loader(connection_data, scene, all_nodes_map)

            for connection_data in chat_data.get("system_prompt_connections", []):
                self.deserialize_system_prompt_connection(connection_data, scene, notes_map, chat_nodes_map)

            for connection_data in chat_data.get("group_summary_connections", []):
                self.deserialize_group_summary_connection(connection_data, scene, chat_nodes_map, notes_map)

            self._load_pins(scene, pins_data)
            self._restore_view_state(chat_data)

            scene.update_connections()
            if self.window:
                total_tokens = chat_data.get("total_session_tokens", 0)
                self.window.reset_token_counter(total_tokens=total_tokens)
            return True
        except Exception as error:
            self._handle_load_error(scene, error)
            return False
