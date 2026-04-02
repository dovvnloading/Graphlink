import webbrowser
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget
)
import qtawesome as qta
import graphite_config as config
from graphite_styles import THEMES
from graphite_update import APP_VERSION

class AboutDialog(QDialog):
    """A premium dialog displaying application information, developer credits, and external links."""
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("About Graphlink")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(True)
        self.resize(420, 420)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(30, 30, 30, 30)
        main_layout.setSpacing(15)

        # --- Header Section ---
        app_title = QLabel("Graphlink")
        app_title.setObjectName("aboutTitle")
        app_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(app_title)

        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setObjectName("aboutVersion")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(version_label)
        
        # --- Divider ---
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); max-height: 1px; margin: 10px 0px;")
        main_layout.addWidget(divider)

        # --- Links & Credits Section ---
        links_layout = QVBoxLayout()
        links_layout.setSpacing(8)

        def create_link_btn(icon_name, text, url):
            btn = QPushButton(qta.icon(icon_name, color='#f3f5f8'), text)
            btn.setObjectName("aboutLinkBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda: webbrowser.open(url))
            return btn

        # Project Info
        project_label = QLabel("PROJECT")
        project_label.setObjectName("aboutSectionLabel")
        links_layout.addWidget(project_label)
        
        repo_btn = create_link_btn('fa5b.github', "  Graphlink Repository", "https://github.com/dovvnloading/Graphlink")
        links_layout.addWidget(repo_btn)

        links_layout.addSpacing(16)

        # Developer Info
        dev_label = QLabel("DEVELOPED BY")
        dev_label.setObjectName("aboutSectionLabel")
        links_layout.addWidget(dev_label)

        name_label = QLabel("Matthew Robert Wesney")
        name_label.setObjectName("aboutDevName")
        links_layout.addWidget(name_label)

        web_btn = create_link_btn('fa5s.globe', "  Personal Webpage", "https://mattwesney.com")
        gh_btn = create_link_btn('fa5b.github', "  Personal GitHub", "https://github.com/dovvnloading")

        links_layout.addWidget(web_btn)
        links_layout.addWidget(gh_btn)

        # Container to constrain button widths cleanly
        links_container = QWidget()
        links_container.setLayout(links_layout)
        links_container.setContentsMargins(30, 0, 30, 0)
        main_layout.addWidget(links_container)

        main_layout.addStretch()

        # --- Footer Section ---
        footer_layout = QHBoxLayout()
        
        copyright_label = QLabel("© 2026")
        copyright_label.setObjectName("aboutCopyright")
        footer_layout.addWidget(copyright_label, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        
        footer_layout.addStretch()

        close_button = QPushButton("Close")
        close_button.setObjectName("aboutCloseBtn")
        close_button.setFixedSize(100, 32)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.accept)
        footer_layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        main_layout.addLayout(footer_layout)

        self.on_theme_changed()

    def on_theme_changed(self):
        """Applies the current theme's stylesheet dynamically to the dialog."""
        palette = config.get_current_palette()
        accent = palette.SELECTION.name()
        
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #1e1e1e;
            }}
            QLabel#aboutTitle {{
                font-size: 26px;
                font-weight: 900;
                color: {accent};
                letter-spacing: 1px;
            }}
            QLabel#aboutVersion {{
                font-size: 12px;
                color: #888888;
            }}
            QLabel#aboutSectionLabel {{
                color: #8d8d8d;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.1em;
                margin-left: 2px;
            }}
            QLabel#aboutDevName {{
                color: #e0e0e0;
                font-size: 13px;
                font-weight: 600;
                margin-left: 2px;
                margin-bottom: 4px;
            }}
            QPushButton#aboutLinkBtn {{
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                color: #f3f5f8;
                font-size: 12px;
                font-weight: 600;
                padding: 10px;
                text-align: left;
                padding-left: 20px;
            }}
            QPushButton#aboutLinkBtn:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border-color: rgba(255, 255, 255, 0.2);
            }}
            QLabel#aboutCopyright {{
                font-size: 11px;
                color: #666666;
            }}
            QPushButton#aboutCloseBtn {{
                background-color: {accent};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#aboutCloseBtn:hover {{
                background-color: {palette.SELECTION.lighter(110).name()};
            }}
        """)


class HelpCategoryButton(QPushButton):
    def __init__(self, section_name, icon_name, parent=None):
        super().__init__(section_name, parent)
        self.section_name = section_name
        self.setObjectName("helpCategoryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(40)
        self.setIcon(qta.icon(icon_name, color="#d9e1ea"))
        self.setIconSize(QSize(14, 14))


class HelpDialog(QFrame):
    """A flyout help panel styled like the plugin picker and settings panel."""
    SECTION_DEFS = [
        (
            "Overview",
            "fa5s.info-circle",
            "What Graphlink is, how work is structured, and how a typical project moves from prompt to polished output.",
            [
                ("What Graphlink Does", [
                    ("Visual AI Workspace", "Graphlink turns prompts, replies, notes, charts, documents, code, and plugin runs into connected items on one canvas. Instead of losing work inside a single scrolling transcript, you can keep multiple lines of thought visible at once.", "fa5s.project-diagram"),
                    ("Branch-Based Conversations", "Every selected node becomes the anchor for what you do next. That makes it easy to fork alternatives, compare directions, and keep complex work organized as a family of branches instead of one long thread.", "fa5s.code-branch"),
                    ("Mixed Content Graph", "A single project can contain chat nodes, code blocks, visible reasoning, attached files, generated images, notes, charts, and specialist tool nodes. The connections preserve where an idea came from and what it produced.", "fa5s.layer-group"),
                    ("Saved Project Space", "Chats, plugin state, frames, containers, pins, and notes are persisted and can be reopened from the Library. Graphlink is designed for ongoing project work, not just one-off prompts.", "fa5s.folder-open"),
                ]),
                ("Typical Session Flow", [
                    ("Start or Resume", "Begin from the Welcome screen, open a recent project, or press Ctrl+T to start a new chat. The app is comfortable both for quick ideation and for returning to long-running work.", "fa5s.play-circle"),
                    ("Build a Branch", "Send a prompt, review the result, then select the exact node you want to continue from. Each selection creates a deliberate branch point so you can refine or challenge a result without overwriting earlier work.", "fa5s.paper-plane"),
                    ("Add Specialist Nodes", "When a branch needs something more than a plain reply, open Plugins to add reasoning, web research, coding, sandbox execution, drafting, HTML rendering, or branch comparison tools.", "fa5s.puzzle-piece"),
                    ("Capture the Outcome", "Use notes, explainers, takeaways, charts, exports, and document views to turn raw responses into a clearer project record. The goal is a canvas you can revisit and understand later.", "fa5s.sticky-note"),
                ]),
            ],
        ),
        (
            "Canvas & Branching",
            "fa5s.code-branch",
            "How selection, context inheritance, branching, and side-by-side exploration work inside the graph.",
            [
                ("How Branches Work", [
                    ("Active Context Node", "The node you select becomes the parent for your next message or specialist tool. The input placeholder updates to show what you are responding to, which helps you stay oriented in large graphs.", "fa5s.crosshairs"),
                    ("Parent and Child History", "Branches inherit the relevant conversation history from their anchor node. Sibling branches can explore different ideas from the same point without stomping on each other.", "fa5s.project-diagram"),
                    ("Focus One Branch", "Chat nodes can temporarily hide other branches so you can concentrate on one path. This is especially useful once a graph starts to fan out into several competing directions.", "fa5s.eye-slash"),
                    ("Regenerate from the Same Context", "Assistant responses can be regenerated from their parent branch when you want a fresh answer without manually rebuilding the surrounding context.", "fa5s.sync"),
                ]),
                ("Working on the Canvas", [
                    ("Select and Move", "Click a node to select it, drag on empty space to rubber-band select, and move one item or a whole group together. The graph behaves like a visual workspace, not a locked transcript.", "fa5s.arrows-alt"),
                    ("Collapse Dense Areas", "Most major node types can collapse to keep the canvas readable. Collapse is especially helpful when a branch is stable but still needs to stay connected to the rest of the project.", "fa5s.compress-arrows-alt"),
                    ("Keep Notes Beside the Work", "Notes live alongside AI nodes rather than inside them. Use them for decisions, TODOs, pasted snippets, executive summaries, or anything that should stay visible but should not be sent back to the model.", "fa5s.sticky-note"),
                    ("Compare Alternatives", "When two branch tips represent different approaches, Branch Lens can inspect both branches and explain where their logic, code direction, and intent diverge.", "fa5s.exchange-alt"),
                ]),
            ],
        ),
        (
            "Nodes & Content",
            "fa5s.layer-group",
            "The main node types you will see on the canvas and what each one is best at.",
            [
                ("Core Conversation Nodes", [
                    ("Chat Nodes", "User and assistant chat nodes are the backbone of the application. Selecting one lets you continue exactly that line of thought, which makes branching feel intentional instead of accidental.", "fa5s.comments"),
                    ("Code Nodes", "When a response includes fenced code, Graphlink breaks it into dedicated code nodes. Those nodes can be copied, exported, regenerated through their parent reply, or used as a starting point for coding tools.", "fa5s.code"),
                    ("Thinking Nodes", "If a model returns visible reasoning or analysis, the app stores it as a separate thinking node. This keeps long reasoning trails available without burying the main answer.", "fa5s.brain"),
                    ("Document and Image Nodes", "Attachments become their own nodes on the branch so the source material remains visible next to the prompt that used it. That makes it easier to audit what the model actually saw.", "fa5s.paperclip"),
                ]),
                ("Supporting Content", [
                    ("Notes", "Manual notes, takeaways, explainer notes, and multi-node summary notes all live as movable note items. They are ideal for turning raw model output into cleaner project knowledge.", "fa5s.sticky-note"),
                    ("Charts", "The app can turn text-derived or numeric content into bar, line, pie, histogram, and Sankey charts on the canvas. Charts are useful when a branch contains data you want to explain visually.", "fa5s.chart-bar"),
                    ("Document View", "Long chat content can be opened in the document viewer for easier reading and review. This is especially helpful for lengthy drafts, specifications, or dense research summaries.", "fa5s.book-open"),
                    ("HTML Preview", "The HTML Renderer node can take markup and show a live preview inside the graph, with an optional pop-out preview window for closer inspection.", "fa5s.window-maximize"),
            ("Node Context Menus", "Right-click chat, code, and plugin nodes for copy, export, document view, branch isolation, regeneration, takeaways, explainers, charts, image generation, and deletion actions. When multiple chat nodes are selected, the menu also exposes group summary workflows.", "fa5s.mouse-pointer"),
                ]),
            ],
        ),
        (
            "Navigation",
            "fa5s.compass",
            "Mouse, keyboard, search, command palette, and view controls for moving quickly through small or very large graphs.",
            [
                ("Mouse and View Controls", [
                    ("Pan the Canvas", "Hold the Middle Mouse Button and drag to move around the workspace. This is the fastest way to traverse large graphs once you stop thinking of the canvas like a normal chat window.", "fa5s.hand-paper"),
                    ("Zoom and Focus", "Use Ctrl + Mouse Wheel to zoom, or use the toolbar buttons when you want a fixed step. Q and E also zoom out and in from the keyboard.", "fa5s.search-plus"),
                    ("Zoom to an Area", "Hold Shift and drag a rectangle to zoom the view to a specific region. It is a precise way to jump into a dense subsection of a large project.", "fa5s.search"),
                    ("Fit All and Reset", "Fit All reframes the canvas around everything currently on it, while Reset restores the default zoom level. These two actions are the quickest recovery tools when you get lost.", "fa5s.expand"),
                    ("Minimap and Overlays", "Use the Controls toggle to reveal drag, grid, and font tools, and use the minimap to jump directly to distant nodes. This becomes more valuable as your project spreads out.", "fa5s.compass"),
                ]),
                ("Keyboard Workflow", [
                    ("WASD and Branch Navigation", "W, A, S, and D pan the view, while Ctrl + Arrow Keys move between parent, child, and sibling nodes in the current branch. This makes branch review much faster than constant mouse travel.", "fa5s.arrows-alt"),
                    ("Command Palette", "Press Ctrl + K to open a searchable command palette for layout, note creation, selection, navigation, plugin insertion, chart generation, and more. It is the fastest way to discover power-user actions.", "fa5s.terminal"),
                    ("Canvas Search", "Press Ctrl + F to search across chat, code, document, image, thinking, and plugin nodes. Search results can be stepped through so you can quickly revisit important content.", "fa5s.search"),
                    ("Selection Utilities", "Delete removes the current selection, and the command palette also exposes focus selection, select all, collapse all, and expand all. These commands are useful once a graph becomes visually dense.", "fa5s.tasks"),
                ]),
                ("Shortcut Reference", [
                    ("Ctrl + T / Ctrl + L / Ctrl + S", "Start a new chat, open the Library, or save the current project. These are the main project-level shortcuts you will use most often.", "fa5s.save"),
                    ("Ctrl + G / Ctrl + Shift + G / Ctrl + N", "Wrap the current selection in a Frame, create a Container, or add a new Note. These three actions cover most day-to-day organization work.", "fa5s.object-group"),
                    ("Ctrl + Left-Click / Ctrl + Right-Click", "Add a connection pin to reroute a line, or remove a pin that you no longer need. Connection pins are helpful when you want to untangle overlapping paths.", "fa5s.dot-circle"),
                ]),
            ],
        ),
        (
            "Organization",
            "fa5s.sitemap",
            "Ways to group, label, tidy, and visually manage complex canvases as a project grows.",
            [
                ("Structuring Large Graphs", [
                    ("Frames", "Select node-like items and press Ctrl + G to wrap them in a frame. Frames are best for labeling a visual section of the project without forcing the grouped items to move as one rigid unit.", "fa5s.object-group"),
                    ("Containers", "Use Ctrl + Shift + G to create a container when a set of items should move together. Containers are useful for keeping a working cluster intact while you reorganize the rest of the canvas.", "fa5s.box-open"),
                    ("Titles and Colors", "Frames and containers can be renamed and recolored so sections of the graph read like chapters or workstreams. This helps when you want the canvas to double as a presentation surface.", "fa5s.palette"),
                    ("Auto-Organize Tree Layout", "The Organize button arranges conversational and plugin branches into a cleaner horizontal tree. It is a fast way to recover readability after a burst of exploratory work.", "fa5s.sitemap"),
                ]),
                ("Orientation Aids", [
                    ("Navigation Pins", "Pins mark important places on the canvas and are saved with the chat. Use the Pins toolbar button to reveal the overlay and jump back to major milestones or hotspots.", "fa5s.map-pin"),
                    ("Connection Pins", "Ctrl + left-click a connection to add a routing pin and shape the line, then Ctrl + right-click a pin to remove it. This is especially helpful when several branches overlap visually.", "fa5s.dot-circle"),
                    ("Grid and Guide Controls", "The controls overlay can enable snap-to-grid, smart guides, orthogonal routing, font controls, and faded connections. These tools are useful when a canvas needs visual cleanup rather than new AI output.", "fa5s.sliders-h"),
                    ("Reduce Visual Noise", "Combine collapse, grouping, branch isolation, and layout tools to keep the graph readable. Graphlink works best when you treat organization as part of the thinking process, not just post-processing.", "fa5s.eye"),
                ]),
            ],
        ),
        (
            "Plugins & Tools",
            "fa5s.puzzle-piece",
            "Specialist nodes that extend a branch into research, reasoning, coding, drafting, rendering, and comparison workflows.",
            [
                ("Research and Analysis", [
                    ("Graphlink-Web", "Use this when a branch depends on current information or external sources. The node runs a web retrieval flow, summarizes the findings, and stores source links directly in the result.", "fa5s.globe-americas"),
                    ("Graphlink-Reasoning", "Best for complex questions that benefit from staged thinking instead of a single direct answer. You can raise the thinking budget when a branch needs a slower, more deliberate analysis path.", "fa5s.brain"),
                    ("Conversation Node", "Creates a self-contained linear chat surface inside the graph. Use it when you want a focused sub-conversation that can be pruned and iterated without expanding the main branch too aggressively.", "fa5s.comments"),
                    ("Branch Lens", "Compare two selected branch tips to see how their logic, code orientation, and intent differ. You can then turn the comparison into a note for easier project review.", "fa5s.code-branch"),
                    ("Quality Gate", "Runs a production-readiness review against the current branch, scores how close it is to done, calls out blockers and missing evidence, and can spawn the next best remediation nodes directly from the report.", "fa5s.check-circle"),
                ]),
                ("Build, Draft, and Render", [
                    ("System Prompt", "Adds a branch-scoped system prompt note that changes assistant behavior only for that conversation path. This is ideal when you want a role, tone, or instruction change without affecting the rest of the project.", "fa5s.sliders-h"),
                    ("Py-Coder", "A coding workspace with AI-driven and manual modes, generated code, terminal output, and final analysis tabs. Use it for fast implementation, debugging, code generation, and lightweight computation.", "fa5s.laptop-code"),
                    ("Execution Sandbox", "Runs Python in an isolated virtual environment and supports per-node requirements. Choose this over Py-Coder when you need dependency-aware execution or a cleaner reproducible runtime.", "fa5s.shield-alt"),
                    ("Artifact / Drafter", "A living markdown drafting surface for reports, specs, briefs, and other documents that need repeated revision. It is the most natural place to keep polished long-form output inside the graph.", "fa5s.pen-nib"),
                    ("Workflow Architect", "Builds a compact execution blueprint for the current goal and recommends the next plugin nodes to create, complete with seeded starter prompts. It is especially useful at the start of a large or ambiguous task.", "fa5s.project-diagram"),
                    ("HTML Renderer", "Turns HTML or UI markup into a preview pane inside the graph and can pop the preview into a separate window. It is the right tool when a branch needs visual feedback instead of plain text output.", "fa5s.window-maximize"),
                ]),
            ],
        ),
        (
            "Settings & Models",
            "fa5s.sliders-h",
            "How runtime modes, providers, model routing, and quality-of-life settings shape the behavior of the application.",
            [
                ("Runtime Modes", [
                    ("Ollama (Local)", "Use local Ollama when you want on-device chat and reasoning. You can choose a default chat model and switch between Quick mode and Thinking mode for different response styles.", "fa5s.desktop"),
                    ("API Endpoint Mode", "Use API Endpoint mode for OpenAI-compatible providers or Gemini. This unlocks hosted models, per-task model routing, and the image-generation path used by the canvas.", "fa5s.cloud"),
                    ("Per-Task Model Selection", "Graphlink can store different models for title generation, main chat, chart generation, image generation, web validation, and web summarization. This lets you optimize cost and quality across different tools.", "fa5s.tasks"),
                    ("Live Provider Reconfiguration", "Switching modes reinitializes the active provider for the current session. If a provider is missing credentials or cannot initialize, the app warns you before you keep working.", "fa5s.sync"),
                ]),
                ("Personalization and Feedback", [
                    ("Appearance", "Theme and appearance settings restyle the app, including flyouts and node accents, around the current palette. This is mainly cosmetic, but it helps tailor the workspace to your preference.", "fa5s.palette"),
                    ("Token Counter", "When enabled, the token counter shows prompt, context, output, and running session totals. It is useful when you want a sense of branch size or model budget.", "fa5s.calculator"),
                    ("Provider-Specific Guidance", "API settings explain which providers are supported and which fields matter for each one. OpenAI-compatible endpoints and Gemini use slightly different setup and model-loading paths.", "fa5s.exclamation-triangle"),
                    ("Optional Feature Dependencies", "Some features rely on optional libraries, such as PDF or DOCX import-export support and HTML preview support. When something is unavailable, Graphlink tries to tell you what dependency is missing.", "fa5s.info-circle"),
                ]),
            ],
        ),
        (
            "Saving & Output",
            "fa5s.save",
            "Persistence, attachments, exports, and the ways Graphlink turns branch results into reusable project deliverables.",
            [
                ("Persistence and Library", [
                    ("Background Saves", "Use Save to persist the current graph, including chats, plugin nodes, notes, pins, frames, containers, and view state. This makes it practical to treat a chat like a reusable workspace instead of a disposable session.", "fa5s.save"),
                    ("Chat Library", "Open the Library with the toolbar or Ctrl + L to reopen, rename, organize, or continue past projects. The Library is the main entry point for resuming work across sessions.", "fa5s.book"),
                    ("Welcome Screen", "The Welcome screen highlights recent projects and starter prompts so you can quickly resume work or begin a fresh exploration without rebuilding context from scratch.", "fa5s.home"),
                    ("Local Project Record", "Because the graph is saved as a full project state, your canvas can function as a working notebook, task map, and decision trail all at once.", "fa5s.database"),
                ]),
                ("Attachments and Exports", [
                    ("Supported Attachments", "You can attach common text and code files, JSON, CSV, XML, HTML, CSS, JS, and Markdown. PDF and DOCX attachments are also supported when their reader libraries are installed.", "fa5s.paperclip"),
                    ("Drag and Drop", "Files can be staged by dragging them onto the canvas or by using the paperclip button. The attachment becomes a visible branch node so it is clear what source material was provided.", "fa5s.file-alt"),
                    ("Export Formats", "Chat, document, and code content can be exported in practical formats such as TXT, Markdown, HTML, and PDF. Chat and document content also support DOCX, and code can be exported as a Python script.", "fa5s.file-export"),
                    ("Generated Outputs", "Image generation, chart creation, takeaway notes, explainers, group summaries, and branch diff notes all let you promote transient AI output into reusable project artifacts.", "fa5s.image"),
                ]),
            ],
        ),
        (
            "Use Cases",
            "fa5s.tasks",
            "Practical ways to use the full application beyond simple back-and-forth prompting.",
            [
                ("Common Workflows", [
                    ("Research and Comparison", "Start with a broad question, branch different answers, use Graphlink-Web to ground facts, and finish with Branch Lens when you want a disciplined comparison between competing paths.", "fa5s.search"),
                    ("Coding and Debugging", "Ask for code on the main canvas, move promising results into Py-Coder for iteration, and switch to Execution Sandbox when dependencies or reproducibility matter. This keeps ideation and execution connected.", "fa5s.code"),
                    ("Planning and Execution", "Use Workflow Architect when a goal feels large or undefined. It can convert a vague request into a clearer plugin sequence so your next few steps are already framed.", "fa5s.project-diagram"),
                    ("Shipping and Hardening", "When a branch is close to done, run Quality Gate to judge whether it actually meets the bar. It is especially useful before handoff, release, demos, or any moment when confidence matters more than momentum.", "fa5s.check-circle"),
                    ("Long-Form Writing", "Keep source research nearby, use reasoning where needed, and let Artifact / Drafter hold the polished document. This is a strong workflow for specs, reports, proposals, and internal documentation.", "fa5s.pen-nib"),
                ]),
                ("Knowledge Work Patterns", [
                    ("Meeting and Study Notes", "Generate takeaways, explainer notes, and group summaries around important nodes so the final canvas reads like a structured knowledge map rather than a raw transcript dump.", "fa5s.sticky-note"),
                    ("Product and UX Exploration", "Branch alternative product directions, draft requirements, render HTML prototypes, and compare variants without losing the reasoning that led to each option.", "fa5s.window-maximize"),
                    ("Data Storytelling", "Turn a branch that contains numbers or structure into charts, then keep the explanation, source inputs, and conclusions beside the visualization on the same canvas.", "fa5s.chart-bar"),
                    ("Prompt Variation by Branch", "Use System Prompt nodes and branching to test different assistant behaviors against the same underlying project. This is especially useful when you want to compare tone, role, or working style.", "fa5s.sliders-h"),
                ]),
            ],
        ),
    ]

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.category_buttons = {}
        self.current_section_name = None
        self.setObjectName("helpFlyoutPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(900, 620)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("helpFlyoutShell")
        self.shell.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.shell)

        root_layout = QHBoxLayout(self.shell)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        rail = QWidget()
        rail.setObjectName("helpCategoryRail")
        rail.setFixedWidth(200)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(12, 12, 12, 12)
        rail_layout.setSpacing(8)

        eyebrow = QLabel("Help Center")
        eyebrow.setObjectName("helpSectionLabel")
        rail_layout.addWidget(eyebrow)

        rail_intro = QLabel("Graphlink is a visual AI workspace. Start with the overview, then jump directly to the workflow, tool, or project area you need.")
        rail_intro.setObjectName("helpRailIntro")
        rail_intro.setWordWrap(True)
        rail_layout.addWidget(rail_intro)

        self.category_button_column = QVBoxLayout()
        self.category_button_column.setContentsMargins(0, 6, 0, 0)
        self.category_button_column.setSpacing(6)
        rail_layout.addLayout(self.category_button_column)
        rail_layout.addStretch(1)

        divider = QFrame()
        divider.setObjectName("helpFlyoutDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        divider.setLineWidth(1)

        content_panel = QWidget()
        content_panel.setObjectName("helpPane")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(14, 12, 14, 12)
        content_layout.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        self.section_icon = QLabel()
        self.section_icon.setObjectName("helpCategoryIcon")
        self.section_icon.setFixedSize(28, 28)
        self.section_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self.section_icon, 0, Qt.AlignmentFlag.AlignTop)

        header_text_column = QVBoxLayout()
        header_text_column.setContentsMargins(0, 0, 0, 0)
        header_text_column.setSpacing(2)

        self.header_title = QLabel("Help")
        self.header_title.setObjectName("helpPaneTitle")
        header_text_column.addWidget(self.header_title)

        self.header_body = QLabel("")
        self.header_body.setObjectName("helpPaneMeta")
        self.header_body.setWordWrap(True)
        header_text_column.addWidget(self.header_body)
        header_row.addLayout(header_text_column, 1)

        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("helpCloseButton")
        self.close_button.clicked.connect(self.close)
        header_row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(header_row)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("helpScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content_layout.addWidget(self.scroll_area, 1)

        root_layout.addWidget(rail)
        root_layout.addWidget(divider)
        root_layout.addWidget(content_panel, 1)

        self._build_category_buttons()
        self._apply_panel_styles()
        self.set_current_section(self.SECTION_DEFS[0][0])

    def _build_category_buttons(self):
        while self.category_button_column.count():
            item = self.category_button_column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.category_buttons.clear()
        for section_name, icon_name, _, _ in self.SECTION_DEFS:
            button = HelpCategoryButton(section_name, icon_name, self)
            button.clicked.connect(lambda checked=False, name=section_name: self.set_current_section(name))
            self.category_button_column.addWidget(button)
            self.category_buttons[section_name] = button

        self.category_button_column.addStretch(1)

    def _apply_panel_styles(self):
        palette = config.get_current_palette()
        accent = palette.SELECTION.name()
        panel_gray = "rgba(42, 42, 42, 248)"
        line_gray = "rgba(255, 255, 255, 0.08)"
        muted_text = "#8d8d8d"
        soft_text = "#bfc4ca"
        hover_gray = "rgba(255, 255, 255, 0.055)"
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.setStyleSheet(f"""
            QFrame#helpFlyoutPanel {{
                background: transparent;
                border: none;
            }}
            QFrame#helpFlyoutShell {{
                background-color: {panel_gray};
                border: 1px solid {line_gray};
                border-radius: 14px;
            }}
            QFrame#helpFlyoutShell QLabel,
            QFrame#helpFlyoutShell QWidget {{
                background: transparent;
            }}
            QWidget#helpCategoryRail, QWidget#helpPane {{
                background: transparent;
            }}
            QFrame#helpFlyoutDivider {{
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                margin-top: 10px;
                margin-bottom: 10px;
            }}
            QLabel#helpSectionLabel {{
                color: {muted_text};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.14em;
                background: transparent;
            }}
            QLabel#helpRailIntro {{
                color: {muted_text};
                font-size: 11px;
                background: transparent;
                padding: 0 2px 4px 2px;
            }}
            QPushButton#helpCategoryButton {{
                background-color: transparent;
                color: {soft_text};
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 10px 12px;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#helpCategoryButton:hover {{
                background-color: {hover_gray};
                border-color: rgba(255, 255, 255, 0.05);
                color: #ffffff;
            }}
            QPushButton#helpCategoryButton:checked {{
                background-color: rgba(255, 255, 255, 0.06);
                border-color: rgba(255, 255, 255, 0.08);
                color: #ffffff;
            }}
            QLabel#helpCategoryIcon {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#helpPaneTitle {{
                color: #f3f5f8;
                font-size: 15px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#helpPaneMeta {{
                color: {muted_text};
                font-size: 11px;
                background: transparent;
            }}
            QScrollArea#helpScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollArea#helpScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
            QPushButton#helpCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #f3f5f8;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#helpCloseButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QWidget#helpScrollContent {{
                background: transparent;
            }}
            QWidget#helpSectionBlock {{
                background: transparent;
            }}
            QWidget#helpItemText {{
                background: transparent;
            }}
            QLabel#helpSectionTitle {{
                color: {accent};
                font-size: 15px;
                font-weight: 700;
                padding-bottom: 6px;
                background: transparent;
            }}
            QFrame#helpItemCard {{
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 10px;
            }}
            QLabel#helpItemBadge {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#helpItemAction {{
                color: #ffffff;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#helpItemDescription {{
                color: {muted_text};
                font-size: 11px;
                background: transparent;
            }}
        """)
        self._accent_color = accent

    def set_current_section(self, section_name):
        section_def = next((item for item in self.SECTION_DEFS if item[0] == section_name), None)
        if section_def is None:
            return

        self.current_section_name = section_name
        for name, button in self.category_buttons.items():
            button.setChecked(name == section_name)

        _, icon_name, description, sections = section_def
        self.header_title.setText(section_name)
        self.header_body.setText(description)
        self.section_icon.setPixmap(qta.icon(icon_name, color=self._accent_color).pixmap(14, 14))
        self.scroll_area.setWidget(self._build_content_widget(sections))

    def _build_content_widget(self, sections):
        content = QWidget()
        content.setObjectName("helpScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(16)

        for title, items in sections:
            layout.addWidget(self._create_section(title, items))

        layout.addStretch(1)
        return content

    def _create_section(self, title, items):
        section = QWidget()
        section.setObjectName("helpSectionBlock")
        layout = QVBoxLayout(section)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel(title)
        title_label.setObjectName("helpSectionTitle")
        layout.addWidget(title_label)

        for action, description, icon_name in items:
            item_widget = QFrame()
            item_widget.setObjectName("helpItemCard")
            item_layout = QHBoxLayout(item_widget)
            item_layout.setSpacing(12)
            item_layout.setContentsMargins(12, 10, 12, 10)

            icon_label = QLabel()
            icon_label.setObjectName("helpItemBadge")
            icon_label.setFixedSize(28, 28)
            icon_label.setPixmap(qta.icon(icon_name, color=self._accent_color).pixmap(14, 14))
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

            text_widget = QWidget()
            text_widget.setObjectName("helpItemText")
            text_layout = QVBoxLayout(text_widget)
            text_layout.setSpacing(4)
            text_layout.setContentsMargins(0, 0, 0, 0)

            action_label = QLabel(action)
            action_label.setObjectName("helpItemAction")
            text_layout.addWidget(action_label)

            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            desc_label.setObjectName("helpItemDescription")
            text_layout.addWidget(desc_label)

            item_layout.addWidget(text_widget, 1)
            layout.addWidget(item_widget)

        return section

    def show_for_anchor(self, anchor_widget):
        self._apply_panel_styles()
        self.resize(900, 620)

        target_global = anchor_widget.mapToGlobal(QPoint(anchor_widget.width() - self.width(), anchor_widget.height() + 6))
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            max_x = available_geometry.right() - self.width() - 12
            max_y = available_geometry.bottom() - self.height() - 12
            x = max(available_geometry.left() + 12, min(x, max_x))
            y = max(available_geometry.top() + 12, min(y, max_y))

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def on_theme_changed(self):
        self._apply_panel_styles()
