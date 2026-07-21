from uuid import uuid4

from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkNode
from graphlink_pycoder import PyCoderNode
from graphlink_web import WebNode
from graphlink_chart_data import ChartDataError, canonicalize_chart_data

from graphlink_session.content_codec import (
    encode_image_bytes,
    process_content_for_serialization,
    serialize_history,
)
from graphlink_session.scene_index import (
    CURRENT_CHAT_SCHEMA_VERSION,
    build_item_index,
    get_all_nodes,
    get_all_serializable_items,
    get_scene_notes,
    get_scene_pins,
)


class SceneSerializer:
    """Convert the live scene into a persisted chat payload."""

    def __init__(self, window):
        self.window = window

    def _scene(self):
        return self.window.chat_view.scene()

    @staticmethod
    def _node_persistent_id(node):
        """Return the node's stable identity for cross-referencing in the payload.

        Graph references used to be serialized purely as list positions
        (all_nodes_list.index(node)), which silently corrupt when a load skips any
        node and later positions shift, silently reattaching links to wrong nodes.
        IDs are assigned lazily, stored on the node object so they stay stable across
        saves within a session, and restored from the payload on load so they stay
        stable across sessions too.
        """
        persistent_id = getattr(node, "persistent_id", None)
        if not persistent_id:
            persistent_id = uuid4().hex
            node.persistent_id = persistent_id
        return persistent_id

    def serialize_pin(self, pin):
        scene = self._scene()
        record = scene.pin_store.get(pin.pin_id) if hasattr(scene, "pin_store") else None
        return {
            "pin_id": getattr(pin, "pin_id", None),
            "title": pin.title,
            "note": pin.note,
            "position": {"x": pin.pos().x(), "y": pin.pos().y()},
            "anchor_item_id": getattr(record, "anchor_item_id", None),
            "sort_order": getattr(record, "sort_order", 0),
            "created_at": getattr(record, "created_at", None),
        }

    def serialize_pin_layout(self, pin):
        return {
            "position": {"x": pin.pos().x(), "y": pin.pos().y()},
        }

    def _serialize_basic_connection(self, connection, all_nodes_list):
        # Indices are still written for backward compatibility (older app versions
        # read only them); IDs are the preferred reference on load - they survive a
        # node being skipped, where positional references silently shift (#47).
        return {
            "start_node_index": all_nodes_list.index(connection.start_node),
            "end_node_index": all_nodes_list.index(connection.end_node),
            "start_node_id": self._node_persistent_id(connection.start_node),
            "end_node_id": self._node_persistent_id(connection.end_node),
        }

    def serialize_connection(self, connection, all_nodes_list):
        payload = self._serialize_basic_connection(connection, all_nodes_list)
        payload["pins"] = [self.serialize_pin_layout(pin) for pin in connection.pins]
        return payload

    def serialize_content_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_document_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_image_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_thinking_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_system_prompt_connection(self, connection, notes_list, nodes_list):
        # Notes are never skipped on load, so the note side stays positional; the
        # node side gets an ID because scene.nodes positions shift if a chat node
        # is skipped (#47).
        return {
            "start_note_index": notes_list.index(connection.start_node),
            "end_node_index": nodes_list.index(connection.end_node),
            "end_node_id": self._node_persistent_id(connection.end_node),
        }

    def serialize_pycoder_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_code_sandbox_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_web_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_conversation_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_html_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_artifact_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_gitlink_connection(self, connection, all_nodes_list):
        return self._serialize_basic_connection(connection, all_nodes_list)

    def serialize_group_summary_connection(self, connection, nodes_list, notes_list):
        return {
            "start_node_index": nodes_list.index(connection.start_node),
            "end_note_index": notes_list.index(connection.end_node),
            "start_node_id": self._node_persistent_id(connection.start_node),
        }

    def serialize_node(self, node, all_nodes_list=None):
        all_nodes_list = all_nodes_list or get_all_nodes(self._scene())

        if isinstance(node, ChatNode):
            return {
                "node_type": "chat",
                "raw_content": process_content_for_serialization(node.raw_content),
                "is_user": node.is_user,
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "conversation_history": serialize_history(node.conversation_history),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
                "scroll_value": node.scroll_value,
                "is_collapsed": node.is_collapsed,
            }
        if isinstance(node, CodeNode):
            return {
                "node_type": "code",
                "code": node.code,
                "language": node.language,
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "parent_content_node_index": all_nodes_list.index(node.parent_content_node),
            }
        if isinstance(node, DocumentNode):
            return {
                "node_type": "document",
                "title": node.title,
                "content": node.content,
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "parent_content_node_index": all_nodes_list.index(node.parent_content_node),
                "attachment_kind": getattr(node, "attachment_kind", "document"),
                "file_path": getattr(node, "file_path", ""),
                "mime_type": getattr(node, "mime_type", ""),
                "duration_seconds": getattr(node, "duration_seconds", None),
                "byte_size": getattr(node, "byte_size", None),
                "preview_label": getattr(node, "preview_label", ""),
                "is_collapsed": getattr(node, "is_collapsed", False),
                "is_docked": getattr(node, "is_docked", False),
            }
        if isinstance(node, ImageNode):
            return {
                "node_type": "image",
                "image_bytes": encode_image_bytes(node.image_bytes),
                "prompt": node.prompt,
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "parent_content_node_index": all_nodes_list.index(node.parent_content_node),
            }
        if isinstance(node, ThinkingNode):
            return {
                "node_type": "thinking",
                "thinking_text": node.thinking_text,
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "parent_content_node_index": all_nodes_list.index(node.parent_content_node),
                "is_docked": node.is_docked,
            }
        if isinstance(node, PyCoderNode):
            return {
                "node_type": "pycoder",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "mode": node.mode.name,
                "prompt": node.get_prompt(),
                "code": node.get_code(),
                "output": node.output_display.toPlainText(),
                "analysis": node.ai_analysis_display.toPlainText(),
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "include_branch_context": getattr(node, "include_branch_context", True),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        if isinstance(node, CodeSandboxNode):
            return {
                "node_type": "code_sandbox",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "prompt": node.get_prompt(),
                "requirements": node.get_requirements(),
                "code": node.get_code(),
                "output": node.output_display.toPlainText(),
                "analysis": node.ai_analysis_display.toPlainText(),
                "status": node.status,
                "sandbox_id": node.sandbox_id,
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "include_branch_context": getattr(node, "include_branch_context", True),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        if isinstance(node, WebNode):
            return {
                "node_type": "web",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "query": node.query,
                "status": node.status,
                "summary": node.summary,
                "sources": [source.to_dict() if callable(getattr(source, "to_dict", None)) else source for source in node.sources],
                "research_result": dict(getattr(node, "research_result_payload", {}) or {}),
                "warnings": list(getattr(node, "warnings", []) or []),
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "include_branch_context": getattr(node, "include_branch_context", True),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        if isinstance(node, ConversationNode):
            return {
                "node_type": "conversation",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        if isinstance(node, HtmlViewNode):
            return {
                "node_type": "html",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                # Phase 7 prerequisite (increment 1): read the raw-source model
                # attribute (get_html_content() -> self.html_content), NOT the
                # widget's toHtml(). toHtml() on this setAcceptRichText(False)
                # editor returns a full Qt rich-text DOCUMENT wrapper with the
                # user's markup HTML-ESCAPED (<h1> -> &lt;h1&gt;); on reload
                # set_html_content(setPlainText) then rendered that wrapper
                # instead of the user's HTML, corrupting the node on the first
                # save/reload cycle (verified empirically). Reading the model
                # mirror both fixes that round-trip bug and clears one of the
                # 14 widget-reads the Phase 7 gate exists to eliminate.
                "html_content": node.get_html_content(),
                "splitter_state": node.get_splitter_state(),
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        if isinstance(node, ArtifactNode):
            return {
                "node_type": "artifact",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "instruction": node.get_instruction(),
                "content": node.get_artifact_content(),
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "local_history": serialize_history(getattr(node, "local_history", [])),
                "chat_html_cache": node.chat_html_cache,
                "include_branch_context": getattr(node, "include_branch_context", True),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        if isinstance(node, GitlinkNode):
            return {
                "node_type": "gitlink",
                "position": {"x": node.pos().x(), "y": node.pos().y()},
                "task_prompt": node.get_task_prompt(),
                "repo_state": dict(getattr(node, "repo_state", {}) or {}),
                "repo_file_paths": list(getattr(node, "repo_file_paths", []) or []),
                "selected_paths": list(getattr(node, "selected_paths", []) or []),
                "context_xml": getattr(node, "context_xml", ""),
                "context_stats": dict(getattr(node, "context_stats", {}) or {}),
                "proposal_data": dict(getattr(node, "proposal_data", {}) or {}),
                "preview_text": getattr(node, "preview_text", ""),
                "conversation_history": serialize_history(getattr(node, "conversation_history", [])),
                "is_collapsed": node.is_collapsed,
                "parent_node_index": all_nodes_list.index(node.parent_node),
                "children_indices": [all_nodes_list.index(child) for child in node.children],
            }
        return None

    def _serialize_node_with_identity(self, node, all_nodes_list):
        """serialize_node() plus the stable-ID fields (#47).

        Stamped here, outside the per-type isinstance chain, so every node type gets
        identity fields without 12 parallel edits: `id` on every payload, and
        `children_ids` alongside any `children_indices` (positions still written for
        older readers; IDs preferred by the deserializer).
        """
        payload = self.serialize_node(node, all_nodes_list)
        if payload is None:
            return payload

        payload["id"] = self._node_persistent_id(node)
        if "children_indices" in payload:
            payload["children_ids"] = [
                self._node_persistent_id(child) for child in node.children
            ]
        return payload

    def serialize_frame(self, frame, frame_items_map):
        return {
            "id": self._node_persistent_id(frame),
            "items": [frame_items_map[item] for item in frame.nodes if item in frame_items_map],
            "item_ids": [self._node_persistent_id(item) for item in frame.nodes],
            "position": {"x": frame.pos().x(), "y": frame.pos().y()},
            "note": frame.note,
            "size": {
                "width": frame.rect.width(),
                "height": frame.rect.height(),
            },
            "rect": {"x": frame.rect.x(), "y": frame.rect.y(), "width": frame.rect.width(), "height": frame.rect.height()},
            "expanded_rect": {"x": frame.expanded_rect.x(), "y": frame.expanded_rect.y(), "width": frame.expanded_rect.width(), "height": frame.expanded_rect.height()},
            "is_locked": frame.is_locked,
            "is_collapsed": frame.is_collapsed,
            "color": frame.color,
            "header_color": frame.header_color,
        }

    def serialize_container(self, container, all_items_map):
        return {
            "id": self._node_persistent_id(container),
            "items": [all_items_map[item] for item in container.contained_items],
            "item_ids": [self._node_persistent_id(item) for item in container.contained_items],
            "position": {"x": container.pos().x(), "y": container.pos().y()},
            "title": container.title,
            "is_collapsed": container.is_collapsed,
            "color": container.color,
            "header_color": container.header_color,
            "expanded_rect": {
                "x": container.expanded_rect.x(),
                "y": container.expanded_rect.y(),
                "width": container.expanded_rect.width(),
                "height": container.expanded_rect.height(),
            },
            "rect": {"x": container.rect.x(), "y": container.rect.y(), "width": container.rect.width(), "height": container.rect.height()},
        }

    def serialize_note(self, note):
        return {
            "id": self._node_persistent_id(note),
            "content": note.content,
            "position": {"x": note.pos().x(), "y": note.pos().y()},
            "size": {"width": note.width, "height": note.height},
            "color": note.color,
            "header_color": note.header_color,
            "is_system_prompt": getattr(note, "is_system_prompt", False),
            "is_summary_note": getattr(note, "is_summary_note", False),
            "role": getattr(note, "note_role", "manual"),
            "source_ids": list(getattr(note, "source_ids", [])),
            "operation_id": getattr(note, "operation_id", ""),
            "source_revisions": dict(getattr(note, "source_revisions", {})),
            "provider_snapshot": dict(getattr(note, "provider_snapshot", {})),
        }

    def serialize_chart(self, chart, all_nodes_list):
        parent_node = getattr(chart, "parent_content_node", None)
        parent_node_index = all_nodes_list.index(parent_node) if parent_node in all_nodes_list else None
        try:
            chart_data = canonicalize_chart_data(chart.data)
            data_error = None
        except (ChartDataError, TypeError, ValueError) as exc:
            chart_data = dict(getattr(chart, "data", {}) or {})
            data_error = str(exc)
        source_node = getattr(chart, "source_node", None)
        return {
            "id": self._node_persistent_id(chart),
            "data": chart_data,
            "position": {"x": chart.pos().x(), "y": chart.pos().y()},
            "size": {"width": chart.width, "height": chart.height},
            "aspect_ratio_locked": getattr(chart, "aspect_ratio_locked", True),
            "parent_node_index": parent_node_index,
            "parent_node_id": self._node_persistent_id(parent_node) if parent_node is not None else None,
            "source_node_id": self._node_persistent_id(source_node) if source_node is not None else None,
            "data_error": data_error,
        }

    def serialize_chat_data(self):
        scene = self._scene()
        notes = get_scene_notes(scene)
        pins = get_scene_pins(scene)
        charts = list(scene.chart_nodes)
        all_nodes_list = get_all_nodes(scene)
        all_items = get_all_serializable_items(scene, all_nodes_list, notes, charts)
        all_items_map = build_item_index(all_items)
        frame_items_map = build_item_index(all_nodes_list + charts)

        connection_groups = (
            ("connections", scene.connections, self.serialize_connection),
            ("content_connections", scene.content_connections, self.serialize_content_connection),
            ("document_connections", scene.document_connections, self.serialize_document_connection),
            ("image_connections", scene.image_connections, self.serialize_image_connection),
            ("thinking_connections", scene.thinking_connections, self.serialize_thinking_connection),
            ("pycoder_connections", scene.pycoder_connections, self.serialize_pycoder_connection),
            ("code_sandbox_connections", scene.code_sandbox_connections, self.serialize_code_sandbox_connection),
            ("web_connections", scene.web_connections, self.serialize_web_connection),
            ("conversation_connections", scene.conversation_connections, self.serialize_conversation_connection),
            ("html_connections", scene.html_connections, self.serialize_html_connection),
            ("artifact_connections", scene.artifact_connections, self.serialize_artifact_connection),
            ("gitlink_connections", scene.gitlink_connections, self.serialize_gitlink_connection),
        )

        chat_data = {
            "schema_version": CURRENT_CHAT_SCHEMA_VERSION,
            "nodes": [self._serialize_node_with_identity(node, all_nodes_list) for node in all_nodes_list],
            "system_prompt_connections": [
                self.serialize_system_prompt_connection(connection, notes, scene.nodes)
                for connection in scene.system_prompt_connections
            ],
            "group_summary_connections": [
                self.serialize_group_summary_connection(connection, scene.nodes, notes)
                for connection in scene.group_summary_connections
            ],
            "frames": [self.serialize_frame(frame, frame_items_map) for frame in scene.frames],
            "containers": [self.serialize_container(container, all_items_map) for container in scene.containers],
            "charts": [self.serialize_chart(chart, all_nodes_list) for chart in charts],
            "total_session_tokens": self.window.total_session_tokens,
            "view_state": {
                "zoom_factor": self.window.chat_view._zoom_factor,
                "scroll_position": {
                    "x": self.window.chat_view.horizontalScrollBar().value(),
                    "y": self.window.chat_view.verticalScrollBar().value(),
                },
            },
            "notes_data": [self.serialize_note(note) for note in notes],
            "pins_data": [self.serialize_pin(pin) for pin in pins],
        }

        for payload_name, connections, serializer in connection_groups:
            chat_data[payload_name] = [serializer(connection, all_nodes_list) for connection in connections]

        return chat_data
