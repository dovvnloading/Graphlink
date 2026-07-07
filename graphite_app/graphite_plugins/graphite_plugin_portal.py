from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF

from graphite_connections import (
    SystemPromptConnectionItem, PyCoderConnectionItem, ConversationConnectionItem,
    ReasoningConnectionItem, HtmlConnectionItem
)
from graphite_config import get_current_palette
from graphite_pycoder import PyCoderNode
from graphite_plugins.graphite_plugin_code_sandbox import CodeSandboxNode, CodeSandboxConnectionItem
from graphite_node import ChatNode, CodeNode
from graphite_web import WebNode, WebConnectionItem
from graphite_conversation_node import ConversationNode
from graphite_reasoning import ReasoningNode
from graphite_html_view import HtmlViewNode
from graphite_plugins.graphite_plugin_workflow import WorkflowNode, WorkflowConnectionItem
from graphite_plugins.graphite_plugin_graph_diff import GraphDiffNode, GraphDiffConnectionItem
from graphite_plugins.graphite_plugin_quality_gate import QualityGateNode, QualityGateConnectionItem
from graphite_plugins.graphite_plugin_code_review import CodeReviewNode, CodeReviewConnectionItem
from graphite_plugins.graphite_plugin_gitlink import GitlinkNode, GitlinkConnectionItem
from graphite_plugins.graphite_plugin_artifact import ArtifactNode, ArtifactConnectionItem
from graphite_memory import clone_history


@dataclass(frozen=True)
class PluginSpec:
    """Declarative metadata for one plugin node type.

    Phase 1 of doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md: introduced additively, alongside the
    existing hand-written `_register_plugin` calls and `_create_X_node` factories on
    PluginPortal below, which are unchanged by this. Other hardcoded per-plugin metadata
    scattered around the app (graphite_plugin_quality_gate.py's `_node_label`,
    graphite_plugin_workflow.py's `WORKFLOW_PLUGIN_ICONS`/`WORKFLOW_ALLOWED_PLUGINS`,
    graphite_window_actions.py's `_seed_plugin_prompt` isinstance chain, the isinstance
    chains in graphite_scene.py/graphite_window.py, etc.) are candidates to migrate onto
    this table one plugin at a time in later phases - this phase only builds the table
    and does not rewire any existing consumer.
    """
    key: str
    display_name: str
    description: str
    category: str
    icon: str
    node_cls: Optional[type]
    connection_cls: Optional[type]
    # Whether graphite_window_actions.py's instantiate_seeded_plugin/_seed_plugin_prompt
    # flow can create this plugin from a single parent node plus a starter prompt. False
    # for plugins with a different creation contract (System Prompt attaches to the graph
    # root; Branch Lens/GraphDiffNode requires exactly two pre-selected existing nodes).
    seedable: bool


PLUGIN_REGISTRY: dict[str, "PluginSpec"] = {}


def _register_spec(**kwargs):
    spec = PluginSpec(**kwargs)
    PLUGIN_REGISTRY[spec.key] = spec
    return spec


_register_spec(
    key="system_prompt", display_name="System Prompt",
    description="Adds a special node to override the default system prompt for a conversation branch.",
    category="Branch Foundations", icon="fa5s.sliders-h",
    node_cls=None, connection_cls=SystemPromptConnectionItem, seedable=False,
)
_register_spec(
    key="conversation", display_name="Conversation Node",
    description="Adds a node for a self-contained, linear chat conversation.",
    category="Branch Foundations", icon="fa5s.comments",
    node_cls=ConversationNode, connection_cls=ConversationConnectionItem, seedable=True,
)
_register_spec(
    key="reasoning", display_name="Graphlink-Reasoning",
    description="A multi-step reasoning agent for solving complex problems.",
    category="Reasoning & Research", icon="fa5s.brain",
    node_cls=ReasoningNode, connection_cls=ReasoningConnectionItem, seedable=True,
)
_register_spec(
    key="web", display_name="Graphlink-Web",
    description="Adds a node with web access for real-time information retrieval.",
    category="Reasoning & Research", icon="fa5s.globe",
    node_cls=WebNode, connection_cls=WebConnectionItem, seedable=True,
)
_register_spec(
    key="graph_diff", display_name="Branch Lens",
    description="Compares two selected graph branches side-by-side and highlights where their logic, code, and intent diverge.",
    category="Validation & Delivery", icon="fa5s.code-branch",
    node_cls=GraphDiffNode, connection_cls=GraphDiffConnectionItem, seedable=False,
)
_register_spec(
    key="quality_gate", display_name="Quality Gate",
    description="Runs a production-readiness review on the current branch, scores its readiness, and recommends the highest-value remediation nodes.",
    category="Validation & Delivery", icon="fa5s.check-circle",
    node_cls=QualityGateNode, connection_cls=QualityGateConnectionItem, seedable=True,
)
_register_spec(
    key="code_review", display_name="Code Review Agent",
    description="Reviews a local or GitHub source file with deterministic scoring, structured findings, and a weighted code quality report.",
    category="Validation & Delivery", icon="fa5s.search",
    node_cls=CodeReviewNode, connection_cls=CodeReviewConnectionItem, seedable=True,
)
_register_spec(
    key="gitlink", display_name="Gitlink",
    description="Loads a GitHub repository into structured XML context, prepares file-level changes, and only writes after explicit approval.",
    category="Build & Execution", icon="fa5s.link",
    node_cls=GitlinkNode, connection_cls=GitlinkConnectionItem, seedable=True,
)
_register_spec(
    key="pycoder", display_name="Py-Coder",
    description="Opens a Python execution environment to run code and get AI analysis.",
    category="Build & Execution", icon="fa5s.laptop-code",
    node_cls=PyCoderNode, connection_cls=PyCoderConnectionItem, seedable=True,
)
_register_spec(
    key="code_sandbox", display_name="Execution Sandbox",
    description="Runs Python inside an isolated virtualenv and lets you declare per-node requirements.txt dependencies.",
    category="Build & Execution", icon="fa5s.shield-alt",
    node_cls=CodeSandboxNode, connection_cls=CodeSandboxConnectionItem, seedable=True,
)
_register_spec(
    key="html_renderer", display_name="HTML Renderer",
    description="Adds a node to render HTML code from a parent node.",
    category="Build & Execution", icon="fa5s.window-maximize",
    node_cls=HtmlViewNode, connection_cls=HtmlConnectionItem, seedable=True,
)
_register_spec(
    key="workflow", display_name="Workflow Architect",
    description="Designs an agentic execution plan, recommends the right specialist plugins, and seeds follow-up nodes.",
    category="Workflow & Drafting", icon="fa5s.project-diagram",
    node_cls=WorkflowNode, connection_cls=WorkflowConnectionItem, seedable=True,
)
_register_spec(
    key="artifact", display_name="Artifact / Drafter",
    description="A split-pane node for iteratively drafting and refining living documents (Markdown).",
    category="Workflow & Drafting", icon="fa5s.pen-nib",
    node_cls=ArtifactNode, connection_cls=ArtifactConnectionItem, seedable=True,
)


def get_plugin_spec(key):
    return PLUGIN_REGISTRY.get(key)


def get_display_name_for_node(node_or_cls):
    """Look up a node's registered display name by class, falling back to the class name.

    Intended as the eventual replacement for the several hand-maintained class-name ->
    display-name maps scattered around the app (see the PluginSpec docstring). Not yet
    wired into any of them - this phase only builds the table.
    """
    target_cls = node_or_cls if isinstance(node_or_cls, type) else type(node_or_cls)
    for spec in PLUGIN_REGISTRY.values():
        if spec.node_cls is target_cls:
            return spec.display_name
    return target_cls.__name__


PLUGIN_CATEGORY_META = [
    {
        "name": "Branch Foundations",
        "description": "Core branch scaffolding, prompt shaping, and focused conversation structures.",
        "icon": "fa5s.layer-group",
    },
    {
        "name": "Reasoning & Research",
        "description": "Deep thinking and web retrieval for exploring complex questions and grounding decisions.",
        "icon": "fa5s.compass",
    },
    {
        "name": "Validation & Delivery",
        "description": "Acceptance reviews, branch comparison, and delivery-focused checks that harden work before release.",
        "icon": "fa5s.check-circle",
    },
    {
        "name": "Build & Execution",
        "description": "Code generation, isolated execution, and rendering tools for turning ideas into working artifacts.",
        "icon": "fa5s.code",
    },
    {
        "name": "Workflow & Drafting",
        "description": "Agentic orchestration and structured drafting surfaces for multi-step work.",
        "icon": "fa5s.project-diagram",
    },
]


class PluginPortal:
    """
    The PluginPortal acts as a centralized manager for discovering,
    listing, and executing available plugins.
    """
    def __init__(self, main_window):
        self.main_window = main_window
        self.plugins = []
        self._discover_plugins()

    def _register_plugin(self, *, name, description, callback, category, icon):
        self.plugins.append({
            'name': name,
            'description': description,
            'callback': callback,
            'category': category,
            'icon': icon,
        })

    def _discover_plugins(self):
        self._register_plugin(
            name='System Prompt',
            description='Adds a special node to override the default system prompt for a conversation branch.',
            callback=self._create_system_prompt_node,
            category='Branch Foundations',
            icon='fa5s.sliders-h',
        )

        self._register_plugin(
            name='Conversation Node',
            description='Adds a node for a self-contained, linear chat conversation.',
            callback=self._create_conversation_node,
            category='Branch Foundations',
            icon='fa5s.comments',
        )

        self._register_plugin(
            name='Graphlink-Reasoning',
            description='A multi-step reasoning agent for solving complex problems.',
            callback=self._create_reasoning_node,
            category='Reasoning & Research',
            icon='fa5s.brain',
        )

        self._register_plugin(
            name='Graphlink-Web',
            description='Adds a node with web access for real-time information retrieval.',
            callback=self._create_web_node,
            category='Reasoning & Research',
            icon='fa5s.globe',
        )

        self._register_plugin(
            name='Branch Lens',
            description='Compares two selected graph branches side-by-side and highlights where their logic, code, and intent diverge.',
            callback=self._create_graph_diff_node,
            category='Validation & Delivery',
            icon='fa5s.code-branch',
        )

        self._register_plugin(
            name='Quality Gate',
            description='Runs a production-readiness review on the current branch, scores its readiness, and recommends the highest-value remediation nodes.',
            callback=self._create_quality_gate_node,
            category='Validation & Delivery',
            icon='fa5s.check-circle',
        )

        self._register_plugin(
            name='Code Review Agent',
            description='Reviews a local or GitHub source file with deterministic scoring, structured findings, and a weighted code quality report.',
            callback=self._create_code_review_node,
            category='Validation & Delivery',
            icon='fa5s.search',
        )

        self._register_plugin(
            name='Gitlink',
            description='Loads a GitHub repository into structured XML context, prepares file-level changes, and only writes after explicit approval.',
            callback=self._create_gitlink_node,
            category='Build & Execution',
            icon='fa5s.link',
        )

        self._register_plugin(
            name='Py-Coder',
            description='Opens a Python execution environment to run code and get AI analysis.',
            callback=self._create_pycoder_node,
            category='Build & Execution',
            icon='fa5s.laptop-code',
        )

        self._register_plugin(
            name='Execution Sandbox',
            description='Runs Python inside an isolated virtualenv and lets you declare per-node requirements.txt dependencies.',
            callback=self._create_code_sandbox_node,
            category='Build & Execution',
            icon='fa5s.shield-alt',
        )

        self._register_plugin(
            name='HTML Renderer',
            description='Adds a node to render HTML code from a parent node.',
            callback=self._create_html_view_node,
            category='Build & Execution',
            icon='fa5s.window-maximize',
        )

        self._register_plugin(
            name='Workflow Architect',
            description='Designs an agentic execution plan, recommends the right specialist plugins, and seeds follow-up nodes.',
            callback=self._create_workflow_node,
            category='Workflow & Drafting',
            icon='fa5s.project-diagram',
        )

        self._register_plugin(
            name='Artifact / Drafter',
            description='A split-pane node for iteratively drafting and refining living documents (Markdown).',
            callback=self._create_artifact_node,
            category='Workflow & Drafting',
            icon='fa5s.pen-nib',
        )

    def get_plugins(self):
        return self.plugins

    def get_plugin_categories(self):
        grouped_categories = []
        categorized_names = set()

        for category in PLUGIN_CATEGORY_META:
            plugins = [plugin for plugin in self.plugins if plugin.get('category') == category['name']]
            if not plugins:
                continue
            categorized_names.update(plugin['name'] for plugin in plugins)
            grouped_categories.append({
                'name': category['name'],
                'description': category['description'],
                'icon': category['icon'],
                'plugins': plugins,
            })

        uncategorized_plugins = [plugin for plugin in self.plugins if plugin['name'] not in categorized_names]
        if uncategorized_plugins:
            grouped_categories.append({
                'name': 'More Plugins',
                'description': 'Additional plugins that do not yet belong to a dedicated flyout category.',
                'icon': 'fa5s.puzzle-piece',
                'plugins': uncategorized_plugins,
            })

        return grouped_categories

    def execute_plugin(self, plugin_name):
        for plugin in self.plugins:
            if plugin['name'] == plugin_name:
                return plugin['callback']()
        print(f"Warning: Plugin '{plugin_name}' not found.")
        return None

    def _get_root_node(self):
        scene = self.main_window.chat_view.scene()
        current_node = self.main_window.current_node

        if current_node:
            root_node = current_node
            while root_node.parent_node:
                root_node = root_node.parent_node
            return root_node
        
        for node in scene.nodes:
            if not node.parent_node:
                return node
        
        return None

    def _resolve_branch_parent(self, selected_node):
        if isinstance(selected_node, CodeNode):
            return selected_node.parent_content_node
        return selected_node

    def _position_branch_node(self, scene, parent_node, node):
        node.setPos(scene.find_branch_position(parent_node, node))

    def _position_free_node(self, scene, base_pos, node, strategy="general"):
        node.setPos(scene.find_free_position(base_pos, node, strategy=strategy))

    def _create_system_prompt_node(self):
        scene = self.main_window.chat_view.scene()
        root_node = self._get_root_node()

        if not root_node:
            self.main_window.notification_banner.show_message("Please start a conversation before adding a System Prompt.", 5000, "warning")
            return

        for conn in scene.system_prompt_connections:
            if conn.end_node == root_node:
                self.main_window.notification_banner.show_message("A System Prompt node already exists for this conversation branch.", 5000, "info")
                return

        palette = get_current_palette()
        note_pos = QPointF(root_node.pos().x(), root_node.pos().y() - 200)
        prompt_note = scene.add_note(note_pos)
        prompt_note.is_system_prompt = True
        prompt_note.content = "Enter custom system prompt here..."
        prompt_note.header_color = palette.FRAME_COLORS["Purple Header"]["color"]
        prompt_note.color = "#252526"
        prompt_note.width = 300
        prompt_note.height = 150

        connection = SystemPromptConnectionItem(prompt_note, root_node)
        scene.addItem(connection)
        scene.system_prompt_connections.append(connection)
        return prompt_note

    def _create_pycoder_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node:
            self.main_window.notification_banner.show_message("Please select a node to branch from before adding Py-Coder.", 5000, "warning")
            return

        parent_node = self._resolve_branch_parent(selected_node)
        
        # Verify parent node is conversational
        if not hasattr(parent_node, 'children'):
             self.main_window.notification_banner.show_message("Py-Coder can only branch from a valid conversational node.", 5000, "warning")
             return

        pycoder_node = PyCoderNode(parent_node=parent_node)
        parent_node.children.append(pycoder_node)
        
        self._position_branch_node(scene, parent_node, pycoder_node)

        scene.addItem(pycoder_node)
        scene.pycoder_nodes.append(pycoder_node)

        connection = PyCoderConnectionItem(parent_node, pycoder_node)
        pycoder_node.incoming_connection = connection
        scene.addItem(connection)
        scene.pycoder_connections.append(connection)
        return pycoder_node

    def _create_code_sandbox_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node:
            self.main_window.notification_banner.show_message("Please select a node to branch from before adding an Execution Sandbox.", 5000, "warning")
            return None

        parent_node = self._resolve_branch_parent(selected_node)

        if not hasattr(parent_node, 'children'):
            self.main_window.notification_banner.show_message("Execution Sandbox can only branch from a valid conversational node.", 5000, "warning")
            return None

        sandbox_node = CodeSandboxNode(parent_node=parent_node)
        parent_node.children.append(sandbox_node)
        sandbox_node.sandbox_requested.connect(self.main_window.execute_code_sandbox_node)

        if hasattr(parent_node, 'conversation_history') and parent_node.conversation_history:
            sandbox_node.conversation_history = clone_history(parent_node.conversation_history)

        self._position_branch_node(scene, parent_node, sandbox_node)

        scene.addItem(sandbox_node)
        scene.code_sandbox_nodes.append(sandbox_node)

        connection = CodeSandboxConnectionItem(parent_node, sandbox_node)
        sandbox_node.incoming_connection = connection
        scene.addItem(connection)
        scene.code_sandbox_connections.append(connection)
        return sandbox_node

    def _create_artifact_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node:
            self.main_window.notification_banner.show_message("Please select a node to branch from before adding an Artifact Drafter.", 5000, "warning")
            return

        parent_node = self._resolve_branch_parent(selected_node)

        if not hasattr(parent_node, 'children'):
             self.main_window.notification_banner.show_message("Artifact Drafter can only branch from a valid conversational node.", 5000, "warning")
             return

        artifact_node = ArtifactNode(parent_node=parent_node)
        parent_node.children.append(artifact_node)
        artifact_node.artifact_requested.connect(self.main_window.execute_artifact_node)
        
        self._position_branch_node(scene, parent_node, artifact_node)

        scene.addItem(artifact_node)
        # Ensure scene tracks it (will be formalized in graphite_scene.py later)
        if not hasattr(scene, 'artifact_nodes'):
            scene.artifact_nodes = []
            scene.artifact_connections = []
            
        scene.artifact_nodes.append(artifact_node)

        connection = ArtifactConnectionItem(parent_node, artifact_node)
        artifact_node.incoming_connection = connection
        scene.addItem(connection)
        scene.artifact_connections.append(connection)
        return artifact_node

    def _create_workflow_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node or not hasattr(selected_node, 'children'):
            self.main_window.notification_banner.show_message("Please select a valid node to branch from before adding Workflow Architect.", 5000, "warning")
            return None

        workflow_node = WorkflowNode(parent_node=selected_node)
        selected_node.children.append(workflow_node)
        workflow_node.workflow_requested.connect(self.main_window.execute_workflow_node)
        workflow_node.plugin_requested.connect(self.main_window.instantiate_seeded_plugin)

        if hasattr(selected_node, 'conversation_history') and selected_node.conversation_history:
            workflow_node.conversation_history = clone_history(selected_node.conversation_history)

        self._position_branch_node(scene, selected_node, workflow_node)

        scene.addItem(workflow_node)
        scene.workflow_nodes.append(workflow_node)

        connection = WorkflowConnectionItem(selected_node, workflow_node)
        workflow_node.incoming_connection = connection
        scene.addItem(connection)
        scene.workflow_connections.append(connection)
        return workflow_node

    def _create_quality_gate_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node or not hasattr(selected_node, 'children'):
            self.main_window.notification_banner.show_message("Please select a valid node to branch from before adding Quality Gate.", 5000, "warning")
            return None

        quality_gate_node = QualityGateNode(parent_node=selected_node)
        selected_node.children.append(quality_gate_node)
        quality_gate_node.review_requested.connect(self.main_window.execute_quality_gate_node)
        quality_gate_node.plugin_requested.connect(self.main_window.instantiate_seeded_plugin)
        quality_gate_node.note_requested.connect(self.main_window.create_quality_gate_note)

        if hasattr(selected_node, 'conversation_history') and selected_node.conversation_history:
            quality_gate_node.conversation_history = clone_history(selected_node.conversation_history)

        self._position_branch_node(scene, selected_node, quality_gate_node)

        scene.addItem(quality_gate_node)
        scene.quality_gate_nodes.append(quality_gate_node)

        connection = QualityGateConnectionItem(selected_node, quality_gate_node)
        quality_gate_node.incoming_connection = connection
        scene.addItem(connection)
        scene.quality_gate_connections.append(connection)
        return quality_gate_node

    def _create_code_review_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node:
            self.main_window.notification_banner.show_message("Please select a node to branch from before adding Code Review Agent.", 5000, "warning")
            return None

        parent_node = self._resolve_branch_parent(selected_node)

        if not hasattr(parent_node, 'children'):
            self.main_window.notification_banner.show_message("Code Review Agent can only branch from a valid conversational node.", 5000, "warning")
            return None

        review_node = CodeReviewNode(parent_node=parent_node, settings_manager=self.main_window.settings_manager)
        parent_node.children.append(review_node)
        review_node.review_requested.connect(self.main_window.execute_code_review_node)

        if hasattr(parent_node, 'conversation_history') and parent_node.conversation_history:
            review_node.conversation_history = clone_history(parent_node.conversation_history)

        self._position_branch_node(scene, parent_node, review_node)

        scene.addItem(review_node)
        scene.code_review_nodes.append(review_node)

        connection = CodeReviewConnectionItem(parent_node, review_node)
        review_node.incoming_connection = connection
        scene.addItem(connection)
        scene.code_review_connections.append(connection)
        return review_node

    def _create_gitlink_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node:
            self.main_window.notification_banner.show_message("Please select a node to branch from before adding Gitlink.", 5000, "warning")
            return None

        parent_node = self._resolve_branch_parent(selected_node)

        if not hasattr(parent_node, 'children'):
            self.main_window.notification_banner.show_message("Gitlink can only branch from a valid conversational node.", 5000, "warning")
            return None

        gitlink_node = GitlinkNode(parent_node=parent_node, settings_manager=self.main_window.settings_manager)
        parent_node.children.append(gitlink_node)
        gitlink_node.gitlink_requested.connect(self.main_window.execute_gitlink_node)

        if hasattr(parent_node, 'conversation_history') and parent_node.conversation_history:
            gitlink_node.conversation_history = clone_history(parent_node.conversation_history)

        self._position_branch_node(scene, parent_node, gitlink_node)

        scene.addItem(gitlink_node)
        scene.gitlink_nodes.append(gitlink_node)

        connection = GitlinkConnectionItem(parent_node, gitlink_node)
        gitlink_node.incoming_connection = connection
        scene.addItem(connection)
        scene.gitlink_connections.append(connection)
        return gitlink_node

    def _create_graph_diff_node(self):
        from graphite_plugins.graphite_plugin_artifact import ArtifactNode

        scene = self.main_window.chat_view.scene()
        valid_sources = (ChatNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, HtmlViewNode, WorkflowNode, ArtifactNode, QualityGateNode, CodeReviewNode, GitlinkNode)
        selected_nodes = [item for item in scene.selectedItems() if isinstance(item, valid_sources)]

        if len(selected_nodes) != 2:
            self.main_window.notification_banner.show_message("Select exactly two branch-tip nodes before adding Branch Lens.", 5000, "warning")
            return None

        left_node, right_node = selected_nodes[:2]
        diff_node = GraphDiffNode(left_node, right_node)
        diff_node.compare_requested.connect(self.main_window.execute_graph_diff_node)
        diff_node.note_requested.connect(self.main_window.create_graph_diff_note)

        left_width = left_node.width if hasattr(left_node, 'width') else left_node.boundingRect().width()
        right_width = right_node.width if hasattr(right_node, 'width') else right_node.boundingRect().width()
        spawn_x = max(left_node.scenePos().x() + left_width, right_node.scenePos().x() + right_width) + 160
        spawn_y = (left_node.scenePos().y() + right_node.scenePos().y()) / 2
        self._position_free_node(scene, QPointF(spawn_x, spawn_y), diff_node, strategy="branch")

        scene.addItem(diff_node)
        scene.graph_diff_nodes.append(diff_node)

        for source_node in (left_node, right_node):
            connection = GraphDiffConnectionItem(source_node, diff_node)
            scene.addItem(connection)
            scene.graph_diff_connections.append(connection)

        return diff_node

    def _create_web_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node or not hasattr(selected_node, 'children'):
            self.main_window.notification_banner.show_message("Please select a valid node to branch from before adding a Web Node.", 5000, "warning")
            return

        web_node = WebNode(parent_node=selected_node)
        selected_node.children.append(web_node)
        web_node.run_clicked.connect(self.main_window.execute_web_node)
        
        self._position_branch_node(scene, selected_node, web_node)

        scene.addItem(web_node)
        scene.web_nodes.append(web_node)

        connection = WebConnectionItem(selected_node, web_node)
        web_node.incoming_connection = connection
        scene.addItem(connection)
        scene.web_connections.append(connection)
        return web_node

    def _create_conversation_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node or not hasattr(selected_node, 'children'):
            self.main_window.notification_banner.show_message("Please select a valid node to branch from before adding a Conversation Node.", 5000, "warning")
            return

        convo_node = ConversationNode(parent_node=selected_node)
        selected_node.children.append(convo_node)
        convo_node.ai_request_sent.connect(self.main_window.handle_conversation_node_request)
        convo_node.cancel_requested.connect(self.main_window.handle_conversation_node_cancel)
        
        if hasattr(selected_node, 'conversation_history') and selected_node.conversation_history:
            history_copy = clone_history(selected_node.conversation_history)
            convo_node.set_history(history_copy)

        self._position_branch_node(scene, selected_node, convo_node)

        scene.addItem(convo_node)
        scene.conversation_nodes.append(convo_node)

        connection = ConversationConnectionItem(selected_node, convo_node)
        convo_node.incoming_connection = connection
        scene.addItem(connection)
        scene.conversation_connections.append(connection)
        return convo_node

    def _create_reasoning_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        if not selected_node or not hasattr(selected_node, 'children'):
            self.main_window.notification_banner.show_message("Please select a valid node to branch from before adding a Reasoning Node.", 5000, "warning")
            return

        reasoning_node = ReasoningNode(parent_node=selected_node)
        selected_node.children.append(reasoning_node)
        reasoning_node.reasoning_requested.connect(self.main_window.execute_reasoning_node)
        
        self._position_branch_node(scene, selected_node, reasoning_node)

        scene.addItem(reasoning_node)
        scene.reasoning_nodes.append(reasoning_node)

        connection = ReasoningConnectionItem(selected_node, reasoning_node)
        reasoning_node.incoming_connection = connection
        scene.addItem(connection)
        scene.reasoning_connections.append(connection)
        return reasoning_node

    def _create_html_view_node(self):
        scene = self.main_window.chat_view.scene()
        selected_node = self.main_window.current_node

        valid_parents = (ChatNode, CodeNode, PyCoderNode, CodeSandboxNode, WebNode, ConversationNode, ReasoningNode, WorkflowNode, QualityGateNode, CodeReviewNode, GitlinkNode)
        if not selected_node or not isinstance(selected_node, valid_parents):
            self.main_window.notification_banner.show_message("Please select a valid node to branch from before adding an HTML Renderer.", 5000, "warning")
            return None

        html_view_node = HtmlViewNode(parent_node=selected_node)
        selected_node.children.append(html_view_node)
        
        self._position_branch_node(scene, selected_node, html_view_node)

        scene.addItem(html_view_node)
        scene.html_view_nodes.append(html_view_node)
        
        if isinstance(selected_node, CodeNode):
            html_view_node.set_html_content(selected_node.code)

        connection = HtmlConnectionItem(selected_node, html_view_node)
        html_view_node.incoming_connection = connection
        scene.addItem(connection)
        scene.html_connections.append(connection)
        return html_view_node
