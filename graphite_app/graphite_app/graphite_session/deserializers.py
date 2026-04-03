import traceback

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform

from graphite_canvas_items import Container, Frame
from graphite_connections import (
    ConnectionItem,
    ContentConnectionItem,
    ConversationConnectionItem,
    DocumentConnectionItem,
    GroupSummaryConnectionItem,
    HtmlConnectionItem,
    ImageConnectionItem,
    PyCoderConnectionItem,
    ReasoningConnectionItem,
    SystemPromptConnectionItem,
    ThinkingConnectionItem,
)
from graphite_conversation_node import ConversationNode
from graphite_html_view import HtmlViewNode
from graphite_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphite_plugin_artifact import ArtifactConnectionItem, ArtifactNode
from graphite_plugin_code_review import CodeReviewConnectionItem, CodeReviewNode
from graphite_plugin_code_sandbox import CodeSandboxConnectionItem, CodeSandboxNode
from graphite_plugin_gitlink import GitlinkConnectionItem, GitlinkNode
from graphite_plugin_graph_diff import GraphDiffConnectionItem, GraphDiffNode
from graphite_plugin_quality_gate import QualityGateConnectionItem, QualityGateNode
from graphite_plugin_workflow import WorkflowConnectionItem, WorkflowNode
from graphite_pycoder import PyCoderMode, PyCoderNode
from graphite_reasoning import ReasoningNode
from graphite_web import WebConnectionItem, WebNode

from graphite_session.content_codec import (
    decode_image_bytes,
    deserialize_history,
    process_content_for_deserialization,
)
from graphite_session.scene_index import CHILD_LINK_NODE_TYPES


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

    def _deserialize_basic_connection(self, data, scene, all_nodes_map, connection_cls, target_list_name):
        start_node = all_nodes_map.get(data["start_node_index"])
        end_node = all_nodes_map.get(data["end_node_index"])
        if not start_node or not end_node:
            return None
        connection = connection_cls(start_node, end_node)
        self._set_incoming_connection(end_node, connection)
        scene.addItem(connection)
        getattr(scene, target_list_name).append(connection)
        return connection

    def deserialize_chart(self, data, scene, all_nodes_map):
        parent_node = all_nodes_map.get(data.get("parent_node_index"))
        chart = scene.add_chart(
            data["data"],
            QPointF(data["position"]["x"], data["position"]["y"]),
            parent_content_node=parent_node,
        )
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
        end_node = nodes_map.get(data["end_node_index"])
        if not start_note or not end_node:
            print("Warning: Skipping orphaned system prompt connection during load.")
            return None

        connection = SystemPromptConnectionItem(start_note, end_node)
        scene.addItem(connection)
        scene.system_prompt_connections.append(connection)
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

    def deserialize_reasoning_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ReasoningConnectionItem, "reasoning_connections"
        )

    def deserialize_html_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, HtmlConnectionItem, "html_connections"
        )

    def deserialize_artifact_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, ArtifactConnectionItem, "artifact_connections"
        )

    def deserialize_workflow_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, WorkflowConnectionItem, "workflow_connections"
        )

    def deserialize_quality_gate_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, QualityGateConnectionItem, "quality_gate_connections"
        )

    def deserialize_code_review_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, CodeReviewConnectionItem, "code_review_connections"
        )

    def deserialize_gitlink_connection(self, data, scene, all_nodes_map):
        return self._deserialize_basic_connection(
            data, scene, all_nodes_map, GitlinkConnectionItem, "gitlink_connections"
        )

    def deserialize_group_summary_connection(self, data, scene, nodes_map, notes_map):
        start_node = nodes_map.get(data["start_node_index"])
        end_note = notes_map.get(data["end_note_index"])
        if not start_node or not end_note:
            print("Warning: Skipping orphaned group summary connection.")
            return None

        connection = GroupSummaryConnectionItem(start_node, end_note)
        scene.addItem(connection)
        scene.group_summary_connections.append(connection)
        return connection

    def deserialize_node(self, index, data, all_nodes_map):
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
                node.prompt_input.setText(data.get("prompt", ""))
                node.set_code(data.get("code", ""))
                node.set_output(data.get("output", ""))
                node.set_ai_analysis(data.get("analysis", ""))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
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
                if summary:
                    node.set_result(summary, sources)
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                self._connect_if_available(node.run_clicked, "execute_web_node")
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
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                scene.addItem(node)
                scene.conversation_nodes.append(node)

        elif node_type == "reasoning":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = ReasoningNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.prompt_input.setText(data.get("prompt", ""))
                node.budget_slider.setValue(data.get("thinking_budget", 3))
                node.thought_process_display.setMarkdown(data.get("thought_process", ""))
                node.set_status(data.get("status", "Idle"))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                self._connect_if_available(node.reasoning_requested, "execute_reasoning_node")
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                scene.addItem(node)
                scene.reasoning_nodes.append(node)

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
                scene.addItem(node)
                scene.artifact_nodes.append(node)

        elif node_type == "workflow":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = WorkflowNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.goal_input.setPlainText(data.get("goal", ""))
                node.constraints_input.setPlainText(data.get("constraints", ""))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                node.blueprint_markdown = data.get("blueprint_markdown", "")
                node.recommendations = data.get("recommendations", [])
                node.status = data.get("status", "Idle")
                if node.blueprint_markdown or node.recommendations:
                    node.set_plan(
                        {
                            "blueprint_markdown": node.blueprint_markdown,
                            "recommended_plugins": node.recommendations,
                        }
                    )
                else:
                    node.set_status(node.status)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.workflow_requested, "execute_workflow_node")
                self._connect_if_available(node.plugin_requested, "instantiate_seeded_plugin")
                scene.addItem(node)
                scene.workflow_nodes.append(node)

        elif node_type == "graph_diff":
            left_source = all_nodes_map.get(data.get("left_source_index"))
            right_source = all_nodes_map.get(data.get("right_source_index"))
            if left_source and right_source:
                node = GraphDiffNode(left_source, right_source)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.comparison_markdown = data.get("comparison_markdown", "")
                node.note_summary = data.get("note_summary", "")
                node.status = data.get("status", "Idle")
                if node.comparison_markdown:
                    node.set_result(
                        {
                            "comparison_markdown": node.comparison_markdown,
                            "note_summary": node.note_summary,
                        }
                    )
                else:
                    node.set_status(node.status)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.compare_requested, "execute_graph_diff_node")
                self._connect_if_available(node.note_requested, "create_graph_diff_note")
                scene.addItem(node)
                scene.graph_diff_nodes.append(node)

                for source_node in (left_source, right_source):
                    connection = GraphDiffConnectionItem(source_node, node)
                    scene.addItem(connection)
                    scene.graph_diff_connections.append(connection)

        elif node_type == "quality_gate":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = QualityGateNode(parent_node)
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.goal_input.setPlainText(data.get("goal", ""))
                node.criteria_input.setPlainText(data.get("criteria", ""))
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.include_branch_context = data.get("include_branch_context", True)
                node.review_markdown = data.get("review_markdown", "")
                node.note_summary = data.get("note_summary", "")
                node.recommendations = data.get("recommendations", [])
                node.verdict = data.get("verdict", "pending")
                node.readiness_score = data.get("readiness_score", 0)
                node.status = data.get("status", "Idle")
                if node.review_markdown or node.recommendations:
                    node.set_review(
                        {
                            "verdict": node.verdict,
                            "readiness_score": node.readiness_score,
                            "review_markdown": node.review_markdown,
                            "note_summary": node.note_summary,
                            "recommended_plugins": node.recommendations,
                        }
                    )
                else:
                    node.set_status(node.status)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.review_requested, "execute_quality_gate_node")
                self._connect_if_available(node.plugin_requested, "instantiate_seeded_plugin")
                self._connect_if_available(node.note_requested, "create_quality_gate_note")
                scene.addItem(node)
                scene.quality_gate_nodes.append(node)

        elif node_type == "code_review":
            parent_node = all_nodes_map.get(data["parent_node_index"])
            if parent_node:
                node = CodeReviewNode(parent_node, settings_manager=getattr(self.window, "settings_manager", None))
                node.setPos(data["position"]["x"], data["position"]["y"])
                node.context_input.setPlainText(data.get("review_context", ""))
                node._set_source_text(
                    data.get("source_text", ""),
                    data.get(
                        "source_state",
                        {
                            "origin": "",
                            "label": "",
                            "repo": "",
                            "branch": "",
                            "path": "",
                            "local_path": "",
                            "edited": False,
                        },
                    ),
                )
                node.conversation_history = deserialize_history(data.get("conversation_history", []))
                node.review_markdown = data.get("review_markdown", "")
                node.review_data = data.get("review_data", {})
                node.verdict = data.get("verdict", "pending")
                node.quality_score = data.get("quality_score", 0)
                node.risk_level = data.get("risk_level", "unknown")
                node.status = data.get("status", "Idle")
                if node.review_data:
                    node.set_review(node.review_data)
                elif node.review_markdown:
                    node.overview_display.setMarkdown(node.review_markdown)
                    node.set_status(node.status)
                else:
                    node.set_status(node.status)
                if data.get("is_collapsed", False):
                    node.set_collapsed(True)
                self._connect_if_available(node.review_requested, "execute_code_review_node")
                scene.addItem(node)
                scene.code_review_nodes.append(node)

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
        frame.setPos(data["position"]["x"], data["position"]["y"])
        frame.note = data["note"]

        if "color" in data:
            frame.color = data["color"]
        if "header_color" in data:
            frame.header_color = data["header_color"]
        if "size" in data:
            frame.rect.setWidth(data["size"]["width"])
            frame.rect.setHeight(data["size"]["height"])

        scene.addItem(frame)
        scene.frames.append(frame)
        frame.setZValue(-2)
        if not data.get("is_locked", True):
            frame.toggle_lock()
        if data.get("is_collapsed", False):
            frame.toggle_collapse()
        return frame

    def deserialize_container(self, data, scene, all_items_map):
        items = [all_items_map[index] for index in data["items"] if index in all_items_map]
        container = Container(items)
        container.setPos(data["position"]["x"], data["position"]["y"])
        container.title = data.get("title", "Container")
        container.color = data.get("color", "#3a3a3a")
        container.header_color = data.get("header_color")

        rect_data = data.get("expanded_rect")
        if rect_data:
            container.expanded_rect = QRectF(
                rect_data["x"], rect_data["y"], rect_data["width"], rect_data["height"]
            )

        if data.get("is_collapsed", False):
            container.toggle_collapse()

        scene.addItem(container)
        scene.containers.append(container)
        container.setZValue(-3)
        return container

    def _restore_children(self, node_payloads, all_nodes_map):
        for index, node_data in enumerate(node_payloads):
            node = all_nodes_map.get(index)
            if not node:
                continue
            if isinstance(node, CHILD_LINK_NODE_TYPES) and "children_indices" in node_data:
                for child_index in node_data["children_indices"]:
                    child_node = all_nodes_map.get(child_index)
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
            note.content = note_data["content"]
            notes_map[index] = note
        return notes_map

    def _load_pins(self, scene, pins_data):
        if self.window and hasattr(self.window, "pin_overlay"):
            self.window.pin_overlay.clear_pins()

        for pin_data in pins_data:
            pin = scene.add_navigation_pin(QPointF(pin_data["position"]["x"], pin_data["position"]["y"]))
            pin.title = pin_data["title"]
            pin.note = pin_data.get("note", "")
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

        try:
            chat_data = chat["data"]
            all_nodes_map = {}

            for index, node_data in enumerate(chat_data["nodes"]):
                self.deserialize_node(index, node_data, all_nodes_map)

            self._restore_children(chat_data["nodes"], all_nodes_map)

            notes_map = self._load_notes(scene, notes_data)

            charts_map = {}
            for index, chart_data in enumerate(chat_data.get("charts", [])):
                charts_map[index] = self.deserialize_chart(chart_data, scene, all_nodes_map)

            frame_source_map = dict(all_nodes_map)
            chart_offset = len(frame_source_map)
            for index, chart in charts_map.items():
                frame_source_map[chart_offset + index] = chart

            frames_map = {}
            for index, frame_data in enumerate(chat_data.get("frames", [])):
                frames_map[index] = self.deserialize_frame(frame_data, scene, frame_source_map)

            all_items_list = list(all_nodes_map.values()) + list(notes_map.values()) + list(charts_map.values()) + list(
                frames_map.values()
            )
            all_items_map = {index: item for index, item in enumerate(all_items_list)}

            for container_data in chat_data.get("containers", []):
                self.deserialize_container(container_data, scene, all_items_map)

            chat_nodes_map = {index: node for index, node in enumerate(scene.nodes)}

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
                ("reasoning_connections", self.deserialize_reasoning_connection),
                ("html_connections", self.deserialize_html_connection),
                ("artifact_connections", self.deserialize_artifact_connection),
                ("workflow_connections", self.deserialize_workflow_connection),
                ("quality_gate_connections", self.deserialize_quality_gate_connection),
                ("code_review_connections", self.deserialize_code_review_connection),
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
