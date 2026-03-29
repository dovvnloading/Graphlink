from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QCursor, QShortcut
from graphite_node import ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode
from graphite_pycoder import PyCoderNode
from graphite_plugin_code_sandbox import CodeSandboxNode
from graphite_web import WebNode
from graphite_conversation_node import ConversationNode
from graphite_reasoning import ReasoningNode
from graphite_html_view import HtmlViewNode
from graphite_plugin_artifact import ArtifactNode
from graphite_plugin_workflow import WorkflowNode
from graphite_plugin_quality_gate import QualityGateNode
from graphite_plugin_gitlink import GitlinkNode
from graphite_command_palette import CommandPaletteDialog

class WindowNavigationMixin:
    def _setup_commands(self):
        self.command_manager.register_command("New Chat", ["start new", "clear session"], self.new_chat)
        self.command_manager.register_command("Create Frame From Selection", ["group nodes", "frame selection"], self.chat_view.scene().createFrame, lambda: any(isinstance(item, (ChatNode, CodeNode, DocumentNode, ImageNode, ThinkingNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, ArtifactNode, WorkflowNode, QualityGateNode, GitlinkNode)) for item in self.chat_view.scene().selectedItems()))
        self.command_manager.register_command("Create Container From Selection", ["group nodes", "container selection"], self.chat_view.scene().createContainer, lambda: bool(self.chat_view.scene().selectedItems()))
        self.command_manager.register_command("Collapse All Nodes", ["fold all"], self._cmd_collapse_all, lambda: bool(self.chat_view.scene()._all_conversational_nodes()))
        self.command_manager.register_command("Expand All Nodes", ["unfold all"], self._cmd_expand_all, lambda: bool(self.chat_view.scene()._all_conversational_nodes()))
        self.command_manager.register_command("Delete Selected Items", ["delete", "remove selected"], self._cmd_delete_selected, lambda: bool(self.chat_view.scene().selectedItems()))
        self.command_manager.register_command("Organize Nodes (Tree Layout)", ["organize", "auto layout", "rearrange"], self.chat_view.scene().organize_nodes, lambda: bool(self.chat_view.scene()._all_conversational_nodes()))
        self.command_manager.register_command("Select All Nodes", ["select all"], self.chat_view.scene().selectAllNodes, lambda: bool(self.chat_view.scene()._all_layout_nodes()))
        self.command_manager.register_command("Fit All to View", ["fit screen", "zoom fit"], self.chat_view.fit_all, lambda: bool(self.chat_view.scene().items()))
        self.command_manager.register_command("Reset View", ["reset zoom", "default view"], self.chat_view.reset_zoom)
        self.command_manager.register_command("Focus on Selection", ["zoom to selection", "center selection"], self._cmd_focus_selection, lambda: bool(self.chat_view.scene().selectedItems()))
        self.command_manager.register_command("Add Note", ["create note", "new note"], self._cmd_add_note_center)
        self.command_manager.register_command("Add Web Search Node", ["web search", "internet"], self.plugin_portal._create_web_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add Reasoning Node", ["reasoning", "multi-step"], self.plugin_portal._create_reasoning_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add HTML Renderer Node", ["render html", "html preview"], self.plugin_portal._create_html_view_node, lambda: isinstance(self.current_node, (ChatNode, CodeNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add Artifact Drafter Node", ["artifact", "document drafter"], self.plugin_portal._create_artifact_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add Workflow Architect Node", ["workflow", "orchestrate plugins", "agentic planner"], self.plugin_portal._create_workflow_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add Quality Gate Node", ["quality gate", "acceptance review", "production readiness", "ship review"], self.plugin_portal._create_quality_gate_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add Execution Sandbox Node", ["sandbox", "isolated python", "requirements runner"], self.plugin_portal._create_code_sandbox_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Add Gitlink Node", ["gitlink", "repo context", "github repo"], self.plugin_portal._create_gitlink_node, lambda: isinstance(self.current_node, (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, ArtifactNode, QualityGateNode, GitlinkNode)))
        self.command_manager.register_command("Generate Key Takeaway", ["takeaway", "summarize node"], lambda: self.generate_takeaway(self._get_single_selected_node()), self._get_single_selected_node)
        self.command_manager.register_command("Generate Explainer Note", ["explain", "simplify node"], lambda: self.generate_explainer(self._get_single_selected_node()), self._get_single_selected_node)
        self.command_manager.register_command("Regenerate Response", ["regen", "new response"], lambda: self.regenerate_node(self._get_single_selected_node()), lambda: self._get_single_selected_node() and not self._get_single_selected_node().is_user)
        self.command_manager.register_command("Generate Image from Text", ["make image", "create image"], lambda: self.generate_image(self._get_single_selected_node()), self._get_single_selected_node)
        chart_types = [("Bar Chart", "bar"), ("Line Chart", "line"), ("Pie Chart", "pie"), ("Histogram", "histogram"), ("Sankey Diagram", "sankey")]
        for name, type_id in chart_types:
            self.command_manager.register_command(f"Generate {name}", [f"make {name.lower()}", f"create {name.lower()}"], lambda chart_type=type_id: self.generate_chart(self._get_single_selected_node(), chart_type), self._get_single_selected_node)

    def _cmd_collapse_all(self):
        for node in self.chat_view.scene()._all_conversational_nodes():
            if hasattr(node, 'set_collapsed'):
                node.set_collapsed(True)

    def _cmd_expand_all(self):
        for node in self.chat_view.scene()._all_conversational_nodes():
            if hasattr(node, 'set_collapsed'):
                node.set_collapsed(False)
    
    def _cmd_delete_selected(self):
        self.chat_view.scene().deleteSelectedItems()

    def _cmd_focus_selection(self):
        scene = self.chat_view.scene()
        if not scene.selectedItems(): return
        rect = scene.itemsBoundingRect()
        self.chat_view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _cmd_add_note_center(self):
        center_pos = self.chat_view.mapToScene(self.chat_view.viewport().rect().center())
        self.chat_view.scene().add_note(center_pos)

    def _navigate_to_node(self, node):
        if not node: return
        self.chat_view.scene().clearSelection()
        node.setSelected(True)
        self.setCurrentNode(node)
        self.chat_view.centerOn(node)

    def _navigate_up(self):
        current = self._get_single_selected_node()
        if current and current.parent_node: self._navigate_to_node(current.parent_node)

    def _navigate_down(self):
        current = self._get_single_selected_node()
        if current and current.children:
            sorted_children = sorted(current.children, key=lambda c: c.pos().x())
            self._navigate_to_node(sorted_children[0])

    def _navigate_left(self):
        current = self._get_single_selected_node()
        if current and current.parent_node:
            siblings = sorted(current.parent_node.children, key=lambda c: c.pos().x())
            try:
                current_index = siblings.index(current)
                if current_index > 0: self._navigate_to_node(siblings[current_index - 1])
            except ValueError: pass

    def _navigate_right(self):
        current = self._get_single_selected_node()
        if current and current.parent_node:
            siblings = sorted(current.parent_node.children, key=lambda c: c.pos().x())
            try:
                current_index = siblings.index(current)
                if current_index < len(siblings) - 1: self._navigate_to_node(siblings[current_index + 1])
            except ValueError: pass

    def show_command_palette(self):
        available_commands = self.command_manager.get_available_commands()
        dialog = CommandPaletteDialog(available_commands, self)
        parent_center = self.geometry().center()
        dialog.move(parent_center.x() - dialog.width() // 2, parent_center.y() - dialog.height() // 2 - 100)
        if dialog.exec():
            command = dialog.get_selected_command()
            if command and 'callback' in command:
                command['callback']()
