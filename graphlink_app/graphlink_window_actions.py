import os
import re
import json
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMessageBox
import graphlink_config as config
import api_provider
from graphlink_prompts import _TokenBytesEncoder
from graphlink_widgets import GhostNodePreview, LoadingAnimation
from graphlink_node import ChatNode, CodeNode
from graphlink_canvas_items import Note
from graphlink_connections import GroupSummaryConnectionItem
from graphlink_pycoder import PyCoderMode, PyCoderNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_web import WebNode
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkNode, GitlinkWorkerThread
from graphlink_config import get_current_palette
from graphlink_config import get_semantic_color
from graphlink_memory import (
    append_history,
    assign_history,
    get_node_history,
    history_to_transcript,
    resolve_branch_parent,
    trim_history,
)
from graphlink_agents import (
    ChatWorkerThread, KeyTakeawayWorkerThread, ExplainerWorkerThread, ChartWorkerThread,
    GroupSummaryWorkerThread, ImageGenerationWorkerThread, CodeExecutionWorker,
    PyCoderExecutionWorker, PyCoderExecutionAgent, PyCoderRepairAgent, PyCoderAnalysisAgent,
    PyCoderAgentWorker, SandboxStage, CodeSandboxExecutionWorker, WebWorkerThread,
    KeyTakeawayAgent, ExplainerAgent, GroupSummaryAgent, ImageGenerationAgent
)

class WindowActionsMixin:
    def _graphics_item_dimensions(self, item):
        if item is None:
            return 0.0, 0.0
        if hasattr(item, 'width') and hasattr(item, 'height'):
            return float(item.width), float(item.height)
        bounds = item.boundingRect()
        return float(bounds.width()), float(bounds.height())

    def _show_loading_animation(self, anchor_node=None, scene_pos=None):
        self._clear_loading_animation()

        loading = LoadingAnimation()
        if anchor_node is not None and anchor_node.scene() == self.chat_view.scene():
            loading.setParentItem(anchor_node)
            width, height = self._graphics_item_dimensions(anchor_node)
            loading.setPos(QPointF(width + loading.radius + 26.0, height * 0.5))
        else:
            self.chat_view.scene().addItem(loading)
            loading.setPos(QPointF(scene_pos) if scene_pos is not None else QPointF())

        loading.start()
        self.loading_animation = loading
        return loading

    def _clear_loading_animation(self):
        loading = getattr(self, "loading_animation", None)
        if not loading:
            return

        loading.stop()
        if loading.scene():
            loading.scene().removeItem(loading)
        loading.deleteLater()
        self.loading_animation = None

    def _should_include_branch_context(self, node):
        return bool(getattr(node, "include_branch_context", True))

    def _branch_context_history(self, node, history_source):
        if not self._should_include_branch_context(node) or history_source is None:
            return []
        return get_node_history(history_source)

    def _show_pending_response_preview(self, source_node):
        self._clear_pending_response_preview()
        if source_node is None or source_node.scene() != self.chat_view.scene():
            return None

        scene = self.chat_view.scene()
        preview = GhostNodePreview(
            width=ChatNode.DEFAULT_WIDTH,
            height=max(ChatNode.MIN_HEIGHT + 18, 128),
            parent=source_node,
        )
        preview_scene_pos = scene.find_branch_position(source_node, preview)
        preview.setPos(source_node.mapFromScene(preview_scene_pos))
        scene.register_transient_layout_item(preview)
        self.pending_response_preview = preview
        return preview

    def _consume_pending_response_preview_position(self):
        preview = getattr(self, "pending_response_preview", None)
        if not preview:
            return None

        preview_pos = preview.scenePos()
        self._clear_pending_response_preview()
        return preview_pos

    def _clear_pending_response_preview(self):
        preview = getattr(self, "pending_response_preview", None)
        if not preview:
            return

        if hasattr(preview, "stop_animation"):
            preview.stop_animation()
        scene = self.chat_view.scene()
        if scene:
            scene.unregister_transient_layout_item(preview)
        if preview.scene():
            preview.scene().removeItem(preview)
        preview.deleteLater()
        self.pending_response_preview = None

    def _build_attachment_node_summary(self, attachments):
        if not attachments:
            return "[Attachment]"

        names = [item.get('name') or os.path.basename(item.get('path', '')) for item in attachments]
        if len(names) == 1:
            return f"[Attachment] {names[0]}"

        preview = ", ".join(names[:3])
        if len(names) > 3:
            preview += ", ..."
        return f"[{len(names)} Attachments] {preview}"

    def _escape_xml_attribute(self, value):
        return (
            str(value)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _wrap_attachment_xml(self, attachment, content):
        name = attachment.get('name') or os.path.basename(attachment.get('path', ''))
        extension = os.path.splitext(name)[1].lower() or "none"
        attachment_path = os.path.normpath(attachment.get('path', ''))
        cdata_content = content.replace("]]>", "]]]]><![CDATA[>")
        return (
            f'<attachment name="{self._escape_xml_attribute(name)}" '
            f'kind="{self._escape_xml_attribute(attachment.get("kind", "document"))}" '
            f'extension="{self._escape_xml_attribute(extension)}" '
            f'path="{self._escape_xml_attribute(attachment_path)}">\n'
            f'<![CDATA[\n{cdata_content}\n]]>\n'
            f'</attachment>'
        )

    def send_message(self):
        message = self.message_input.text().strip()
        attachments = list(getattr(self, 'pending_attachments', []))
        if not message and not attachments:
            return

        self.message_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.attach_file_btn.setEnabled(False)

        branch_parent = resolve_branch_parent(self.current_node)
        history_context_node = branch_parent if branch_parent else self.current_node
        history = get_node_history(history_context_node)
        user_node_text = message if message else self._build_attachment_node_summary(attachments)
        media_content_parts = []
        text_content_parts = []

        user_node = self.chat_view.scene().add_chat_node(
            user_node_text,
            is_user=True, 
            parent_node=branch_parent,
            conversation_history=history
        )
        if user_node is None:
            self.handle_error("Unable to add the message node to the scene.")
            return
        
        for attachment in attachments:
            attachment_path = attachment.get('path')
            try:
                if attachment.get('kind') == 'image':
                    with open(attachment_path, 'rb') as f:
                        image_bytes = f.read()
                    self.chat_view.scene().add_image_node(image_bytes, user_node, prompt=message)
                    media_content_parts.append({'type': 'image_bytes', 'data': image_bytes})
                    continue

                if attachment.get('kind') == 'audio':
                    self.chat_view.scene().add_document_node(
                        title=attachment.get('name') or os.path.basename(attachment_path),
                        content="",
                        parent_user_node=user_node,
                        attachment_kind='audio',
                        file_path=attachment_path,
                        mime_type=attachment.get('mime_type'),
                        duration_seconds=attachment.get('duration_seconds'),
                        byte_size=attachment.get('byte_size'),
                        preview_label=attachment.get('context_label'),
                    )
                    media_content_parts.append({
                        'type': 'audio_file',
                        'path': attachment_path,
                        'name': attachment.get('name') or os.path.basename(attachment_path),
                        'mime_type': attachment.get('mime_type'),
                        'duration_seconds': attachment.get('duration_seconds'),
                        'byte_size': attachment.get('byte_size'),
                    })
                    continue

                file_name = attachment.get('name') or os.path.basename(attachment_path)
                doc_content = attachment.get('content')
                error = None
                if doc_content is None:
                    doc_content, error = self.file_handler.read_file(attachment_path)
                if error:
                    self.handle_error(error)
                    user_node.scene().delete_chat_node(user_node)
                    return

                self.chat_view.scene().add_document_node(
                    title=file_name,
                    content=doc_content,
                    parent_user_node=user_node,
                    attachment_kind='document',
                    file_path=attachment_path,
                    byte_size=attachment.get('byte_size'),
                    preview_label=attachment.get('context_label'),
                )
                text_content_parts.append({
                    'type': 'text',
                    'text': self._wrap_attachment_xml(attachment, doc_content),
                })
            except IOError as e:
                self.handle_error(f"Could not read attachment '{attachment_path}': {e}")
                user_node.scene().delete_chat_node(user_node)
                return

        if message:
            text_content_parts.insert(0, {'type': 'text', 'text': user_node_text})

        # Keep media parts ahead of prompt text for multimodal models that prefer that ordering.
        llm_content_parts = media_content_parts + text_content_parts

        if len(llm_content_parts) == 1 and llm_content_parts[0].get('type') == 'text':
            payload_for_llm = llm_content_parts[0]['text']
        else:
            payload_for_llm = llm_content_parts
        input_msg_for_token = {'role': 'user', 'content': payload_for_llm}
        input_tokens = self.token_estimator.count_tokens(json.dumps(input_msg_for_token, cls=_TokenBytesEncoder))

        trimmed_history, context_tokens = trim_history(
            history,
            self.token_estimator,
            max_tokens=8000,
            system_prompt_estimate=500 if self.settings_manager.get_enable_system_prompt() else 0,
            reserve_tokens=input_tokens,
        )
        self.token_counter_widget.update_counts(input_tokens=input_tokens, context_tokens=context_tokens)

        history_for_worker = append_history(trimmed_history, [input_msg_for_token])
        assign_history(user_node, history_for_worker)
        self.session_manager.save_current_chat()

        # Image generation is an explicit node action. Never infer it from chat
        # text: doing so routes ordinary local/Ollama prompts into the API-only
        # image backend and produces a misleading provider-mode error.
        self._show_pending_response_preview(user_node)

        worker_thread = ChatWorkerThread(self.agent, history_for_worker, history_context_node)
        self.chat_thread = worker_thread
        self._set_main_request_state(
            active=True,
            cancel_callback=lambda thread=worker_thread: self._cancel_main_chat_request(thread),
        )
        worker_thread.finished.connect(
            lambda new_message, node=user_node, history=history_for_worker, tokens=input_tokens, thread=worker_thread:
                self.handle_response(new_message, node, history, tokens, thread)
        )
        worker_thread.status.connect(self._handle_chat_worker_status)
        worker_thread.error.connect(lambda error_message, thread=worker_thread: self._handle_main_chat_error(error_message, thread))
        worker_thread.cancelled.connect(lambda thread=worker_thread: self._handle_main_chat_cancelled(thread))
        worker_thread.finished.connect(lambda _message, thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.error.connect(lambda _error, thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.cancelled.connect(lambda thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.start()

    def handle_response(self, new_assistant_message, user_node, history_before_assistant, input_tokens, worker_thread=None):
        if worker_thread is not None and self.chat_thread is not worker_thread:
            return

        scene = self.chat_view.scene()
        if not user_node or user_node.scene() is None or user_node.scene() is not scene:
            self._set_main_request_state(active=False)
            self._clear_loading_animation()
            self._clear_pending_response_preview()
            return

        self._set_main_request_state(active=False)
        self._clear_loading_animation()
        assign_history(user_node, history_before_assistant)

        full_history = append_history(history_before_assistant, [new_assistant_message])
        response_text = new_assistant_message['content']
        
        output_tokens = self.token_estimator.count_tokens(response_text)
        self.total_session_tokens += input_tokens + output_tokens
        self.token_counter_widget.update_counts(output_tokens=output_tokens, total_tokens=self.total_session_tokens)

        parsed_parts = self._parse_response(response_text)
        text_content_parts = [part['content'] for part in parsed_parts if part['type'] == 'text']
        text_content = "\n\n".join(text_content_parts)

        ai_node = None
        if text_content or parsed_parts:
            placeholder_text = text_content
            if not placeholder_text:
                if any(part['type'] == 'code' for part in parsed_parts):
                    placeholder_text = "[Generated Content]"
                elif any(part['type'] == 'thinking' for part in parsed_parts):
                    placeholder_text = "[Assistant Reasoning]"
                else:
                    placeholder_text = "[Empty Response]"
            preview_pos = self._consume_pending_response_preview_position()
            ai_node = scene.add_chat_node(
                placeholder_text,
                is_user=False, 
                parent_node=user_node, 
                conversation_history=full_history,
                preferred_pos=preview_pos,
            )
        else:
            self._clear_pending_response_preview()
        
        parent_for_content = ai_node if ai_node else user_node
        last_created_node = ai_node

        for part in parsed_parts:
            if part['type'] == 'code':
                code_node = scene.add_code_node(part['content'], part['language'], parent_for_content)
                last_created_node = code_node
            elif part['type'] == 'thinking':
                thinking_node = scene.add_thinking_node(part['content'], parent_for_content)
                last_created_node = thinking_node

        self.current_node = last_created_node if last_created_node else user_node
        self.chat_view.reveal_item(self.current_node)
        self.message_input.clear()
        self.message_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.attach_file_btn.setEnabled(True)
        self.clear_attachment()
        self.save_chat()

    def _handle_chat_worker_status(self, message):
        if not message:
            return
        self.notification_banner.show_message(message, 7000, "info")

    def _parse_response(self, response_text):
        parts = []
        code_block_tag_pattern = re.compile(r"<code_block>([\s\S]*?)</code_block>", re.IGNORECASE)
        code_fence_pattern = re.compile(r"```(\w*)\s*\n?([\s\S]*?)\s*```")
        thinking_content, remaining_text = api_provider.split_reasoning_and_content(response_text)
        if thinking_content:
            parts.append({'type': 'thinking', 'content': thinking_content})
        text_content = ""
        code_snippets = []
        language = ""
        code_block_match = code_block_tag_pattern.search(remaining_text)
        if code_block_match:
            code_content_raw = code_block_match.group(1).strip()
            text_content = (remaining_text[:code_block_match.start()] + remaining_text[code_block_match.end():]).strip()
            inner_matches = list(code_fence_pattern.finditer(code_content_raw))
            if inner_matches:
                language = inner_matches[0].group(1).strip()
                code_snippets = [m.group(2).strip() for m in inner_matches]
            else:
                code_snippets = [code_content_raw]
        else:
            matches = list(code_fence_pattern.finditer(remaining_text))
            if matches:
                language = matches[0].group(1).strip()
                code_snippets = [m.group(2).strip() for m in matches]
                text_content = code_fence_pattern.sub("", remaining_text).strip()
            else:
                text_content = remaining_text.strip()
        if text_content:
            parts.append({'type': 'text', 'content': text_content})
        if code_snippets:
            combined_code = "\n\n# --- Next Code Block ---\n\n".join(code_snippets).strip()
            if combined_code:
                parts.append({'type': 'code', 'language': language, 'content': combined_code})
        if not parts and response_text.strip():
             return [{'type': 'text', 'content': response_text.strip()}]
        return parts

    def regenerate_node(self, node_to_regenerate):
        if not hasattr(node_to_regenerate, 'parent_node') or not node_to_regenerate.parent_node:
            self.notification_banner.show_message("This node has no parent and cannot be regenerated.", 5000, "warning")
            return

        history_for_worker = get_node_history(node_to_regenerate.parent_node)
        self.message_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.attach_file_btn.setEnabled(False)
        self._show_loading_animation(anchor_node=node_to_regenerate)
        worker_thread = ChatWorkerThread(self.agent, history_for_worker, node_to_regenerate.parent_node)
        self.chat_thread = worker_thread
        self._set_main_request_state(
            active=True,
            cancel_callback=lambda thread=worker_thread: self._cancel_main_chat_request(thread),
        )
        worker_thread.finished.connect(
            lambda new_message, node=node_to_regenerate, history=history_for_worker, thread=worker_thread:
                self.handle_regenerated_response(new_message, node, history, thread)
        )
        worker_thread.status.connect(self._handle_chat_worker_status)
        worker_thread.error.connect(lambda error_message, thread=worker_thread: self._handle_main_chat_error(error_message, thread))
        worker_thread.cancelled.connect(lambda thread=worker_thread: self._handle_regeneration_cancelled(thread))
        worker_thread.finished.connect(lambda _message, thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.error.connect(lambda _error, thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.cancelled.connect(lambda thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.start()

    def handle_regenerated_response(self, new_assistant_message, old_node, parent_history, worker_thread=None):
        if worker_thread is not None and self.chat_thread is not worker_thread:
            return

        try:
            self._set_main_request_state(active=False)
            new_response = new_assistant_message['content']
            if not new_response or not new_response.strip():
                self.notification_banner.show_message("The model returned an empty response. The original response has been kept.", 6000, "warning")
                return
            scene = self.chat_view.scene()
            if not old_node or not old_node.scene(): return
            
            # Use safe duck typing check for chat node method
            if hasattr(scene, 'remove_associated_content_nodes'):
                scene.remove_associated_content_nodes(old_node)
                
            parsed_parts = self._parse_response(new_response)
            text_content_parts = [p['content'] for p in parsed_parts if p['type'] == 'text']
            text_content = "\n\n".join(text_content_parts)
            
            if hasattr(old_node, 'conversation_history'):
                assign_history(old_node, append_history(parent_history, [new_assistant_message]))
            if hasattr(old_node, 'update_content'):
                old_node.update_content(text_content if text_content else "[Generated Content]")
                
            last_created_node = old_node
            for part in parsed_parts:
                if part['type'] == 'code':
                    code_node = scene.add_code_node(part['content'], part['language'], old_node)
                    last_created_node = code_node
                elif part['type'] == 'thinking':
                    thinking_node = scene.add_thinking_node(part['content'], old_node)
                    last_created_node = thinking_node
            scene.update_connections()
            self.current_node = last_created_node
            self.chat_view.reveal_item(last_created_node)
            self.save_chat()
        except Exception as e:
            self.handle_error(f"An error occurred during regeneration: {str(e)}")
        finally:
            self._clear_loading_animation()
            self.message_input.setEnabled(True)
            self.send_button.setEnabled(True)
            self.attach_file_btn.setEnabled(True)
    
    def generate_takeaway(self, node):
        try:
            self._show_loading_animation(anchor_node=node)
            self.takeaway_thread = KeyTakeawayWorkerThread(KeyTakeawayAgent(), node.text, node.scenePos())
            self.takeaway_thread.finished.connect(self.handle_takeaway_response)
            self.takeaway_thread.error.connect(self.handle_error)
            self.takeaway_thread.finished.connect(self.takeaway_thread.deleteLater)
            self.takeaway_thread.error.connect(self.takeaway_thread.deleteLater)
            self.takeaway_thread.start()
        except Exception as e:
            self.handle_error(f"Error generating takeaway: {str(e)}")
            
    def handle_takeaway_response(self, response, node_pos):
        try:
            note_pos = QPointF(node_pos.x() + 400, node_pos.y())
            note = self.chat_view.scene().add_note(note_pos)
            note.width, note.content = 400, response
            note.color, note.header_color = get_current_palette().FRAME_COLORS["Mid Gray"]["color"], get_semantic_color("status_info").name()
            note._recalculate_geometry()
        except Exception as e:
            self.handle_error(f"Error creating takeaway note: {str(e)}")
        finally:
            self._clear_loading_animation()

    def generate_group_summary(self):
        try:
            scene = self.chat_view.scene()
            selected_nodes = [item for item in scene.selectedItems() if isinstance(item, ChatNode)]
            if len(selected_nodes) < 2:
                self.notification_banner.show_message("Please select two or more chat nodes to summarize.", 5000, "warning")
                return
            texts = [node.text for node in selected_nodes]
            avg_x, max_x, avg_y = 0, 0, 0
            for node in selected_nodes:
                pos = node.scenePos()
                avg_x += pos.x()
                max_x = max(max_x, pos.x() + node.width)
                avg_y += pos.y()
            note_pos = QPointF(max_x + 100, avg_y / len(selected_nodes))
            self._show_loading_animation(scene_pos=QPointF(note_pos.x() - 50, note_pos.y()))
            self.group_summary_thread = GroupSummaryWorkerThread(GroupSummaryAgent(), texts, note_pos, selected_nodes)
            self.group_summary_thread.finished.connect(self.handle_group_summary_response)
            self.group_summary_thread.error.connect(self.handle_error)
            self.group_summary_thread.finished.connect(self.group_summary_thread.deleteLater)
            self.group_summary_thread.error.connect(self.group_summary_thread.deleteLater)
            self.group_summary_thread.start()
        except Exception as e:
            self.handle_error(f"Error generating group summary: {str(e)}")

    def handle_group_summary_response(self, response, note_pos, source_nodes):
        try:
            scene = self.chat_view.scene()
            note = scene.add_note(note_pos)
            note.content, note.color, note.header_color = response, get_current_palette().FRAME_COLORS["Mid Gray"]["color"], get_semantic_color("status_warning").name()
            note.width, note.is_summary_note = 450, True
            note._recalculate_geometry()
            for source_node in source_nodes:
                if source_node.scene() == scene:
                    conn = GroupSummaryConnectionItem(source_node, note)
                    scene.addItem(conn)
                    scene.group_summary_connections.append(conn)
                    scene.register_connection(conn)
        except Exception as e:
            self.handle_error(f"Error creating summary note: {str(e)}")
        finally:
            self._clear_loading_animation()

    def generate_explainer(self, node):
        try:
            self._show_loading_animation(anchor_node=node)
            self.explainer_thread = ExplainerWorkerThread(ExplainerAgent(), node.text, node.scenePos())
            self.explainer_thread.finished.connect(self.handle_explainer_response)
            self.explainer_thread.error.connect(self.handle_error)
            self.explainer_thread.finished.connect(self.explainer_thread.deleteLater)
            self.explainer_thread.error.connect(self.explainer_thread.deleteLater)
            self.explainer_thread.start()
        except Exception as e:
            self.handle_error(f"Error generating explanation: {str(e)}")
            
    def handle_explainer_response(self, response, node_pos):
        try:
            note_pos = QPointF(node_pos.x() + 400, node_pos.y() + 100)
            note = self.chat_view.scene().add_note(note_pos)
            note.width, note.content = 400, response
            note.color, note.header_color = get_current_palette().FRAME_COLORS["Mid Gray"]["color"], get_semantic_color("status_info").name()
            note._recalculate_geometry()
        except Exception as e:
            self.handle_error(f"Error creating explainer note: {str(e)}")
        finally:
            self._clear_loading_animation()

    def _clean_chart_context_text(self, text):
        if text is None:
            return ""
        cleaned = re.sub(r"\r\n?", "\n", str(text))
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _read_chart_widget_text(self, widget):
        if widget is None:
            return ""

        try:
            if hasattr(widget, "toPlainText"):
                return self._clean_chart_context_text(widget.toPlainText())
            if hasattr(widget, "text"):
                return self._clean_chart_context_text(widget.text())
        except Exception:
            return ""

        return ""

    def _extract_chart_node_content(self, node):
        fragments = []

        def add_fragment(label, text):
            cleaned = self._clean_chart_context_text(text)
            if not cleaned:
                return
            fragments.append(f"{label}:\n{cleaned}")

        text_value = ""
        try:
            if hasattr(node, "text"):
                text_value = getattr(node, "text")
        except Exception:
            text_value = ""
        add_fragment("Visible Text", text_value)

        for label, attr_name in (
            ("Prompt", "prompt"),
            ("Document Title", "title"),
            ("Document Content", "content"),
            ("Code", "code"),
            ("Reasoning", "thinking_text"),
            ("Thought Process", "thought_process"),
            ("Summary", "summary"),
            ("Blueprint", "blueprint_markdown"),
            ("HTML", "html_content"),
            ("Goal", "goal"),
            ("Constraints", "constraints"),
            ("Diff Summary", "note_summary"),
        ):
            add_fragment(label, getattr(node, attr_name, ""))

        if hasattr(node, "get_prompt"):
            add_fragment("Prompt Input", node.get_prompt())
        if hasattr(node, "get_code"):
            add_fragment("Editable Code", node.get_code())
        if hasattr(node, "get_artifact_content"):
            add_fragment("Artifact Content", node.get_artifact_content())
        if hasattr(node, "get_html_content"):
            add_fragment("Rendered HTML", node.get_html_content())

        for label, widget_name in (
            ("Prompt Input", "prompt_input"),
            ("Instructions", "instruction_input"),
            ("Query", "query_input"),
            ("Plan Display", "plan_display"),
            ("Summary Display", "summary_display"),
            ("Thought Display", "thought_process_display"),
            ("Execution Output", "output_display"),
            ("AI Analysis", "ai_analysis_display"),
            ("Generated Code", "generated_code_display"),
            ("Raw Artifact", "raw_editor"),
            ("Diff Display", "diff_display"),
            ("HTML Input", "html_input"),
        ):
            add_fragment(label, self._read_chart_widget_text(getattr(node, widget_name, None)))

        sources = getattr(node, "sources", None)
        if isinstance(sources, list) and sources:
            source_lines = []
            for source in sources:
                if isinstance(source, dict):
                    title = self._clean_chart_context_text(source.get("title", ""))
                    url = self._clean_chart_context_text(source.get("url", ""))
                    combined = " - ".join(part for part in (title, url) if part)
                    if combined:
                        source_lines.append(combined)
                else:
                    cleaned = self._clean_chart_context_text(source)
                    if cleaned:
                        source_lines.append(cleaned)
            if source_lines:
                add_fragment("Sources", "\n".join(source_lines))

        history = getattr(node, "conversation_history", None)
        if isinstance(history, list) and history and node.__class__.__name__ != "ChatNode":
            add_fragment(
                "Local Conversation History",
                history_to_transcript(history, max_messages=8, max_chars_per_message=500),
            )

        local_history = getattr(node, "local_history", None)
        if isinstance(local_history, list) and local_history:
            add_fragment(
                "Local Session History",
                history_to_transcript(local_history, max_messages=8, max_chars_per_message=500),
            )

        unique_fragments = []
        seen = set()
        for fragment in fragments:
            dedupe_key = fragment.lower()
            if dedupe_key in seen:
                continue
            unique_fragments.append(fragment)
            seen.add(dedupe_key)

        return "\n\n".join(unique_fragments)

    def _collect_chart_related_nodes(self, node):
        related_nodes = []
        seen = {id(node)}

        def add_related(candidate):
            if candidate is None or id(candidate) in seen:
                return
            related_nodes.append(candidate)
            seen.add(id(candidate))

        context_anchor = resolve_branch_parent(node) or getattr(node, "parent_content_node", None) or getattr(node, "parent_node", None)
        if context_anchor is not None and context_anchor is not node:
            add_related(context_anchor)

        if context_anchor is not None:
            for docked_node in getattr(context_anchor, "docked_thinking_nodes", []):
                add_related(docked_node)

            scene = context_anchor.scene() or node.scene()
            if scene:
                for collection_name in ("code_nodes", "document_nodes", "thinking_nodes"):
                    for candidate in getattr(scene, collection_name, []):
                        if getattr(candidate, "parent_content_node", None) is context_anchor:
                            add_related(candidate)

            for child in getattr(context_anchor, "children", []):
                if child.__class__.__name__ != "ChatNode":
                    add_related(child)

        return related_nodes

    def _append_chart_section(self, sections, seen, title, content, per_section_limit=2200, total_limit=14000):
        cleaned = self._clean_chart_context_text(content)
        if not cleaned:
            return

        dedupe_key = cleaned.lower()
        if dedupe_key in seen:
            return

        section_overhead = len(title) + 8
        remaining = total_limit - sum(len(section) for section in sections)
        if remaining <= section_overhead + 80:
            return

        allowed_chars = min(per_section_limit, max(80, remaining - section_overhead))
        if len(cleaned) > allowed_chars:
            cleaned = cleaned[: allowed_chars - 3].rstrip() + "..."

        sections.append(f"## {title}\n{cleaned}")
        seen.add(dedupe_key)

    def _build_chart_source_text(self, node):
        sections = []
        seen = set()

        self._append_chart_section(
            sections,
            seen,
            f"Selected Node ({node.__class__.__name__})",
            self._extract_chart_node_content(node),
            per_section_limit=2600,
        )

        for related_node in self._collect_chart_related_nodes(node):
            self._append_chart_section(
                sections,
                seen,
                f"Attached Context ({related_node.__class__.__name__})",
                self._extract_chart_node_content(related_node),
                per_section_limit=2200,
            )

        branch_history = get_node_history(node)
        if branch_history:
            self._append_chart_section(
                sections,
                seen,
                "Recent Branch Conversation",
                history_to_transcript(branch_history, max_messages=12, max_chars_per_message=1200),
                per_section_limit=4000,
            )

        return "\n\n".join(sections).strip()
        
    def generate_chart(self, node, chart_type):
        try:
            chart_source_text = self._build_chart_source_text(node)
            if not chart_source_text:
                self.notification_banner.show_message(
                    "The selected branch does not contain readable text, reasoning, code, or document content to chart.",
                    12000,
                    "warning",
                )
                return
            self._show_loading_animation(anchor_node=node)
            self.chart_thread = ChartWorkerThread(chart_source_text, chart_type)
            self.chart_thread.finished.connect(lambda data, emitted_chart_type, source_node=node: self.handle_chart_data(data, emitted_chart_type, source_node))
            self.chart_thread.error.connect(self.handle_error)
            self.chart_thread.finished.connect(self.chart_thread.deleteLater)
            self.chart_thread.error.connect(self.chart_thread.deleteLater)
            self.chart_thread.start()
        except Exception as e:
            self.handle_error(f"Error generating chart: {str(e)}")
        
    def handle_chart_data(self, data, chart_type, source_node=None):
        try:
            chart_data = json.loads(data)
            if "error" in chart_data:
                self.notification_banner.show_message(chart_data["error"], 15000, "error")
                return
            scene = self.chat_view.scene()
            if source_node and source_node.scene():
                chart_pos = QPointF(source_node.scenePos().x() + 450, source_node.scenePos().y())
            elif self.current_node and self.current_node.scene():
                chart_pos = QPointF(self.current_node.scenePos().x() + 450, self.current_node.scenePos().y())
            else:
                chart_pos = QPointF(0, 0)
            chart = scene.add_chart(chart_data, chart_pos, parent_content_node=source_node)
            self.current_node = chart
            self.chat_view.reveal_item(chart)
            self.save_chat()
        except Exception as e:
            self.handle_error(f"Error creating chart: {str(e)}")
        finally:
            self._clear_loading_animation()

    def generate_image(self, node):
        try:
            prompt = node.text
            if not prompt:
                self.notification_banner.show_message("The selected node has no text to use as a prompt.", 5000, "warning")
                return
            self._show_loading_animation(anchor_node=node)
            self.image_gen_thread = ImageGenerationWorkerThread(ImageGenerationAgent(), prompt)
            self.image_gen_thread.finished.connect(lambda image_bytes, p: self.handle_image_response(image_bytes, p, node))
            self.image_gen_thread.error.connect(self.handle_error)
            self.image_gen_thread.finished.connect(self.image_gen_thread.deleteLater)
            self.image_gen_thread.error.connect(self.image_gen_thread.deleteLater)
            self.image_gen_thread.start()
        except Exception as e:
            self.handle_error(f"Error initiating image generation: {str(e)}")

    def handle_image_response(self, image_bytes, prompt, parent_node):
        try:
            history_additions = []
            if not (
                isinstance(parent_node, ChatNode) and
                getattr(parent_node, 'is_user', False) and
                parent_node.text.strip() == (prompt or "").strip()
            ):
                history_additions.append({'role': 'user', 'content': prompt})
            history_additions.append({'role': 'assistant', 'content': '[Image successfully generated]'})
            history = append_history(get_node_history(parent_node), history_additions)
            ai_node = self.chat_view.scene().add_chat_node(
                f"Generated image for prompt: \"{prompt}\"",
                is_user=False, parent_node=parent_node, conversation_history=history
            )
            self.chat_view.scene().add_image_node(image_bytes, ai_node, prompt)
            self.chat_view.reveal_item(ai_node)
            self.save_chat()
        except Exception as e:
            self.handle_error(f"Failed to display generated image: {e}")
        finally:
            self._clear_loading_animation()

    def execute_pycoder_node(self, pycoder_node):
        if pycoder_node.is_running:
            self.stop_pycoder_node(pycoder_node)
            return

        pycoder_node.set_running_state(True)
        if pycoder_node.mode == PyCoderMode.MANUAL:
            code = pycoder_node.get_code()
            if not code.strip():
                pycoder_node.set_output("[No code to run]")
                pycoder_node.set_running_state(False)
                return
            worker_thread = CodeExecutionWorker(code, pycoder_node.repl)
            pycoder_node.worker_thread = worker_thread
            worker_thread.finished.connect(
                lambda output, history=self._branch_context_history(pycoder_node, pycoder_node.parent_node): self._handle_code_execution_result(output, pycoder_node, history)
            )
            worker_thread.error.connect(lambda error_msg: self._handle_pycoder_error(error_msg, pycoder_node))
            worker_thread.finished.connect(lambda _output, thread=worker_thread, node=pycoder_node: self._cleanup_pycoder_thread(thread, node))
            worker_thread.error.connect(lambda _error, thread=worker_thread, node=pycoder_node: self._cleanup_pycoder_thread(thread, node))
            worker_thread.start()
        elif pycoder_node.mode == PyCoderMode.AI_DRIVEN:
            prompt = pycoder_node.get_prompt()
            if not prompt.strip():
                pycoder_node.set_ai_analysis("Please enter a prompt.")
                pycoder_node.set_running_state(False)
                return
            pycoder_node.reset_statuses(); pycoder_node.set_code(""); pycoder_node.set_output(""); pycoder_node.set_ai_analysis("")
            context_node = pycoder_node.parent_node
            if isinstance(context_node, CodeNode): context_node = context_node.parent_content_node
            history = self._branch_context_history(pycoder_node, context_node)
            worker_thread = PyCoderExecutionWorker(prompt, history, pycoder_node.repl)
            pycoder_node.worker_thread = worker_thread
            worker_thread.log_update.connect(pycoder_node.update_status)
            worker_thread.approval_requested.connect(
                lambda code, worker=worker_thread, node=pycoder_node: self._handle_pycoder_approval_request(worker, node, code)
            )
            worker_thread.finished.connect(lambda result, history=history: self._handle_ai_pycoder_result(result, pycoder_node, history))
            worker_thread.error.connect(lambda error_msg: self._handle_pycoder_error(error_msg, pycoder_node))
            worker_thread.finished.connect(lambda _result, thread=worker_thread, node=pycoder_node: self._cleanup_pycoder_thread(thread, node))
            worker_thread.error.connect(lambda _error, thread=worker_thread, node=pycoder_node: self._cleanup_pycoder_thread(thread, node))
            worker_thread.start()

    def _handle_pycoder_approval_request(self, worker_thread, pycoder_node, code):
        # Mirrors _handle_code_sandbox_approval_request: AI-generated code runs in a
        # completely unsandboxed REPL subprocess with the full privileges of the user's
        # account, so the user sees exactly what will run and must opt in. MANUAL mode
        # is deliberately ungated - there the user authored the code themselves and
        # clicking Run *is* the approval.
        if not pycoder_node or getattr(pycoder_node, "is_disposed", False):
            worker_thread.deny()
            return

        message_box = QMessageBox(
            QMessageBox.Icon.Question,
            "Approve Py-Coder Execution",
            "This will run AI-generated Python code in a persistent local session with "
            "the full privileges of your user account (there is no sandboxing).\n\n"
            "If execution fails, automatically repaired versions of this code may run "
            "under this same approval.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        message_box.setDefaultButton(QMessageBox.StandardButton.No)
        message_box.setDetailedText(code or "[No code to display]")
        reply = message_box.exec()

        if reply == QMessageBox.StandardButton.Yes:
            worker_thread.approve()
        else:
            worker_thread.deny()

    def _cleanup_pycoder_thread(self, worker_thread, pycoder_node):
        if pycoder_node and getattr(pycoder_node, "worker_thread", None) is worker_thread:
            pycoder_node.worker_thread = None
        worker_thread.deleteLater()

    def stop_pycoder_node(self, pycoder_node):
        # Only ever touch this node's own worker_thread - previously this stopped
        # main_window.code_exec_thread/pycoder_exec_thread unconditionally, which (since
        # those were single attributes shared across every PyCoderNode) meant clicking
        # "stop" on one node could instead stop a *different*, concurrently-running
        # node's execution if it happened to be the most recently started one. Same
        # class of bug already fixed for CodeSandboxNode - see stop_code_sandbox_node.
        worker_thread = getattr(pycoder_node, "worker_thread", None)
        if worker_thread and worker_thread.isRunning():
            worker_thread.stop()
        pycoder_node.worker_thread = None

        pycoder_node.set_running_state(False)
        pycoder_node.set_ai_analysis("Execution manually stopped.")
        
    def _handle_code_execution_result(self, output, pycoder_node, parent_history):
        pycoder_node.set_output(output)
        code = pycoder_node.get_code()
        user_msg = f"--- EXECUTED PYTHON CODE ---\n```python\n{code}\n```\n\n--- EXECUTION OUTPUT ---\n{output}"
        
        self.pycoder_agent_thread = PyCoderAgentWorker(code, output)
        self.pycoder_agent_thread.finished.connect(lambda analysis: self._handle_pycoder_analysis_result(analysis, pycoder_node, parent_history, user_msg))
        self.pycoder_agent_thread.error.connect(lambda error_msg: self._handle_pycoder_error(error_msg, pycoder_node))
        self.pycoder_agent_thread.finished.connect(self.pycoder_agent_thread.deleteLater)
        self.pycoder_agent_thread.error.connect(self.pycoder_agent_thread.deleteLater)
        self.pycoder_agent_thread.start()

    def _handle_pycoder_analysis_result(self, analysis, pycoder_node, parent_history, user_msg):
        # We explicitly bundle the code, the output, and the analysis so downstream nodes inherit them.
        assign_history(pycoder_node, append_history(parent_history, [
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': analysis}
        ]))
        pycoder_node.set_ai_analysis(analysis)
        pycoder_node.set_running_state(False)
        self.setCurrentNode(pycoder_node)
        self.save_chat()

    def _handle_ai_pycoder_result(self, result_dict, pycoder_node, parent_history):
        analysis_text = result_dict.get('analysis', '')
        code = result_dict.get('code', '')
        output = result_dict.get('output', '')
        prompt = pycoder_node.get_prompt()
        
        # We bundle the generated code, execution output, and analysis into the history.
        assistant_msg = f"--- GENERATED CODE ---\n```python\n{code}\n```\n\n--- EXECUTION OUTPUT ---\n{output}\n\n--- ANALYSIS ---\n{analysis_text}"
        
        assign_history(pycoder_node, append_history(parent_history, [
            {'role': 'user', 'content': prompt},
            {'role': 'assistant', 'content': assistant_msg}
        ]))
        
        pycoder_node.set_code(code)
        pycoder_node.set_output(output)
        pycoder_node.set_ai_analysis(analysis_text)
        pycoder_node.set_running_state(False)
        self.setCurrentNode(pycoder_node)
        self.save_chat()

    def _handle_pycoder_error(self, error_message, pycoder_node):
        pycoder_node.set_ai_analysis(f"An error occurred: {error_message}"); pycoder_node.set_running_state(False)

    def execute_code_sandbox_node(self, sandbox_node):
        if not sandbox_node or getattr(sandbox_node, "is_disposed", False):
            return

        if sandbox_node.is_running:
            self.stop_code_sandbox_node(sandbox_node)
            return

        run_mode = getattr(sandbox_node, "last_run_mode", "generate")
        prompt = sandbox_node.get_prompt().strip()
        code = sandbox_node.get_code()
        requirements_manifest = sandbox_node.get_requirements()

        if run_mode == "generate" and not prompt:
            sandbox_node.set_error("Enter a task brief before generating sandbox code.")
            sandbox_node.set_running_state(False)
            return

        if run_mode == "manual" and not code.strip():
            sandbox_node.set_error("Add Python code before running the sandbox directly.")
            sandbox_node.set_running_state(False)
            return

        parent_history = self._branch_context_history(sandbox_node, sandbox_node.parent_node)
        trimmed_history, _ = trim_history(
            parent_history,
            self.token_estimator,
            max_tokens=6500,
            system_prompt_estimate=1200 + len(requirements_manifest),
        )

        sandbox_node.reset_statuses()
        sandbox_node.clear_terminal_output()
        sandbox_node.set_ai_analysis("")
        sandbox_node.set_running_state(True)

        existing_code = code if run_mode == "manual" else ""
        worker_thread = CodeSandboxExecutionWorker(
            sandbox_node.sandbox_id,
            prompt if run_mode == "generate" else "",
            trimmed_history,
            requirements_manifest,
            existing_code=existing_code,
        )
        sandbox_node.worker_thread = worker_thread

        worker_thread.log_update.connect(sandbox_node.update_status)
        worker_thread.terminal_chunk.connect(sandbox_node.append_terminal_output)
        worker_thread.approval_requested.connect(
            lambda code, reqs, worker=worker_thread, node=sandbox_node: self._handle_code_sandbox_approval_request(worker, node, code, reqs)
        )
        worker_thread.finished.connect(
            lambda result, node=sandbox_node, history=parent_history, mode=run_mode: self._handle_code_sandbox_result(result, node, history, mode)
        )
        worker_thread.error.connect(lambda error_msg, node=sandbox_node: self._handle_code_sandbox_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=sandbox_node: self._cleanup_code_sandbox_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=sandbox_node: self._cleanup_code_sandbox_thread(thread, node))
        worker_thread.start()

    def _handle_code_sandbox_approval_request(self, worker_thread, sandbox_node, code, requirements_manifest):
        if not sandbox_node or getattr(sandbox_node, "is_disposed", False):
            worker_thread.deny()
            return

        declared_packages = [line.strip() for line in (requirements_manifest or "").splitlines() if line.strip()]
        if declared_packages:
            package_summary = f"{len(declared_packages)} package(s) declared in requirements.txt will be installed from PyPI if not already cached:\n\n" + "\n".join(declared_packages)
        else:
            package_summary = "No extra packages are declared."

        message_box = QMessageBox(
            QMessageBox.Icon.Question,
            "Approve Sandbox Execution",
            "This will run Python code inside an isolated virtual environment with the "
            "full privileges of your user account (the environment isolates installed "
            "packages, not the operating system).\n\n" + package_summary,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        message_box.setDefaultButton(QMessageBox.StandardButton.No)
        message_box.setDetailedText(code or "[No code to display]")
        reply = message_box.exec()

        if reply == QMessageBox.StandardButton.Yes:
            worker_thread.approve()
        else:
            worker_thread.deny()

    def stop_code_sandbox_node(self, sandbox_node):
        # Only ever touch this node's own worker_thread. Previously this also stopped
        # main_window.sandbox_thread unconditionally, which - since that was a single
        # attribute shared across every CodeSandboxNode - meant clicking "stop" on one
        # sandbox node could also stop a *different*, concurrently-running sandbox
        # node's execution if it happened to be the most recently started one.
        worker_thread = getattr(sandbox_node, "worker_thread", None)
        if worker_thread and worker_thread.isRunning():
            worker_thread.stop()
        sandbox_node.worker_thread = None

        sandbox_node.append_terminal_output("\n[Sandbox] Execution manually stopped.\n")
        sandbox_node.status = "Stopped"
        sandbox_node.set_running_state(False)
        sandbox_node.set_error("Sandbox execution was manually stopped.")

    def _cleanup_code_sandbox_thread(self, worker_thread, sandbox_node):
        if sandbox_node and getattr(sandbox_node, "worker_thread", None) is worker_thread:
            sandbox_node.worker_thread = None
        worker_thread.deleteLater()

    def _handle_code_sandbox_result(self, result_dict, sandbox_node, parent_history, run_mode):
        if not sandbox_node or getattr(sandbox_node, "is_disposed", False) or not sandbox_node.scene():
            return

        code = result_dict.get("code", "")
        output = result_dict.get("output", "")
        analysis_text = result_dict.get("analysis", "")
        requirements_manifest = result_dict.get("requirements", sandbox_node.get_requirements())

        sandbox_node.set_requirements(requirements_manifest)
        sandbox_node.set_code(code)
        sandbox_node.set_output(output)
        sandbox_node.set_ai_analysis(analysis_text)
        sandbox_node.status = "Ready"
        sandbox_node.set_running_state(False)

        if run_mode == "generate":
            user_message = sandbox_node.get_prompt().strip()
            if requirements_manifest:
                user_message += f"\n\nRequirements:\n{requirements_manifest}"
        else:
            user_message = (
                "--- SANDBOX REQUEST ---\n"
                "Execute the following code in the sandbox virtualenv.\n\n"
                f"--- REQUIREMENTS ---\n{requirements_manifest or '[none specified]'}\n\n"
                f"--- CODE ---\n```python\n{code}\n```"
            )

        assistant_message = (
            f"--- SANDBOX REQUIREMENTS ---\n```text\n{requirements_manifest or '[none specified]'}\n```\n\n"
            f"--- SANDBOX CODE ---\n```python\n{code}\n```\n\n"
            f"--- EXECUTION OUTPUT ---\n{output}\n\n"
            f"--- REVIEW ---\n{analysis_text}"
        )

        assign_history(sandbox_node, append_history(parent_history, [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]))

        self.setCurrentNode(sandbox_node)
        self.save_chat()

    def _handle_code_sandbox_error(self, error_message, sandbox_node):
        if not sandbox_node or getattr(sandbox_node, "is_disposed", False):
            return

        sandbox_node.append_terminal_output(f"\n[Sandbox] {error_message}\n")
        sandbox_node.status = "Error"
        sandbox_node.set_running_state(False)
        sandbox_node.set_error(error_message)

    def execute_web_node(self, web_node):
        query = web_node.query.strip()
        if not query:
            web_node.set_error("Query cannot be empty."); return
        web_node.set_running_state(True); web_node.set_status("Initializing...")
        parent_node = web_node.parent_node
        parent_history = self._branch_context_history(web_node, parent_node)
        trimmed_history, _ = trim_history(
            parent_history,
            self.token_estimator,
            max_tokens=7000,
            system_prompt_estimate=1000,
        )
        self.web_worker_thread = WebWorkerThread(query, trimmed_history)
        self.web_worker_thread.update_status.connect(lambda status, node=web_node: self._handle_web_worker_status(status, node))
        self.web_worker_thread.finished.connect(lambda result, node=web_node, history=parent_history: self._handle_web_worker_finished(result, node, history))
        self.web_worker_thread.error.connect(lambda error, node=web_node: self._handle_web_worker_error(error, node))
        self.web_worker_thread.finished.connect(self.web_worker_thread.deleteLater)
        self.web_worker_thread.error.connect(self.web_worker_thread.deleteLater)
        self.web_worker_thread.start()

    def _handle_web_worker_status(self, status, node):
        if node and node.scene(): node.set_status(status)

    def _handle_web_worker_finished(self, result, node, base_history):
        if node and node.scene():
            node.set_result(result['summary'], result['sources'], base_history=base_history); node.set_running_state(False); self.save_chat()

    def _handle_web_worker_error(self, error_message, node):
        if node and node.scene():
            node.set_error(error_message); node.set_running_state(False)

    def handle_conversation_node_request(self, requesting_node, history):
        requesting_node.set_typing(True)
        worker_thread = ChatWorkerThread(self.agent, history, requesting_node.parent_node)
        self.conversation_node_thread = worker_thread
        requesting_node.worker_thread = worker_thread
        worker_thread.finished.connect(
            lambda new_message, node=requesting_node, thread=worker_thread:
                self.handle_conversation_node_response(new_message, node, thread)
        )
        worker_thread.status.connect(self._handle_chat_worker_status)
        worker_thread.error.connect(
            lambda error_msg, node=requesting_node, thread=worker_thread:
                self.handle_conversation_node_error(error_msg, node, thread)
        )
        worker_thread.cancelled.connect(
            lambda node=requesting_node, thread=worker_thread:
                self.handle_conversation_node_cancelled(node, thread)
        )
        worker_thread.finished.connect(lambda _message, node=requesting_node, thread=worker_thread: self._cleanup_conversation_node_thread(thread, node))
        worker_thread.error.connect(lambda _error, node=requesting_node, thread=worker_thread: self._cleanup_conversation_node_thread(thread, node))
        worker_thread.cancelled.connect(lambda node=requesting_node, thread=worker_thread: self._cleanup_conversation_node_thread(thread, node))
        worker_thread.start()

    def handle_conversation_node_response(self, new_message, target_node, worker_thread=None):
        if worker_thread is not None and getattr(target_node, "worker_thread", None) is not worker_thread:
            return

        target_node.set_typing(False)
        if target_node and target_node.scene():
            response_text = new_message.get('content', '')
            target_node.add_ai_message(response_text); self.save_chat()

    def handle_conversation_node_error(self, error_message, target_node, worker_thread=None):
        if worker_thread is not None and getattr(target_node, "worker_thread", None) is not worker_thread:
            return

        target_node.set_typing(False)
        self.notification_banner.show_message(f"An error occurred: {error_message}", 8000, "error")
        if target_node and target_node.scene():
            target_node.set_input_enabled(True); target_node.add_ai_message(f"[ERROR]: Could not get response. {error_message}")

    def handle_conversation_node_cancel(self, requesting_node):
        worker_thread = getattr(requesting_node, "worker_thread", None)
        if worker_thread and worker_thread.isRunning():
            requesting_node.set_cancel_pending(True)
            worker_thread.cancel()

    def handle_conversation_node_cancelled(self, target_node, worker_thread=None):
        if worker_thread is not None and getattr(target_node, "worker_thread", None) is not worker_thread:
            return

        target_node.set_typing(False)
        target_node.set_input_enabled(True)
        self.save_chat()
        self.notification_banner.show_message("Conversation request cancelled.", 3000, "info")

    def _cancel_main_chat_request(self, worker_thread):
        if worker_thread is None or worker_thread is not self.chat_thread:
            return
        worker_thread.cancel()

    def _handle_main_chat_error(self, error_message, worker_thread):
        if worker_thread is not self.chat_thread:
            return
        self._set_main_request_state(active=False)
        self.handle_error(error_message)

    def _handle_main_chat_cancelled(self, worker_thread):
        if worker_thread is not self.chat_thread:
            return
        self._set_main_request_state(active=False)
        self._clear_loading_animation()
        self._clear_pending_response_preview()
        self.message_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.attach_file_btn.setEnabled(True)
        self.save_chat()
        self.notification_banner.show_message("Request cancelled.", 3000, "info")

    def _handle_regeneration_cancelled(self, worker_thread):
        if worker_thread is not self.chat_thread:
            return
        self._set_main_request_state(active=False)
        self._clear_loading_animation()
        self.message_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.attach_file_btn.setEnabled(True)
        self.notification_banner.show_message("Regeneration cancelled.", 3000, "info")

    def _cleanup_main_chat_thread(self, worker_thread):
        if self.chat_thread is worker_thread:
            self.chat_thread = None
        worker_thread.deleteLater()

    def _cleanup_conversation_node_thread(self, worker_thread, conversation_node):
        if conversation_node and getattr(conversation_node, "worker_thread", None) is worker_thread:
            conversation_node.worker_thread = None
        if getattr(self, "conversation_node_thread", None) is worker_thread:
            self.conversation_node_thread = None
        worker_thread.deleteLater()

    def execute_artifact_node(self, artifact_node):
        """Starts the ArtifactAgent workflow."""
        instruction = artifact_node.get_instruction()
        if not instruction.strip():
            artifact_node.set_running_state(False)
            return

        artifact_node.set_running_state(True)
        artifact_node.add_chat_message(instruction, is_user=True)
        
        parent_history = self._branch_context_history(artifact_node, artifact_node.parent_node)
        current_doc = artifact_node.get_artifact_content()
        history_to_send = append_history(parent_history, artifact_node.local_history)
        
        # Token limit safety
        MAX_TOKENS = 8000
        SYSTEM_PROMPT_ESTIMATE = 1000 
        doc_tokens = self.token_estimator.count_tokens(current_doc)
        trimmed_history, _ = trim_history(
            history_to_send,
            self.token_estimator,
            max_tokens=MAX_TOKENS,
            system_prompt_estimate=SYSTEM_PROMPT_ESTIMATE + doc_tokens,
        )

        # Update the internal node state with the latest history before AI replies
        assign_history(artifact_node, append_history(parent_history, artifact_node.local_history))

        from graphlink_plugins.graphlink_plugin_artifact import ArtifactWorkerThread
        worker_thread = ArtifactWorkerThread(current_doc, trimmed_history)
        artifact_node.worker_thread = worker_thread
        worker_thread.finished.connect(lambda doc, msg, node=artifact_node: self._handle_artifact_result(doc, msg, node))
        worker_thread.error.connect(lambda err, node=artifact_node: self._handle_artifact_error(err, node))
        worker_thread.finished.connect(lambda _doc, _msg, thread=worker_thread, node=artifact_node: self._cleanup_artifact_thread(thread, node))
        worker_thread.error.connect(lambda _err, thread=worker_thread, node=artifact_node: self._cleanup_artifact_thread(thread, node))
        worker_thread.start()

    def _cleanup_artifact_thread(self, worker_thread, artifact_node):
        if artifact_node and getattr(artifact_node, "worker_thread", None) is worker_thread:
            artifact_node.worker_thread = None
        worker_thread.deleteLater()

    def stop_artifact_node(self, artifact_node):
        worker_thread = getattr(artifact_node, "worker_thread", None)
        if worker_thread and worker_thread.isRunning():
            worker_thread.stop()
        artifact_node.worker_thread = None
        artifact_node.add_chat_message("Generation manually stopped.", is_user=False)
        artifact_node.set_running_state(False)

    def _handle_artifact_result(self, new_doc, ai_msg, artifact_node):
        """Processes the finished document and commentary from the Artifact thread."""
        artifact_node.set_artifact_content(new_doc)
        if ai_msg:
            artifact_node.add_chat_message(ai_msg, is_user=False)
        artifact_node.set_running_state(False)
        
        parent_history = self._branch_context_history(artifact_node, artifact_node.parent_node)
        assign_history(artifact_node, append_history(parent_history, artifact_node.local_history))
        
        self.save_chat()

    def _handle_artifact_error(self, error_msg, artifact_node):
        """Handles an error within the Artifact thread."""
        artifact_node.add_chat_message(f"Error: {error_msg}", is_user=False)
        artifact_node.set_running_state(False)

    def execute_gitlink_node(self, gitlink_node):
        if not gitlink_node or getattr(gitlink_node, "is_disposed", False) or not gitlink_node.scene():
            return

        payload = gitlink_node.build_change_request()
        if not payload.get("task_prompt", "").strip():
            gitlink_node.set_error("Describe the code change you want before generating a change set.")
            return

        parent_history = get_node_history(gitlink_node.parent_node)
        context_estimate = self.token_estimator.count_tokens(payload.get("context_xml", ""))
        trimmed_parent_history, _ = trim_history(
            parent_history,
            self.token_estimator,
            max_tokens=6500,
            system_prompt_estimate=1600,
            reserve_tokens=min(5000, context_estimate),
        )
        payload["branch_transcript"] = history_to_transcript(
            trimmed_parent_history,
            max_messages=8,
            max_chars_per_message=700,
        )

        gitlink_node.set_running_state(True)

        worker_thread = GitlinkWorkerThread(payload)
        gitlink_node.worker_thread = worker_thread

        worker_thread.finished.connect(lambda result, node=gitlink_node, history=parent_history: self._handle_gitlink_result(result, node, history))
        worker_thread.error.connect(lambda error_msg, node=gitlink_node: self._handle_gitlink_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=gitlink_node: self._cleanup_gitlink_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=gitlink_node: self._cleanup_gitlink_thread(thread, node))
        worker_thread.start()

    def _cleanup_gitlink_thread(self, worker_thread, gitlink_node):
        if gitlink_node and getattr(gitlink_node, "worker_thread", None) is worker_thread:
            gitlink_node.worker_thread = None
        worker_thread.deleteLater()

    def _handle_gitlink_result(self, result, gitlink_node, parent_history):
        if not gitlink_node or getattr(gitlink_node, "is_disposed", False) or not gitlink_node.scene():
            return

        gitlink_node.set_proposal(result)
        gitlink_node.set_running_state(False)

        repo_name = gitlink_node.repo_state.get("repo") or gitlink_node.repo_input.text().strip() or "repository"
        request_text = gitlink_node.get_task_prompt().strip() or "Prepare a Gitlink change set."
        assistant_text = gitlink_node.proposal_markdown or result.get("summary", "")
        assign_history(gitlink_node, append_history(parent_history, [
            {'role': 'user', 'content': f"Gitlink task for {repo_name}: {request_text}"},
            {'role': 'assistant', 'content': assistant_text}
        ]))

        self.setCurrentNode(gitlink_node)
        self.save_chat()

    def _handle_gitlink_error(self, error_message, gitlink_node):
        if not gitlink_node or getattr(gitlink_node, "is_disposed", False):
            return
        gitlink_node.set_error(error_message)
        gitlink_node.set_running_state(False)

    def instantiate_seeded_plugin(self, source_node, plugin_name, seed_prompt):
        previous_node = self.current_node
        self.current_node = source_node
        new_node = self.plugin_portal.execute_plugin(plugin_name)

        if not new_node:
            self.current_node = previous_node
            return

        if seed_prompt:
            self._seed_plugin_prompt(new_node, seed_prompt)

        scene = self.chat_view.scene()
        scene.clearSelection()
        if hasattr(new_node, 'setSelected'):
            new_node.setSelected(True)
        self.setCurrentNode(new_node)
        self.chat_view.reveal_item(new_node)
        self.save_chat()

    def _seed_plugin_prompt(self, node, seed_prompt):
        # Plugin nodes implement seed_prompt(text) themselves (see PluginSpec.seedable in
        # graphlink_plugin_portal.py) - adding a new seedable plugin no longer requires
        # editing this dispatcher. Note is not a plugin node (System Prompt has no
        # dedicated node class, see PLUGIN_REGISTRY's "system_prompt" entry) so it keeps
        # its own branch here.
        seed_method = getattr(node, "seed_prompt", None)
        if callable(seed_method):
            seed_method(seed_prompt)
        elif isinstance(node, Note):
            node.content = seed_prompt
            if hasattr(node, '_recalculate_geometry'):
                node._recalculate_geometry()
