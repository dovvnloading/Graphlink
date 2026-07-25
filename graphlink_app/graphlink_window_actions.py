import os
import re
import json
from PySide6.QtCore import QPointF
import graphlink_config as config
import api_provider
from graphlink_prompts import _TokenBytesEncoder
from graphlink_widgets import GhostNodePreview, LoadingAnimation
from graphlink_composer import ComposerRequestState
from graphlink_node import ChatNode, CodeNode
from graphlink_canvas_items import Note
from graphlink_connections import GroupSummaryConnectionItem
from graphlink_utility import UtilityKind, render_context, source_snapshot
from graphlink_conversation_node import ConversationNode
from graphlink_html_view import HtmlViewNode
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
    GroupSummaryWorkerThread, ImageGenerationWorkerThread,
    PyCoderExecutionAgent, PyCoderRepairAgent, PyCoderAnalysisAgent, SandboxStage,
    KeyTakeawayAgent, ExplainerAgent, GroupSummaryAgent, ImageGenerationAgent
)

class WindowActionsMixin:
    def _utility_chat_epoch(self):
        return int(getattr(getattr(self, "session_manager", None), "_context_epoch", 0))

    def _utility_bounded_text(self, node):
        snapshot = source_snapshot(node, getattr(node, "text", ""))
        return render_context([snapshot])[0]

    def _utility_bounded_texts(self, nodes):
        snapshots = [source_snapshot(node, getattr(node, "text", "")) for node in nodes]
        rendered, omitted = render_context(snapshots)
        omitted_ids = set(omitted)
        return [snapshot.text for snapshot in snapshots if snapshot.source_id not in omitted_ids]

    def _utility_start(self, kind, source_nodes, worker, finished_factory):
        snapshots = [source_snapshot(node, getattr(node, "text", "")) for node in source_nodes]
        rendered_context, omitted = render_context(snapshots)
        operation_id = self.utility_operation_controller.begin(
            kind, snapshots, chat_epoch=self._utility_chat_epoch(),
            rendered_context=rendered_context,
            estimated_tokens=max(1, len(rendered_context) // 4),
            omitted_source_ids=omitted,
        )
        self.utility_operation_controller.mark_running(operation_id)
        self.utility_threads[operation_id] = worker
        worker.finished.connect(finished_factory(operation_id))
        worker.error.connect(lambda message, op=operation_id: self._utility_failed(op, message))
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        worker.start()
        return operation_id

    def _utility_failed(self, operation_id, message):
        if self.utility_operation_controller.cancellation_requested(operation_id):
            self._utility_cleanup(operation_id)
            self._clear_loading_animation()
            return
        self.utility_operation_controller.fail(operation_id, message)
        self._utility_cleanup(operation_id)
        self.handle_error(message)

    def _utility_cleanup(self, operation_id):
        self.utility_threads.pop(operation_id, None)

    def _utility_result(self, operation_id, source_nodes, response):
        controller = self.utility_operation_controller
        scene = self.chat_view.scene()
        if not controller.is_current(operation_id, self._utility_chat_epoch()):
            controller.mark_stale(operation_id)
            self._utility_cleanup(operation_id)
            self._clear_loading_animation()
            return None
        if any(node.scene() != scene for node in source_nodes):
            controller.mark_stale(operation_id)
            self._utility_cleanup(operation_id)
            self._clear_loading_animation()
            return None
        result = controller.complete(operation_id, response)
        self._utility_cleanup(operation_id)
        return result

    def cancel_latest_utility_operation(self):
        active = self.utility_operation_controller.active_operations()
        if not active:
            return False
        operation = active[-1]
        self.utility_operation_controller.cancel(operation.operation_id)
        worker = self.utility_threads.get(operation.operation_id)
        if worker is not None and hasattr(worker, "stop"):
            worker.stop()
        self._utility_cleanup(operation.operation_id)
        self._clear_loading_animation()
        self.notification_banner.show_message("Utility generation cancelled.", 2500, "info")
        return True

    def _decorate_utility_note(self, note, result, role, source_nodes):
        note.note_role = role.value
        note.operation_id = result.operation_id
        note.source_ids = [source.source_id for source in result.context.sources]
        note.source_revisions = {source.source_id: source.revision for source in result.context.sources if source.revision}
        note.provider_snapshot = result.provider_snapshot
        note.is_summary_note = role == UtilityKind.GROUP_SUMMARY
        scene = self.chat_view.scene()
        for source_node in source_nodes:
            conn = GroupSummaryConnectionItem(source_node, note)
            scene.addItem(conn)
            scene.group_summary_connections.append(conn)
            scene.register_connection(conn)

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

        request_id = self.composer_controller.begin_request(text=message, attachments=attachments)
        # Clear the visible prompt at the accepted-send boundary. The
        # controller retains the immutable request snapshot and restores the
        # text automatically if preparation, the provider, or cancellation
        # fails.
        self.composer_controller.clear_submitted_text()

        self.message_input.set_editor_enabled(False)

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
            self.composer_controller.fail(request_id, "Unable to add the message node to the scene.")
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
                    self.composer_controller.fail(request_id, error)
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
                self.composer_controller.fail(request_id, f"Could not read attachment '{attachment_path}': {e}")
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
        self.token_counter_widget.bridge.update_counts(input_tokens=input_tokens, context_tokens=context_tokens)

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
        self.composer_controller.mark_started(request_id)
        worker_thread.finished.connect(
            lambda new_message, node=user_node, history=history_for_worker, tokens=input_tokens, thread=worker_thread, rid=request_id:
                self.handle_response(new_message, node, history, tokens, thread, rid)
        )
        worker_thread.status.connect(self._handle_chat_worker_status)
        worker_thread.error.connect(lambda error_message, thread=worker_thread, rid=request_id: self._handle_main_chat_error(error_message, thread, rid))
        worker_thread.cancelled.connect(lambda thread=worker_thread, rid=request_id: self._handle_main_chat_cancelled(thread, rid))
        worker_thread.finished.connect(lambda _message, thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.error.connect(lambda _error, thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.cancelled.connect(lambda thread=worker_thread: self._cleanup_main_chat_thread(thread))
        worker_thread.start()

    def handle_response(self, new_assistant_message, user_node, history_before_assistant, input_tokens, worker_thread=None, request_id=None):
        if worker_thread is not None and self.chat_thread is not worker_thread:
            return
        if request_id and not self.composer_controller.is_current(request_id):
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
        self.token_counter_widget.bridge.update_counts(output_tokens=output_tokens, total_tokens=self.total_session_tokens)

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
        self.message_input.set_editor_enabled(True)
        self.clear_attachment()
        if request_id:
            self.composer_controller.complete(request_id, "Response ready")
            self.composer_controller.clear_after_success()
        self.save_chat()

    def _handle_chat_worker_status(self, message):
        if not message:
            return
        if getattr(self, 'composer_controller', None) and self.composer_controller.active_request_id:
            self.composer_controller.set_state(ComposerRequestState.GENERATING, message)
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
        self.message_input.set_editor_enabled(False)
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
            self.message_input.set_editor_enabled(True)

    def generate_takeaway(self, node):
        try:
            self._show_loading_animation(anchor_node=node)
            self.takeaway_thread = KeyTakeawayWorkerThread(KeyTakeawayAgent(), self._utility_bounded_text(node), node.scenePos())
            self._utility_start(
                UtilityKind.TAKEAWAY, [node], self.takeaway_thread,
                lambda operation_id: lambda response, node_pos: self.handle_takeaway_response(
                    operation_id, response, node_pos, [node]
                ),
            )
        except Exception as e:
            self.handle_error(f"Error generating takeaway: {str(e)}")
            
    def handle_takeaway_response(self, operation_id, response, node_pos, source_nodes):
        try:
            result = self._utility_result(operation_id, source_nodes, response)
            if result is None:
                return
            note_pos = QPointF(node_pos.x() + 400, node_pos.y())
            note = self.chat_view.scene().add_note(note_pos)
            note.width, note.content = 400, response
            note.color, note.header_color = get_current_palette().FRAME_COLORS["Mid Gray"]["color"], get_semantic_color("status_info").name()
            self._decorate_utility_note(note, result, UtilityKind.TAKEAWAY, source_nodes)
            note._recalculate_geometry()
            self.save_chat()
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
            texts = self._utility_bounded_texts(selected_nodes)
            if len(texts) < 2:
                self.notification_banner.show_message("Selected context exceeds the utility limit; select fewer sources.", 5000, "warning")
                return
            avg_x, max_x, avg_y = 0, 0, 0
            for node in selected_nodes:
                pos = node.scenePos()
                avg_x += pos.x()
                max_x = max(max_x, pos.x() + node.width)
                avg_y += pos.y()
            note_pos = QPointF(max_x + 100, avg_y / len(selected_nodes))
            self._show_loading_animation(scene_pos=QPointF(note_pos.x() - 50, note_pos.y()))
            self.group_summary_thread = GroupSummaryWorkerThread(GroupSummaryAgent(), texts, note_pos, selected_nodes)
            self._utility_start(
                UtilityKind.GROUP_SUMMARY, selected_nodes, self.group_summary_thread,
                lambda operation_id: lambda response, result_pos, result_sources: self.handle_group_summary_response(
                    operation_id, response, result_pos, result_sources
                ),
            )
        except Exception as e:
            self.handle_error(f"Error generating group summary: {str(e)}")

    def handle_group_summary_response(self, operation_id, response, note_pos, source_nodes):
        try:
            result = self._utility_result(operation_id, source_nodes, response)
            if result is None:
                return
            scene = self.chat_view.scene()
            note = scene.add_note(note_pos)
            note.content, note.color, note.header_color = response, get_current_palette().FRAME_COLORS["Mid Gray"]["color"], get_semantic_color("status_warning").name()
            note.width, note.is_summary_note = 450, True
            self._decorate_utility_note(note, result, UtilityKind.GROUP_SUMMARY, source_nodes)
            note._recalculate_geometry()
            self.save_chat()
        except Exception as e:
            self.handle_error(f"Error creating summary note: {str(e)}")
        finally:
            self._clear_loading_animation()

    def generate_explainer(self, node):
        try:
            self._show_loading_animation(anchor_node=node)
            self.explainer_thread = ExplainerWorkerThread(ExplainerAgent(), self._utility_bounded_text(node), node.scenePos())
            self._utility_start(
                UtilityKind.EXPLAINER, [node], self.explainer_thread,
                lambda operation_id: lambda response, node_pos: self.handle_explainer_response(
                    operation_id, response, node_pos, [node]
                ),
            )
        except Exception as e:
            self.handle_error(f"Error generating explanation: {str(e)}")
            
    def handle_explainer_response(self, operation_id, response, node_pos, source_nodes):
        try:
            result = self._utility_result(operation_id, source_nodes, response)
            if result is None:
                return
            note_pos = QPointF(node_pos.x() + 400, node_pos.y() + 100)
            note = self.chat_view.scene().add_note(note_pos)
            note.width, note.content = 400, response
            note.color, note.header_color = get_current_palette().FRAME_COLORS["Mid Gray"]["color"], get_semantic_color("status_info").name()
            self._decorate_utility_note(note, result, UtilityKind.EXPLAINER, source_nodes)
            note._recalculate_geometry()
            self.save_chat()
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
        if hasattr(node, "to_context_text"):
            add_fragment("Structured Chart Data", node.to_context_text())

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
            # Phase 7 prerequisite (increment 5): PyCoderNode/CodeSandboxNode now
            # also expose plain self.prompt/self.code mirror attributes (for
            # serializers.py, not for this function), which this generic
            # bare-attribute probe - originally meant for other node types like
            # ImageNode.prompt/CodeNode.code - would otherwise ALSO pick up,
            # duplicating the get_prompt()/get_code() fragment added below.
            # Skip the bare read when a dedicated getter exists; the getter's
            # own fragment covers that content instead.
            if attr_name in ("prompt", "code") and hasattr(node, f"get_{attr_name}"):
                continue
            add_fragment(label, getattr(node, attr_name, ""))

        if hasattr(node, "get_prompt"):
            add_fragment("Prompt Input", node.get_prompt())
        if hasattr(node, "get_code"):
            add_fragment("Editable Code", node.get_code())
        if hasattr(node, "get_output"):
            add_fragment("Execution Output", node.get_output())
        if hasattr(node, "get_ai_analysis"):
            add_fragment("AI Analysis", node.get_ai_analysis())
        if hasattr(node, "get_instruction"):
            add_fragment("Instructions", node.get_instruction())
        if hasattr(node, "get_artifact_content"):
            add_fragment("Artifact Content", node.get_artifact_content())
        if hasattr(node, "get_html_content"):
            add_fragment("Rendered HTML", node.get_html_content())

        # Phase 7 prerequisite (increment 5): prompt_input/output_display/
        # ai_analysis_display/instruction_input/raw_editor/generated_code_display
        # are now all covered by the getter-based fragments above (get_prompt,
        # get_output, get_ai_analysis, get_instruction, get_artifact_content) -
        # reading them again here duplicated content, and for ai_analysis_display
        # specifically reproduced the exact rendered-HTML plaintext-extraction
        # data-loss bug this increment's serializer fix eliminated elsewhere
        # (adversarial review caught this: a real, reachable, untested path).
        for label, widget_name in (
            ("Query", "query_input"),
            ("Plan Display", "plan_display"),
            ("Summary Display", "summary_display"),
            ("Thought Display", "thought_process_display"),
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
            self._chart_generation_token = getattr(self, "_chart_generation_token", 0) + 1
            request_token = self._chart_generation_token
            source_scene = node.scene()
            thread = ChartWorkerThread(chart_source_text, chart_type)
            self.chart_thread = thread
            thread.finished.connect(
                lambda data, emitted_chart_type, source_node=node, origin_scene=source_scene, token=request_token:
                    self.handle_chart_data(data, emitted_chart_type, source_node, token, origin_scene)
            )
            thread.error.connect(
                lambda message, token=request_token: self._handle_chart_error(message, token)
            )
            thread.finished.connect(thread.deleteLater)
            thread.error.connect(thread.deleteLater)
            thread.start()
        except Exception as e:
            self.handle_error(f"Error generating chart: {str(e)}")

    def invalidate_chart_requests(self):
        """Invalidate results from chart workers that belong to another chat."""
        self._chart_generation_token = getattr(self, "_chart_generation_token", 0) + 1

    def _handle_chart_error(self, message, request_token):
        if request_token == getattr(self, "_chart_generation_token", request_token):
            self.handle_error(message)

    def handle_chart_data(self, data, chart_type, source_node=None, request_token=None, source_scene=None):
        is_current_request = request_token is None or request_token == getattr(self, "_chart_generation_token", request_token)
        try:
            if not is_current_request:
                return
            chart_data = json.loads(data)
            if "error" in chart_data:
                self.notification_banner.show_message(chart_data["error"], 15000, "error")
                return
            scene = self.chat_view.scene()
            if source_node is None or (source_scene is not None and source_scene is not scene) or source_node.scene() is not scene:
                self.notification_banner.show_message(
                    "Chart generation finished after its source chat was closed; the result was discarded.",
                    8000,
                    "warning",
                )
                return
            chart_pos = QPointF(source_node.scenePos().x() + 450, source_node.scenePos().y())
            parent_node = scene.resolve_chart_parent(source_node)
            if parent_node is None:
                raise ValueError("The chart source is no longer attached to a conversational node.")
            chart = scene.add_chart(
                chart_data,
                chart_pos,
                parent_content_node=parent_node,
                source_node=source_node,
            )
            self.current_node = chart
            self.chat_view.reveal_item(chart)
            self.save_chat()
        except Exception as e:
            self.handle_error(f"Error creating chart: {str(e)}")
        finally:
            if is_current_request:
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

    def execute_html_view_node(self, html_node):
        """Window slot for HtmlViewNode.render_requested (Phase 7 prerequisite,
        increment 1). Unlike the other execute_* handlers, HTML rendering needs
        no worker thread or window-owned resource - the work is pure in-node
        Qt (web_view.setHtml). So this slot simply calls back into the node's
        own render_html(); the value is the request-signal SEAM itself (a
        window-mediated entry point a future web island's "Render" intent can
        target), not access to a window-only capability."""
        if html_node is not None and hasattr(html_node, "render_html"):
            html_node.render_html()

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

    def _handle_main_chat_error(self, error_message, worker_thread, request_id=None):
        if worker_thread is not self.chat_thread:
            return
        if request_id and not self.composer_controller.is_current(request_id):
            return
        if request_id:
            self.composer_controller.fail(request_id, error_message)
        self._set_main_request_state(active=False)
        self.handle_error(error_message)

    def _handle_main_chat_cancelled(self, worker_thread, request_id=None):
        if worker_thread is not self.chat_thread:
            return
        if request_id and not self.composer_controller.is_current(request_id):
            return
        if request_id:
            self.composer_controller.cancel(request_id)
        self._set_main_request_state(active=False)
        self._clear_loading_animation()
        self._clear_pending_response_preview()
        self.message_input.set_editor_enabled(True)
        self.save_chat()
        self.notification_banner.show_message("Request cancelled.", 3000, "info")

    def _handle_regeneration_cancelled(self, worker_thread):
        if worker_thread is not self.chat_thread:
            return
        self._set_main_request_state(active=False)
        self._clear_loading_animation()
        self.message_input.set_editor_enabled(True)
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
