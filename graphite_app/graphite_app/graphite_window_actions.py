import os
import re
import json
from PySide6.QtCore import QPointF
import graphite_config as config
import api_provider
from graphite_prompts import _TokenBytesEncoder
from graphite_widgets import LoadingAnimation
from graphite_node import ChatNode, CodeNode
from graphite_canvas_items import Note
from graphite_connections import GroupSummaryConnectionItem
from graphite_pycoder import PyCoderMode, PyCoderNode
from graphite_plugin_code_sandbox import CodeSandboxNode
from graphite_web import WebNode
from graphite_conversation_node import ConversationNode
from graphite_reasoning import ReasoningNode
from graphite_html_view import HtmlViewNode
from graphite_plugin_artifact import ArtifactNode
from graphite_plugin_workflow import WorkflowNode, WorkflowWorkerThread
from graphite_plugin_graph_diff import GraphDiffNode, GraphDiffWorkerThread
from graphite_plugin_quality_gate import QualityGateNode, QualityGateWorkerThread
from graphite_plugin_code_review import CodeReviewNode, CodeReviewWorkerThread
from graphite_plugin_gitlink import GitlinkNode, GitlinkWorkerThread
from graphite_config import get_current_palette
from graphite_memory import (
    append_history,
    assign_history,
    get_node_history,
    history_to_transcript,
    resolve_branch_parent,
    trim_history,
)
from graphite_agents import (
    ChatWorkerThread, KeyTakeawayWorkerThread, ExplainerWorkerThread, ChartWorkerThread,
    GroupSummaryWorkerThread, ImageGenerationWorkerThread, CodeExecutionWorker,
    PyCoderExecutionWorker, PyCoderExecutionAgent, PyCoderRepairAgent, PyCoderAnalysisAgent,
    PyCoderAgentWorker, SandboxStage, CodeSandboxExecutionWorker, WebWorkerThread, ReasoningWorkerThread,
    KeyTakeawayAgent, ExplainerAgent, GroupSummaryAgent, ImageGenerationAgent, ReasoningAgent
)

class WindowActionsMixin:
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

    def _looks_like_image_generation_request(self, message):
        text = (message or "").strip().lower()
        if not text:
            return False

        if text.endswith("?") or text.startswith(("how ", "what ", "why ", "can ", "could ", "should ", "would ", "do ")):
            return False

        image_verbs = ("generate", "create", "make", "render", "draw", "illustrate", "design")
        image_targets = (
            "image", "picture", "photo", "portrait", "illustration",
            "art", "artwork", "logo", "icon", "wallpaper", "poster"
        )
        return any(verb in text for verb in image_verbs) and any(target in text for target in image_targets)

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
        llm_content_parts = []
        
        if message:
            llm_content_parts.append({'type': 'text', 'text': user_node_text})

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
                    llm_content_parts.append({'type': 'image_bytes', 'data': image_bytes})
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

                self.chat_view.scene().add_document_node(title=file_name, content=doc_content, parent_user_node=user_node)
                llm_content_parts.append({
                    'type': 'text',
                    'text': self._wrap_attachment_xml(attachment, doc_content),
                })
            except IOError as e:
                self.handle_error(f"Could not read attachment '{attachment_path}': {e}")
                user_node.scene().delete_chat_node(user_node)
                return

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

        if self._looks_like_image_generation_request(message) and not attachments:
            self.current_node = user_node
            self.chat_view.centerOn(user_node)
            self.message_input.clear()
            self.message_input.setEnabled(True)
            self.send_button.setEnabled(True)
            self.attach_file_btn.setEnabled(True)
            self.clear_attachment()
            self.generate_image(user_node)
            self.save_chat()
            return

        self.loading_animation = LoadingAnimation()
        self.chat_view.scene().addItem(self.loading_animation)
        anim_pos = QPointF(user_node.pos().x() + user_node.width + 50, user_node.pos().y() + user_node.height / 2)
        self.loading_animation.setPos(anim_pos)
        self.loading_animation.start()

        self.chat_thread = ChatWorkerThread(self.agent, history_for_worker, history_context_node)
        self.chat_thread.finished.connect(lambda new_message: self.handle_response(new_message, user_node, history_for_worker))
        self.chat_thread.error.connect(self.handle_error)
        self.chat_thread.finished.connect(self.chat_thread.deleteLater)
        self.chat_thread.error.connect(self.chat_thread.deleteLater)
        self.chat_thread.start()

    def handle_response(self, new_assistant_message, user_node, history_before_assistant):
        if self.loading_animation:
            self.loading_animation.stop()
            self.chat_view.scene().removeItem(self.loading_animation)
            self.loading_animation = None

        full_history = append_history(history_before_assistant, [new_assistant_message])
        response_text = new_assistant_message['content']
        
        output_tokens = self.token_estimator.count_tokens(response_text)
        input_tokens_str = self.token_counter_widget.input_label.text().replace(',', '')
        input_tokens = int(input_tokens_str) if input_tokens_str.isdigit() else 0
        self.total_session_tokens += input_tokens + output_tokens
        self.token_counter_widget.update_counts(output_tokens=output_tokens, total_tokens=self.total_session_tokens)

        scene = self.chat_view.scene()
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
            ai_node = scene.add_chat_node(
                placeholder_text,
                is_user=False, 
                parent_node=user_node, 
                conversation_history=full_history
            )
        
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
        self.chat_view.centerOn(self.current_node)
        self.message_input.clear()
        self.message_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.attach_file_btn.setEnabled(True)
        self.clear_attachment()
        self.save_chat()

    def _parse_response(self, response_text):
        parts = []
        think_tag_pattern = re.compile(r"<(think|thinking)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
        fallback_reasoning_pattern = re.compile(r"--- REASONING ---\s*(.*?)\s*--- END REASONING ---", re.DOTALL | re.IGNORECASE)
        code_block_tag_pattern = re.compile(r"<code_block>([\s\S]*?)</code_block>", re.IGNORECASE)
        code_fence_pattern = re.compile(r"```(\w*)\s*\n?([\s\S]*?)\s*```")
        remaining_text = response_text
        thinking_match = think_tag_pattern.search(remaining_text)
        if thinking_match:
            thinking_content = thinking_match.group(2).strip()
            parts.append({'type': 'thinking', 'content': thinking_content})
            remaining_text = remaining_text.replace(thinking_match.group(0), "").strip()
        else:
            fallback_match = fallback_reasoning_pattern.search(remaining_text)
            if fallback_match:
                thinking_content = fallback_match.group(1).strip()
                parts.append({'type': 'thinking', 'content': thinking_content})
                remaining_text = remaining_text.replace(fallback_match.group(0), "").strip()
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
        self.loading_animation = LoadingAnimation()
        self.chat_view.scene().addItem(self.loading_animation)
        anim_pos = QPointF(node_to_regenerate.pos().x() + node_to_regenerate.width + 50, node_to_regenerate.pos().y() + node_to_regenerate.height / 2)
        self.loading_animation.setPos(anim_pos)
        self.loading_animation.start()
        self.chat_thread = ChatWorkerThread(self.agent, history_for_worker, node_to_regenerate.parent_node)
        self.chat_thread.finished.connect(lambda new_message: self.handle_regenerated_response(new_message, node_to_regenerate, history_for_worker))
        self.chat_thread.error.connect(self.handle_error)
        self.chat_thread.finished.connect(self.chat_thread.deleteLater)
        self.chat_thread.error.connect(self.chat_thread.deleteLater)
        self.chat_thread.start()

    def handle_regenerated_response(self, new_assistant_message, old_node, parent_history):
        try:
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
            self.chat_view.centerOn(last_created_node)
            self.save_chat()
        except Exception as e:
            self.handle_error(f"An error occurred during regeneration: {str(e)}")
        finally:
            if self.loading_animation:
                self.loading_animation.stop()
                self.chat_view.scene().removeItem(self.loading_animation)
                self.loading_animation = None
            self.message_input.setEnabled(True)
            self.send_button.setEnabled(True)
    
    def generate_takeaway(self, node):
        try:
            self.loading_animation = LoadingAnimation()
            self.chat_view.scene().addItem(self.loading_animation)
            anim_pos = QPointF(node.pos().x() + node.width + 50, node.pos().y() + node.height / 2)
            self.loading_animation.setPos(anim_pos)
            self.loading_animation.start()
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
            note.color, note.header_color = "#2d2d2d", "#2ecc71"
            note._recalculate_geometry()
        except Exception as e:
            self.handle_error(f"Error creating takeaway note: {str(e)}")
        finally:
            if self.loading_animation:
                self.loading_animation.stop()
                self.chat_view.scene().removeItem(self.loading_animation)
                self.loading_animation = None

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
            self.loading_animation = LoadingAnimation()
            scene.addItem(self.loading_animation)
            self.loading_animation.setPos(QPointF(note_pos.x() - 50, note_pos.y()))
            self.loading_animation.start()
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
            note.content, note.color, note.header_color = response, "#2d2d2d", "#e67e22"
            note.width, note.is_summary_note = 450, True
            note._recalculate_geometry()
            for source_node in source_nodes:
                if source_node.scene() == scene:
                    conn = GroupSummaryConnectionItem(source_node, note)
                    scene.addItem(conn)
                    scene.group_summary_connections.append(conn)
        except Exception as e:
            self.handle_error(f"Error creating summary note: {str(e)}")
        finally:
            if self.loading_animation:
                self.loading_animation.stop()
                scene.removeItem(self.loading_animation)
                self.loading_animation = None

    def generate_explainer(self, node):
        try:
            self.loading_animation = LoadingAnimation()
            self.chat_view.scene().addItem(self.loading_animation)
            anim_pos = QPointF(node.pos().x() + node.width + 50, node.pos().y() + node.height / 2)
            self.loading_animation.setPos(anim_pos)
            self.loading_animation.start()
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
            note.color, note.header_color = "#2d2d2d", "#9b59b6"
            note._recalculate_geometry()
        except Exception as e:
            self.handle_error(f"Error creating explainer note: {str(e)}")
        finally:
            if self.loading_animation:
                self.loading_animation.stop()
                self.chat_view.scene().removeItem(self.loading_animation)
                self.loading_animation = None

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
            self.loading_animation = LoadingAnimation()
            self.chat_view.scene().addItem(self.loading_animation)
            anim_pos = QPointF(node.pos().x() + node.width + 50, node.pos().y() + node.height / 2)
            self.loading_animation.setPos(anim_pos)
            self.loading_animation.start()
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
            if source_node and source_node.scene():
                chart_pos = QPointF(source_node.scenePos().x() + 450, source_node.scenePos().y())
            elif self.current_node and self.current_node.scene():
                chart_pos = QPointF(self.current_node.scenePos().x() + 450, self.current_node.scenePos().y())
            else:
                chart_pos = QPointF(0, 0)
            self.chat_view.scene().add_chart(chart_data, chart_pos, parent_content_node=source_node)
        except Exception as e:
            self.handle_error(f"Error creating chart: {str(e)}")
        finally:
            if self.loading_animation:
                self.loading_animation.stop()
                self.chat_view.scene().removeItem(self.loading_animation)
                self.loading_animation = None

    def generate_image(self, node):
        try:
            prompt = node.text
            if not prompt:
                self.notification_banner.show_message("The selected node has no text to use as a prompt.", 5000, "warning")
                return
            self.loading_animation = LoadingAnimation()
            self.chat_view.scene().addItem(self.loading_animation)
            anim_pos = QPointF(node.pos().x() + node.width + 50, node.pos().y() + node.height / 2)
            self.loading_animation.setPos(anim_pos)
            self.loading_animation.start()
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
            self.chat_view.centerOn(ai_node)
            self.save_chat()
        except Exception as e:
            self.handle_error(f"Failed to display generated image: {e}")
        finally:
            if self.loading_animation:
                self.loading_animation.stop()
                self.chat_view.scene().removeItem(self.loading_animation)
                self.loading_animation = None

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
            self.code_exec_thread = CodeExecutionWorker(code, pycoder_node.repl)
            self.code_exec_thread.finished.connect(lambda output: self._handle_code_execution_result(output, pycoder_node))
            self.code_exec_thread.error.connect(lambda error_msg: self._handle_pycoder_error(error_msg, pycoder_node))
            self.code_exec_thread.finished.connect(self.code_exec_thread.deleteLater)
            self.code_exec_thread.error.connect(self.code_exec_thread.deleteLater)
            self.code_exec_thread.start()
        elif pycoder_node.mode == PyCoderMode.AI_DRIVEN:
            prompt = pycoder_node.get_prompt()
            if not prompt.strip():
                pycoder_node.set_ai_analysis("Please enter a prompt.")
                pycoder_node.set_running_state(False)
                return
            pycoder_node.reset_statuses(); pycoder_node.set_code(""); pycoder_node.set_output(""); pycoder_node.set_ai_analysis("")
            context_node = pycoder_node.parent_node
            if isinstance(context_node, CodeNode): context_node = context_node.parent_content_node
            history = get_node_history(context_node)
            self.pycoder_exec_thread = PyCoderExecutionWorker(prompt, history, pycoder_node.repl)
            self.pycoder_exec_thread.log_update.connect(pycoder_node.update_status)
            self.pycoder_exec_thread.finished.connect(lambda result: self._handle_ai_pycoder_result(result, pycoder_node))
            self.pycoder_exec_thread.error.connect(lambda error_msg: self._handle_pycoder_error(error_msg, pycoder_node))
            self.pycoder_exec_thread.finished.connect(self.pycoder_exec_thread.deleteLater)
            self.pycoder_exec_thread.error.connect(self.pycoder_exec_thread.deleteLater)
            self.pycoder_exec_thread.start()

    def stop_pycoder_node(self, pycoder_node):
        if hasattr(self, 'code_exec_thread') and self.code_exec_thread and self.code_exec_thread.isRunning():
            self.code_exec_thread.stop()
        if hasattr(self, 'pycoder_exec_thread') and self.pycoder_exec_thread and self.pycoder_exec_thread.isRunning():
            self.pycoder_exec_thread.stop()
            
        pycoder_node.set_running_state(False)
        pycoder_node.set_ai_analysis("Execution manually stopped.")
        
    def _handle_code_execution_result(self, output, pycoder_node):
        pycoder_node.set_output(output)
        code = pycoder_node.get_code()
        
        parent_history = get_node_history(pycoder_node.parent_node)
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

    def _handle_ai_pycoder_result(self, result_dict, pycoder_node):
        analysis_text = result_dict.get('analysis', '')
        code = result_dict.get('code', '')
        output = result_dict.get('output', '')
        prompt = pycoder_node.get_prompt()
        
        # We bundle the generated code, execution output, and analysis into the history.
        assistant_msg = f"--- GENERATED CODE ---\n```python\n{code}\n```\n\n--- EXECUTION OUTPUT ---\n{output}\n\n--- ANALYSIS ---\n{analysis_text}"
        
        parent_history = get_node_history(pycoder_node.parent_node)

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

        parent_history = get_node_history(sandbox_node.parent_node)
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
        self.sandbox_thread = worker_thread
        sandbox_node.worker_thread = worker_thread

        worker_thread.log_update.connect(sandbox_node.update_status)
        worker_thread.terminal_chunk.connect(sandbox_node.append_terminal_output)
        worker_thread.finished.connect(
            lambda result, node=sandbox_node, history=parent_history, mode=run_mode: self._handle_code_sandbox_result(result, node, history, mode)
        )
        worker_thread.error.connect(lambda error_msg, node=sandbox_node: self._handle_code_sandbox_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=sandbox_node: self._cleanup_code_sandbox_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=sandbox_node: self._cleanup_code_sandbox_thread(thread, node))
        worker_thread.start()

    def stop_code_sandbox_node(self, sandbox_node):
        worker_thread = getattr(sandbox_node, "worker_thread", None)
        if worker_thread and worker_thread.isRunning():
            worker_thread.stop()
        if getattr(self, "sandbox_thread", None) and self.sandbox_thread.isRunning():
            self.sandbox_thread.stop()
        sandbox_node.worker_thread = None
        if getattr(self, "sandbox_thread", None) is worker_thread:
            self.sandbox_thread = None

        sandbox_node.append_terminal_output("\n[Sandbox] Execution manually stopped.\n")
        sandbox_node.status = "Stopped"
        sandbox_node.set_running_state(False)
        sandbox_node.set_error("Sandbox execution was manually stopped.")

    def _cleanup_code_sandbox_thread(self, worker_thread, sandbox_node):
        if sandbox_node and getattr(sandbox_node, "worker_thread", None) is worker_thread:
            sandbox_node.worker_thread = None
        if getattr(self, "sandbox_thread", None) is worker_thread:
            self.sandbox_thread = None
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
                "Execute the following code inside the isolated sandbox.\n\n"
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
        parent_history = get_node_history(parent_node)
        history, _ = trim_history(
            parent_history,
            self.token_estimator,
            max_tokens=7000,
            system_prompt_estimate=1000,
        )
        self.web_worker_thread = WebWorkerThread(query, history)
        self.web_worker_thread.update_status.connect(lambda status, node=web_node: self._handle_web_worker_status(status, node))
        self.web_worker_thread.finished.connect(lambda result, node=web_node: self._handle_web_worker_finished(result, node))
        self.web_worker_thread.error.connect(lambda error, node=web_node: self._handle_web_worker_error(error, node))
        self.web_worker_thread.finished.connect(self.web_worker_thread.deleteLater)
        self.web_worker_thread.error.connect(self.web_worker_thread.deleteLater)
        self.web_worker_thread.start()

    def _handle_web_worker_status(self, status, node):
        if node and node.scene(): node.set_status(status)

    def _handle_web_worker_finished(self, result, node):
        if node and node.scene():
            node.set_result(result['summary'], result['sources']); node.set_running_state(False); self.save_chat()

    def _handle_web_worker_error(self, error_message, node):
        if node and node.scene():
            node.set_error(error_message); node.set_running_state(False)

    def execute_reasoning_node(self, reasoning_node):
        prompt = reasoning_node.prompt.strip()
        if not prompt:
            reasoning_node.set_error("Prompt cannot be empty."); return
        reasoning_node.set_running_state(True); reasoning_node.clear_thoughts()
        parent_history = get_node_history(reasoning_node.parent_node)
        trimmed_parent_history, _ = trim_history(
            parent_history,
            self.token_estimator,
            max_tokens=6000,
            system_prompt_estimate=1200,
        )
        branch_context = history_to_transcript(trimmed_parent_history, max_messages=8, max_chars_per_message=700)
        self.reasoning_thread = ReasoningWorkerThread(
            ReasoningAgent(),
            original_prompt=prompt,
            budget=reasoning_node.thinking_budget,
            branch_context=branch_context,
        )
        self.reasoning_thread.step_finished.connect(lambda title, text, node=reasoning_node: self._handle_reasoning_step(title, text, node))
        self.reasoning_thread.finished.connect(lambda answer, node=reasoning_node, history=parent_history: self._handle_reasoning_finished(answer, node, history))
        self.reasoning_thread.error.connect(lambda error, node=reasoning_node: self._handle_reasoning_error(error, node))
        self.reasoning_thread.finished.connect(self.reasoning_thread.deleteLater)
        self.reasoning_thread.error.connect(self.reasoning_thread.deleteLater)
        self.reasoning_thread.start()

    def _handle_reasoning_step(self, title, text, node):
        if node and node.scene(): node.set_status(title); node.append_thought(title, text)

    def _handle_reasoning_finished(self, final_answer, node, parent_history):
        if node and node.scene():
            node.set_final_answer(final_answer, parent_history=parent_history)
            node.set_running_state(False)
            self.save_chat()

    def _handle_reasoning_error(self, error_message, node):
        if node and node.scene(): node.set_error(error_message)

    def handle_conversation_node_request(self, requesting_node, history):
        requesting_node.set_typing(True)
        self.conversation_node_thread = ChatWorkerThread(self.agent, history, requesting_node.parent_node)
        self.conversation_node_thread.finished.connect(lambda new_message: self.handle_conversation_node_response(new_message, requesting_node))
        self.conversation_node_thread.error.connect(lambda error_msg: self.handle_conversation_node_error(error_msg, requesting_node))
        self.conversation_node_thread.finished.connect(self.conversation_node_thread.deleteLater)
        self.conversation_node_thread.error.connect(lambda: requesting_node.set_typing(False))
        self.conversation_node_thread.start()

    def handle_conversation_node_response(self, new_message, target_node):
        target_node.set_typing(False)
        if target_node and target_node.scene():
            response_text = new_message.get('content', '')
            target_node.add_ai_message(response_text); self.save_chat()

    def handle_conversation_node_error(self, error_message, target_node):
        target_node.set_typing(False)
        self.notification_banner.show_message(f"An error occurred: {error_message}", 8000, "error")
        if target_node and target_node.scene():
            target_node.set_input_enabled(True); target_node.add_ai_message(f"[ERROR]: Could not get response. {error_message}")

    def execute_artifact_node(self, artifact_node):
        """Starts the ArtifactAgent workflow."""
        instruction = artifact_node.get_instruction()
        if not instruction.strip():
            artifact_node.set_running_state(False)
            return

        artifact_node.set_running_state(True)
        artifact_node.add_chat_message(instruction, is_user=True)
        
        parent_history = get_node_history(artifact_node.parent_node)
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

        from graphite_plugin_artifact import ArtifactWorkerThread
        self.artifact_thread = ArtifactWorkerThread(current_doc, trimmed_history)
        self.artifact_thread.finished.connect(lambda doc, msg, node=artifact_node: self._handle_artifact_result(doc, msg, node))
        self.artifact_thread.error.connect(lambda err, node=artifact_node: self._handle_artifact_error(err, node))
        self.artifact_thread.finished.connect(self.artifact_thread.deleteLater)
        self.artifact_thread.error.connect(self.artifact_thread.deleteLater)
        self.artifact_thread.start()

    def _handle_artifact_result(self, new_doc, ai_msg, artifact_node):
        """Processes the finished document and commentary from the Artifact thread."""
        artifact_node.set_artifact_content(new_doc)
        if ai_msg:
            artifact_node.add_chat_message(ai_msg, is_user=False)
        artifact_node.set_running_state(False)
        
        parent_history = get_node_history(artifact_node.parent_node)
        assign_history(artifact_node, append_history(parent_history, artifact_node.local_history))
        
        self.save_chat()

    def _handle_artifact_error(self, error_msg, artifact_node):
        """Handles an error within the Artifact thread."""
        artifact_node.add_chat_message(f"Error: {error_msg}", is_user=False)
        artifact_node.set_running_state(False)

    def execute_workflow_node(self, workflow_node):
        goal = workflow_node.get_goal().strip()
        constraints = workflow_node.get_constraints().strip()
        if not goal:
            workflow_node.set_error("Mission cannot be empty.")
            return

        workflow_node.set_running_state(True)
        parent_history = get_node_history(workflow_node.parent_node)
        trimmed_parent_history, _ = trim_history(
            parent_history,
            self.token_estimator,
            max_tokens=6500,
            system_prompt_estimate=1200,
        )
        self.workflow_thread = WorkflowWorkerThread(goal, constraints, trimmed_parent_history)
        self.workflow_thread.finished.connect(lambda result, node=workflow_node, history=parent_history: self._handle_workflow_result(result, node, history))
        self.workflow_thread.error.connect(lambda error_msg, node=workflow_node: self._handle_workflow_error(error_msg, node))
        self.workflow_thread.finished.connect(self.workflow_thread.deleteLater)
        self.workflow_thread.error.connect(self.workflow_thread.deleteLater)
        self.workflow_thread.start()

    def _handle_workflow_result(self, result, workflow_node, parent_history):
        if not workflow_node or not workflow_node.scene():
            return
        workflow_node.set_plan(result)
        workflow_node.set_running_state(False)

        request_text = workflow_node.get_goal().strip()
        constraints = workflow_node.get_constraints().strip()
        if constraints:
            request_text = f"{request_text}\n\nConstraints:\n{constraints}"

        assign_history(workflow_node, append_history(parent_history, [
            {'role': 'user', 'content': request_text},
            {'role': 'assistant', 'content': result.get('blueprint_markdown', '')}
        ]))

        self.setCurrentNode(workflow_node)
        self.save_chat()

    def _handle_workflow_error(self, error_message, workflow_node):
        if not workflow_node or not workflow_node.scene():
            return
        workflow_node.set_error(error_message)
        workflow_node.set_running_state(False)

    def execute_quality_gate_node(self, quality_gate_node):
        if not quality_gate_node or getattr(quality_gate_node, "is_disposed", False) or not quality_gate_node.scene():
            return

        goal = quality_gate_node.get_goal().strip()
        criteria = quality_gate_node.get_criteria().strip()
        if not goal:
            quality_gate_node.set_error("Target outcome cannot be empty.")
            return

        quality_gate_node.refresh_branch_context()
        payload = quality_gate_node.get_review_payload()
        quality_gate_node.set_running_state(True)

        worker_thread = QualityGateWorkerThread(goal, criteria, payload)
        self.quality_gate_thread = worker_thread
        quality_gate_node.worker_thread = worker_thread

        worker_thread.finished.connect(lambda result, node=quality_gate_node: self._handle_quality_gate_result(result, node))
        worker_thread.error.connect(lambda error_msg, node=quality_gate_node: self._handle_quality_gate_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=quality_gate_node: self._cleanup_quality_gate_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=quality_gate_node: self._cleanup_quality_gate_thread(thread, node))
        worker_thread.start()

    def _cleanup_quality_gate_thread(self, worker_thread, quality_gate_node):
        if quality_gate_node and getattr(quality_gate_node, "worker_thread", None) is worker_thread:
            quality_gate_node.worker_thread = None
        if getattr(self, "quality_gate_thread", None) is worker_thread:
            self.quality_gate_thread = None
        worker_thread.deleteLater()

    def _handle_quality_gate_result(self, result, quality_gate_node):
        if not quality_gate_node or getattr(quality_gate_node, "is_disposed", False) or not quality_gate_node.scene():
            return

        quality_gate_node.set_review(result)
        quality_gate_node.set_running_state(False)

        request_text = quality_gate_node.get_goal().strip()
        criteria = quality_gate_node.get_criteria().strip()
        if criteria:
            request_text = f"{request_text}\n\nAcceptance Criteria:\n{criteria}"

        parent_history = get_node_history(quality_gate_node.parent_node)
        assign_history(quality_gate_node, append_history(parent_history, [
            {'role': 'user', 'content': request_text},
            {'role': 'assistant', 'content': result.get('review_markdown', '')}
        ]))

        self.setCurrentNode(quality_gate_node)
        self.save_chat()

    def _handle_quality_gate_error(self, error_message, quality_gate_node):
        if not quality_gate_node or getattr(quality_gate_node, "is_disposed", False) or not quality_gate_node.scene():
            return
        quality_gate_node.set_error(error_message)
        quality_gate_node.set_running_state(False)

    def execute_code_review_node(self, code_review_node):
        if not code_review_node or getattr(code_review_node, "is_disposed", False) or not code_review_node.scene():
            return

        payload = code_review_node.build_review_payload()
        if not payload.get("source_text", "").strip():
            code_review_node.set_error("Load or paste source code before running the review.")
            return

        code_review_node.refresh_github_state()
        code_review_node.set_running_state(True)

        worker_thread = CodeReviewWorkerThread(payload)
        self.code_review_thread = worker_thread
        code_review_node.worker_thread = worker_thread

        worker_thread.finished.connect(lambda result, node=code_review_node: self._handle_code_review_result(result, node))
        worker_thread.error.connect(lambda error_msg, node=code_review_node: self._handle_code_review_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=code_review_node: self._cleanup_code_review_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=code_review_node: self._cleanup_code_review_thread(thread, node))
        worker_thread.start()

    def _cleanup_code_review_thread(self, worker_thread, code_review_node):
        if code_review_node and getattr(code_review_node, "worker_thread", None) is worker_thread:
            code_review_node.worker_thread = None
        if getattr(self, "code_review_thread", None) is worker_thread:
            self.code_review_thread = None
        worker_thread.deleteLater()

    def _handle_code_review_result(self, result, code_review_node):
        if not code_review_node or getattr(code_review_node, "is_disposed", False) or not code_review_node.scene():
            return

        code_review_node.set_review(result)
        code_review_node.set_running_state(False)

        source_state = getattr(code_review_node, "source_state", {}) or {}
        source_label = source_state.get("path") or source_state.get("local_path") or source_state.get("label") or "loaded source"
        request_text = f"Run deterministic code review for: {source_label}"
        review_context = code_review_node.get_review_context().strip()
        if review_context:
            request_text = f"{request_text}\n\nContext:\n{review_context}"

        parent_history = get_node_history(code_review_node.parent_node)
        assign_history(code_review_node, append_history(parent_history, [
            {'role': 'user', 'content': request_text},
            {'role': 'assistant', 'content': result.get('review_markdown', '')}
        ]))

        self.setCurrentNode(code_review_node)
        self.save_chat()

    def _handle_code_review_error(self, error_message, code_review_node):
        if not code_review_node or getattr(code_review_node, "is_disposed", False) or not code_review_node.scene():
            return
        code_review_node.set_error(error_message)
        code_review_node.set_running_state(False)

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
        self.gitlink_thread = worker_thread
        gitlink_node.worker_thread = worker_thread

        worker_thread.finished.connect(lambda result, node=gitlink_node, history=parent_history: self._handle_gitlink_result(result, node, history))
        worker_thread.error.connect(lambda error_msg, node=gitlink_node: self._handle_gitlink_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=gitlink_node: self._cleanup_gitlink_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=gitlink_node: self._cleanup_gitlink_thread(thread, node))
        worker_thread.start()

    def _cleanup_gitlink_thread(self, worker_thread, gitlink_node):
        if gitlink_node and getattr(gitlink_node, "worker_thread", None) is worker_thread:
            gitlink_node.worker_thread = None
        if getattr(self, "gitlink_thread", None) is worker_thread:
            self.gitlink_thread = None
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

    def execute_graph_diff_node(self, graph_diff_node):
        if not graph_diff_node or getattr(graph_diff_node, "is_disposed", False) or not graph_diff_node.scene():
            return

        left_payload, right_payload = graph_diff_node.get_comparison_payloads()
        graph_diff_node.set_running_state(True)

        worker_thread = GraphDiffWorkerThread(left_payload, right_payload)
        self.graph_diff_thread = worker_thread
        graph_diff_node.worker_thread = worker_thread

        worker_thread.finished.connect(lambda result, node=graph_diff_node: self._handle_graph_diff_result(result, node))
        worker_thread.error.connect(lambda error_msg, node=graph_diff_node: self._handle_graph_diff_error(error_msg, node))
        worker_thread.finished.connect(lambda _result, thread=worker_thread, node=graph_diff_node: self._cleanup_graph_diff_thread(thread, node))
        worker_thread.error.connect(lambda _error, thread=worker_thread, node=graph_diff_node: self._cleanup_graph_diff_thread(thread, node))
        worker_thread.start()

    def _cleanup_graph_diff_thread(self, worker_thread, graph_diff_node):
        if graph_diff_node and getattr(graph_diff_node, "worker_thread", None) is worker_thread:
            graph_diff_node.worker_thread = None
        if getattr(self, "graph_diff_thread", None) is worker_thread:
            self.graph_diff_thread = None
        worker_thread.deleteLater()

    def _handle_graph_diff_result(self, result, graph_diff_node):
        if not graph_diff_node or getattr(graph_diff_node, "is_disposed", False) or not graph_diff_node.scene():
            return
        graph_diff_node.set_result(result)
        graph_diff_node.set_running_state(False)
        self.setCurrentNode(graph_diff_node)
        self.save_chat()

    def _handle_graph_diff_error(self, error_message, graph_diff_node):
        if not graph_diff_node or getattr(graph_diff_node, "is_disposed", False) or not graph_diff_node.scene():
            return
        graph_diff_node.set_error(error_message)
        graph_diff_node.set_running_state(False)

    def create_graph_diff_note(self, graph_diff_node):
        if not graph_diff_node or getattr(graph_diff_node, "is_disposed", False) or not graph_diff_node.scene():
            return

        scene = self.chat_view.scene()
        note_pos = QPointF(graph_diff_node.pos().x() + graph_diff_node.width + 80, graph_diff_node.pos().y())
        note = scene.add_note(note_pos)
        note.is_summary_note = True
        note.content = graph_diff_node.note_summary or graph_diff_node.comparison_markdown or "Graph diff summary unavailable."
        note.width = 340
        note.color = "#2d2d2d"
        note.header_color = get_current_palette().FRAME_COLORS["Orange"]["color"]
        if hasattr(note, "_recalculate_geometry"):
            note._recalculate_geometry()
        self.save_chat()

    def create_quality_gate_note(self, quality_gate_node):
        if not quality_gate_node or getattr(quality_gate_node, "is_disposed", False) or not quality_gate_node.scene():
            return

        scene = self.chat_view.scene()
        note_pos = QPointF(quality_gate_node.pos().x() + quality_gate_node.width + 80, quality_gate_node.pos().y())
        note = scene.add_note(note_pos)
        note.is_summary_note = True
        note.content = quality_gate_node.note_summary or quality_gate_node.review_markdown or "Quality gate summary unavailable."
        note.width = 360
        note.color = "#2d2d2d"
        note.header_color = get_current_palette().FRAME_COLORS["Yellow"]["color"]
        if hasattr(note, "_recalculate_geometry"):
            note._recalculate_geometry()
        self.save_chat()

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
        self.chat_view.centerOn(new_node)
        self.save_chat()

    def _seed_plugin_prompt(self, node, seed_prompt):
        if isinstance(node, WebNode):
            node.set_query(seed_prompt)
        elif isinstance(node, ReasoningNode):
            node.prompt_input.setPlainText(seed_prompt)
            node._on_prompt_changed()
        elif isinstance(node, PyCoderNode):
            node.prompt_input.setPlainText(seed_prompt)
        elif isinstance(node, CodeSandboxNode):
            node.prompt_input.setPlainText(seed_prompt)
        elif isinstance(node, ArtifactNode):
            node.instruction_input.setPlainText(seed_prompt)
        elif isinstance(node, ConversationNode):
            node.message_input.setText(seed_prompt)
        elif isinstance(node, HtmlViewNode):
            node.html_input.setPlainText(seed_prompt)
        elif isinstance(node, WorkflowNode):
            node.goal_input.setPlainText(seed_prompt)
        elif isinstance(node, QualityGateNode):
            node.goal_input.setPlainText(seed_prompt)
        elif isinstance(node, CodeReviewNode):
            node.context_input.setPlainText(seed_prompt)
            node._on_context_changed()
        elif isinstance(node, GitlinkNode):
            node.task_input.setPlainText(seed_prompt)
            node._on_task_changed()
        elif isinstance(node, Note):
            node.content = seed_prompt
            if hasattr(node, '_recalculate_geometry'):
                node._recalculate_geometry()
