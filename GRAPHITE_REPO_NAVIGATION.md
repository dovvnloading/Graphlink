# Graphite Repo Navigation

Living navigation document for the Graphite / Graphlink codebase.

Primary goal: give future work a reliable, current map of where behavior actually lives so we do not need to re-discover the repo from scratch every session.

Last refreshed: 2026-04-02

## Repo Snapshot

- Product name in the UI: `Graphlink`
- Repo / module naming in code: `Graphite`
- Code root: `graphite_app/`
- Startup project: `graphite_app/graphite_app.pyproj`
- Solution file: `graphite_app.sln`
- Python files under `graphite_app/` excluding `__pycache__`: `93`
- Top-level Python modules under `graphite_app/`: `51`
- Concrete top-level modules: `33`
- Top-level facades:
  - `17` compatibility wrappers
  - `1` broad agent facade: `graphite_agents.py`
- Real package directories with `__init__.py`: `5`
  - `graphite_canvas/` (`8` Python files)
  - `graphite_nodes/` (`11` Python files)
  - `graphite_plugins/` (`10` Python files)
  - `graphite_ui_dialogs/` (`4` Python files)
  - `graphite_widgets/` (`9` Python files)
- Runtime persistence outside the repo:
  - chats database: `~/.graphlink/chats.db`
  - settings/session state: `~/.graphlink/session.dat`
- Hardcoded repo-local icon path appears in multiple UI files:
  - `C:\Users\Admin\source\repos\graphite_app\assets\graphite.ico`

## Read This First

If you need to rebuild the mental model quickly, open files in this order:

1. `graphite_app/graphite_app.py`
2. `graphite_app/graphite_window.py`
3. `graphite_app/graphite_window_actions.py`
4. `graphite_app/graphite_view.py`
5. `graphite_app/graphite_scene.py`
6. `graphite_app/graphite_core.py`
7. `graphite_app/graphite_memory.py`
8. `graphite_app/graphite_plugins/graphite_plugin_portal.py`
9. `graphite_app/api_provider.py`
10. `graphite_app/graphite_agents.py`
11. `graphite_app/graphite_widgets/__init__.py`

That path shows boot, shell ownership, message dispatch, viewport behavior, scene ownership, persistence, branch memory, plugin registration, provider plumbing, agent facades, and reusable widgets.

## Architecture Truths That Matter

### 1. This is still a flat-import app with package islands

- Root modules import each other by top-level names such as `from graphite_window import ChatWindow`.
- The app is not organized as a fully namespaced package-first design.
- The split packages are real, but much of the running app still enters them through top-level compatibility modules.

### 2. The repo is mid-migration toward split packages

- Concrete implementations now live in `graphite_nodes/`, `graphite_canvas/`, `graphite_plugins/`, `graphite_ui_dialogs/`, and `graphite_widgets/`.
- Top-level wrappers preserve import stability.
- When both a wrapper and a package module exist, edit the concrete package module unless you are intentionally changing the public import surface.

### 3. The runtime shell is split cleanly across four files

- `graphite_window.py` owns the main window, toolbar, input row, overlays, plugin picker, and worker-thread handles.
- `graphite_window_actions.py` owns prompt dispatch, response parsing, chart/image generation, and `execute_*_node()` workflows.
- `graphite_window_navigation.py` owns keyboard navigation, command palette entry points, and canvas commands.
- `graphite_view.py` and `graphite_scene.py` own the actual graph surface.

### 4. `ChatScene` and `ChatSessionManager` are the real schema authorities

- `graphite_scene.py` defines which node lists and connection lists exist at runtime.
- `graphite_core.py` decides which node types and connection types are serialized, deserialized, and considered load-compatible.
- If you add a new node family and only wire the UI, the app is still incomplete until `ChatScene` and `ChatSessionManager` know about it.

### 5. Package `__init__` files are convenience surfaces, not perfect truth

- `graphite_nodes/__init__.py`, `graphite_canvas/__init__.py`, and `graphite_widgets/__init__.py` are useful.
- `graphite_plugins/__init__.py` is not exhaustive; for example, it does not export the code-review plugin types even though the top-level compatibility wrapper exists.
- For plugin work, the most authoritative files are the concrete modules plus `graphite_plugins/graphite_plugin_portal.py`.

### 6. There are a few easy-to-misread legacy seams

- `graphite_dialogs.py` duplicates canvas dialog classes but appears unused by repo imports; the live canvas dialog implementations are in `graphite_canvas/graphite_canvas_dialogs.py`.
- `graphite_widgets/pins.py` defines a lightweight overlay-side `NavigationPin`; `graphite_canvas/graphite_canvas_navigation_pin.py` defines the persisted scene item with the same name.
- `graphite_widgets/*.py` use UTF-8 BOM; tooling that parses them directly should use `utf-8-sig`.

## Runtime Ownership Map

### Boot and application shell

- `graphite_app/graphite_app.py`
  - `main()`
  - Creates `QApplication`, loads persisted settings, applies theme/model, creates `ChatWindow`, `WelcomeScreen`, and `SplashScreen`.
- `graphite_app/graphite_window.py`
  - `ChatWindow`
  - Main shell, toolbar, document viewer panel, pin overlay, token counter, attachment staging, plugin picker, shortcuts, and session lifecycle.
- `graphite_app/graphite_window_actions.py`
  - `WindowActionsMixin`
  - Core prompt send flow, attachment packing, response parsing, regeneration, takeaways, group summaries, explainers, charts, images, and all plugin execution entry points.
- `graphite_app/graphite_window_navigation.py`
  - `WindowNavigationMixin`
  - Command registration, collapse/expand/delete/focus commands, note creation, directional navigation, command palette.
- `graphite_app/graphite_command_palette.py`
  - `CommandManager`, `CommandPaletteDialog`
  - Generic command registration and searchable palette dialog.

### Canvas, graph surface, and layout

- `graphite_app/graphite_view.py`
  - `ChatView`
  - `QGraphicsView` wrapper, panning/zooming, drag-and-drop attachments, custom scrollbars, overlay widgets, minimap mounting, background grid, keyboard pan.
- `graphite_app/graphite_scene.py`
  - `ChatScene`
  - Node registries, connection registries, node creation helpers, search, frame/container/note/chart creation, delete logic, branch visibility, font propagation.
- `graphite_app/graphite_connections.py`
  - Core connection types: `ConnectionItem`, `ContentConnectionItem`, `DocumentConnectionItem`, `ImageConnectionItem`, `ThinkingConnectionItem`, `SystemPromptConnectionItem`, `PyCoderConnectionItem`, `ConversationConnectionItem`, `ReasoningConnectionItem`, `GroupSummaryConnectionItem`, `HtmlConnectionItem`
  - Shared pin behavior and connection path updates.
- `graphite_app/graphite_minimap.py`
  - `MinimapWidget`
  - Graph overview and node jump navigation.

### Persistence, context, and attachments

- `graphite_app/graphite_core.py`
  - `ChatDatabase`, `SaveWorkerThread`, `ChatSessionManager`, `TitleGenerator`
  - SQLite schema, save/load pipeline, node serialization, connection serialization, note/pin persistence, graph reconstruction, chat title generation.
- `graphite_app/graphite_memory.py`
  - `clone_history`, `append_history`, `assign_history`, `resolve_context_anchor`, `resolve_branch_parent`, `get_node_history`, `trim_history`, `history_to_transcript`
  - Branch-memory utilities; do not hand-roll history mutation when these helpers already exist.
- `graphite_app/graphite_file_handler.py`
  - `FileHandler`
  - Attachment readability checks and text extraction for plain text, code, PDF, and DOCX.
- `graphite_app/graphite_exporter.py`
  - `Exporter`
  - Export helpers used by node context menus.

### Providers, prompts, settings, and themes

- `graphite_app/api_provider.py`
  - Provider/runtime abstraction for Ollama, OpenAI-compatible APIs, and Gemini.
  - Chat, image generation, task-model routing, endpoint initialization, provider-mode switching.
- `graphite_app/graphite_prompts.py`
  - `BASE_SYSTEM_PROMPT`, `THINKING_INSTRUCTIONS_PROMPT`, `_TokenBytesEncoder`
  - Global prompt text and token-safe JSON encoding helpers.
- `graphite_app/graphite_config.py`
  - Theme palette getters, semantic colors, graph-node colors, theme application, current model assignment.
- `graphite_app/graphite_licensing.py`
  - `SettingsManager`
  - Persisted user settings: theme, welcome screen, token counter, system prompt toggle, local model, reasoning mode, API endpoint config, API models, GitHub token.
- `graphite_app/graphite_styles.py`
  - `StyleSheet`, `ColorPalette`, `THEMES`
  - QSS themes and palette definitions for `dark` and `mono`.

### Shared UI, welcome flow, and dialogs

- `graphite_app/graphite_ui_components.py`
  - `NotificationBanner`, `DocumentViewerPanel`, `CustomTitleBar`
  - `NotificationBanner` and `DocumentViewerPanel` are active; `CustomTitleBar` exists but is not imported elsewhere.
- `graphite_app/graphite_welcome_screen.py`
  - `WelcomeScreen`, `GridBackgroundWidget`, `StarterNodeWidget`, `ProjectButton`
  - Splash-follow welcome screen, recent chats, starter templates.
- `graphite_app/graphite_ui_dialogs/graphite_library_dialog.py`
  - `ChatLibraryDialog`
  - Recent chat browser, rename/delete/load.
- `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`
  - `SettingsDialog`, provider widgets, appearance settings, integrations settings.
- `graphite_app/graphite_ui_dialogs/graphite_system_dialogs.py`
  - `HelpDialog`, `AboutDialog`
  - Static product/help UI.

### Agents and background workers

- `graphite_app/graphite_agents.py`
  - Broad facade used by the shell and settings code.
- `graphite_app/graphite_agents_core.py`
  - Standard chat, explainer, takeaway, and group-summary agents plus worker threads.
- `graphite_app/graphite_agents_tools.py`
  - Chart data extraction/repair, image generation, model pull workers.
- `graphite_app/graphite_agents_pycoder.py`
  - Python REPL, execution/repair/analysis agents, Py-Coder workers.
- `graphite_app/graphite_agents_code_sandbox.py`
  - Virtualenv sandbox, generation/repair agents, isolated execution worker.
- `graphite_app/graphite_agents_web.py`
  - Search/fetch/validate/summarize worker for the web node.
- `graphite_app/graphite_agents_reasoning.py`
  - Multi-step reasoning workflow and worker thread.

## Concrete Node and Connection Taxonomy

### Persisted node types in `ChatSessionManager.serialize_node()`

- `chat`
- `code`
- `document`
- `image`
- `thinking`
- `pycoder`
- `code_sandbox`
- `web`
- `conversation`
- `reasoning`
- `html`
- `artifact`
- `workflow`
- `graph_diff`
- `quality_gate`
- `code_review`
- `gitlink`

### Other persisted scene objects

- frames
- containers
- notes
- charts
- navigation pins

### Connection families present in `ChatScene` and `ChatSessionManager`

- base conversation graph: `connections`
- `content_connections`
- `document_connections`
- `image_connections`
- `thinking_connections`
- `system_prompt_connections`
- `pycoder_connections`
- `code_sandbox_connections`
- `web_connections`
- `conversation_connections`
- `reasoning_connections`
- `group_summary_connections`
- `html_connections`
- `artifact_connections`
- `workflow_connections`
- `graph_diff_connections`
- `quality_gate_connections`
- `code_review_connections`
- `gitlink_connections`

## Core Runtime Flows

### 1. Application boot

1. `graphite_app/graphite_app.py:main()`
2. `graphite_licensing.SettingsManager()`
3. `graphite_config.apply_theme()`
4. `graphite_config.set_current_model()`
5. `graphite_window.ChatWindow`
6. `graphite_welcome_screen.WelcomeScreen`
7. `graphite_widgets.SplashScreen`

### 2. Normal prompt send / response flow

1. `WindowActionsMixin.send_message()`
2. `graphite_memory.resolve_branch_parent()` and `get_node_history()`
3. `ChatScene.add_chat_node()` creates the user node
4. `FileHandler` reads attachment text or image bytes when needed
5. `trim_history()` bounds the context window
6. `ChatWorkerThread` runs `ChatAgent`
7. `ChatWorker` optionally swaps in a branch system-prompt note
8. `api_provider.chat()` executes the provider call
9. `WindowActionsMixin.handle_response()` parses plain text, code blocks, and thinking blocks
10. `ChatScene.add_chat_node()`, `add_code_node()`, and `add_thinking_node()` create the result structure
11. `ChatWindow.save_chat()` persists the updated graph

### 3. Plugin lifecycle

1. `PluginPortal` registers plugin metadata and categories
2. The plugin picker surfaces that catalog
3. `_create_*_node()` methods add plugin nodes and specialized connections to `ChatScene`
4. `WindowActionsMixin.execute_*_node()` starts the relevant worker thread
5. Worker thread updates the node UI
6. `ChatSessionManager` serializes the node and its specialized connections
7. Delete logic in `ChatScene` and plugin `dispose()` methods performs cleanup

### 4. Save / load flow

1. `ChatWindow.save_chat()`
2. `ChatSessionManager._get_serialized_chat_data()`
3. `ChatDatabase.save_chat()` or `update_chat()`
4. Main graph JSON goes into `chats.data`
5. Notes and pins are stored in dedicated SQLite tables
6. `ChatSessionManager.load_chat()` deserializes nodes first, then notes/charts/frames/containers, then connections, then pins

### 5. Branch memory / anti-drift flow

Use the helpers in `graphite_app/graphite_memory.py`:

- `clone_history()`
- `append_history()`
- `assign_history()`
- `resolve_context_anchor()`
- `resolve_branch_parent()`
- `get_node_history()`
- `trim_history()`
- `history_to_transcript()`

Rule of thumb:

- Never share mutable history lists by reference across nodes.
- Never replace these helpers with ad hoc list slicing or shallow copies.
- Planner / reasoning / review / comparison plugins should consume deliberate branch context, not arbitrary graph-wide state.

## Plugin Catalog As Registered Today

This is the live registration order in `graphite_plugins/graphite_plugin_portal.py`.

### Branch Foundations

- `System Prompt`
- `Conversation Node`

### Reasoning & Research

- `Graphlink-Reasoning`
- `Graphlink-Web`

### Validation & Delivery

- `Branch Lens`
- `Quality Gate`
- `Code Review Agent`

### Build & Execution

- `Gitlink`
- `Py-Coder`
- `Execution Sandbox`
- `HTML Renderer`

### Workflow & Drafting

- `Workflow Architect`
- `Artifact / Drafter`

## Compatibility Wrapper Map

These top-level files are import-stability shims, not the main implementation.

### Node wrapper

- `graphite_app/graphite_node.py` -> `graphite_app/graphite_nodes/*`

### Canvas wrappers

- `graphite_app/graphite_canvas_items.py` -> `graphite_app/graphite_canvas/__init__.py`
- `graphite_app/graphite_canvas_groups.py` -> `graphite_app/graphite_canvas/__init__.py`
- `graphite_app/graphite_canvas_note_items.py` -> `graphite_app/graphite_canvas/__init__.py`
- `graphite_app/graphite_canvas_dialogs.py` -> `graphite_app/graphite_canvas/graphite_canvas_dialogs.py`

### Dialog wrappers

- `graphite_app/graphite_library_dialog.py` -> `graphite_app/graphite_ui_dialogs/graphite_library_dialog.py`
- `graphite_app/graphite_settings_dialogs.py` -> `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`
- `graphite_app/graphite_system_dialogs.py` -> `graphite_app/graphite_ui_dialogs/graphite_system_dialogs.py`

### Plugin wrappers

- `graphite_app/graphite_plugin_artifact.py` -> `graphite_app/graphite_plugins/graphite_plugin_artifact.py`
- `graphite_app/graphite_plugin_code_review.py` -> `graphite_app/graphite_plugins/graphite_plugin_code_review.py`
- `graphite_app/graphite_plugin_code_sandbox.py` -> `graphite_app/graphite_plugins/graphite_plugin_code_sandbox.py`
- `graphite_app/graphite_plugin_gitlink.py` -> `graphite_app/graphite_plugins/graphite_plugin_gitlink.py`
- `graphite_app/graphite_plugin_graph_diff.py` -> `graphite_app/graphite_plugins/graphite_plugin_graph_diff.py`
- `graphite_app/graphite_plugin_picker.py` -> `graphite_app/graphite_plugins/graphite_plugin_picker.py`
- `graphite_app/graphite_plugin_portal.py` -> `graphite_app/graphite_plugins/graphite_plugin_portal.py`
- `graphite_app/graphite_plugin_quality_gate.py` -> `graphite_app/graphite_plugins/graphite_plugin_quality_gate.py`
- `graphite_app/graphite_plugin_workflow.py` -> `graphite_app/graphite_plugins/graphite_plugin_workflow.py`

### Agent facade

- `graphite_app/graphite_agents.py` re-exports the split `graphite_agents_*` modules

## Concrete File Index

This is the practical lookup map for where code actually lives today.

### Top-level concrete modules

- `api_provider.py`
  - Provider abstraction for chat and image generation
  - Key functions: `chat()`, `generate_image()`, `initialize_api()`, `get_available_models()`, `set_mode()`, `set_task_model()`
- `graphite_agents_code_sandbox.py`
  - Sandbox runtime and virtualenv execution
  - Key classes/functions: `SandboxStage`, `SandboxGenerationAgent`, `SandboxRepairAgent`, `VirtualEnvSandbox`, `CodeSandboxExecutionWorker`, `_normalize_requirements()`
- `graphite_agents_core.py`
  - Core chat/text agents and worker threads
  - Key classes: `ChatWorkerThread`, `ChatWorker`, `ChatAgent`, `ExplainerAgent`, `KeyTakeawayAgent`, `GroupSummaryAgent`
- `graphite_agents_pycoder.py`
  - Py-Coder execution pipeline
  - Key classes: `PythonREPL`, `CodeExecutionWorker`, `PyCoderExecutionAgent`, `PyCoderRepairAgent`, `PyCoderAnalysisAgent`, `PyCoderExecutionWorker`, `PyCoderAgentWorker`
- `graphite_agents_reasoning.py`
  - Reasoning-node execution runtime
  - Key classes: `ReasoningAgent`, `ReasoningWorkerThread`
- `graphite_agents_tools.py`
  - Charts, image generation, and model pull helpers
  - Key classes: `ChartDataAgent`, `ChartWorkerThread`, `ImageGenerationAgent`, `ImageGenerationWorkerThread`, `ModelPullWorkerThread`
- `graphite_agents_web.py`
  - Web-search runtime
  - Key classes: `WebSearchAgent`, `WebWorkerThread`
- `graphite_app.py`
  - App entry point
  - Key function: `main()`
- `graphite_command_palette.py`
  - Generic command palette
  - Key classes: `CommandManager`, `CommandPaletteDialog`
- `graphite_config.py`
  - Theme/model globals
  - Key functions: `get_current_palette()`, `get_semantic_color()`, `apply_theme()`, `set_current_model()`
- `graphite_connections.py`
  - Shared connection rendering and interaction layer
  - Key classes: `Pin`, `ConnectionItem`, `ContentConnectionItem`, `DocumentConnectionItem`, `ImageConnectionItem`, `ThinkingConnectionItem`, `SystemPromptConnectionItem`, `PyCoderConnectionItem`, `ConversationConnectionItem`, `ReasoningConnectionItem`, `GroupSummaryConnectionItem`, `HtmlConnectionItem`
- `graphite_conversation_node.py`
  - Self-contained chat thread node
  - Key classes: `ChatMessageBubbleItem`, `TypingIndicatorItem`, `ConversationNode`
- `graphite_core.py`
  - SQLite persistence and graph serialization authority
  - Key classes/functions: `TitleGenerator`, `ChatDatabase`, `SaveWorkerThread`, `ChatSessionManager`, `_process_content_for_serialization()`, `_process_content_for_deserialization()`
- `graphite_dialogs.py`
  - Legacy duplicate of canvas dialogs
  - Key classes: `ColorPickerDialog`, `PinEditDialog`
- `graphite_exporter.py`
  - Export helpers used by node context menus
  - Key class: `Exporter`
- `graphite_file_handler.py`
  - Attachment reader
  - Key class: `FileHandler`
- `graphite_html_view.py`
  - HTML render node and popout window
  - Key classes: `HtmlPopoutWindow`, `HtmlViewNode`
- `graphite_licensing.py`
  - Persisted settings manager
  - Key class: `SettingsManager`
- `graphite_memory.py`
  - Branch/history utilities
  - Key functions: `clone_history()`, `append_history()`, `assign_history()`, `resolve_context_anchor()`, `resolve_branch_parent()`, `get_node_history()`, `trim_history()`, `history_to_transcript()`
- `graphite_minimap.py`
  - Minimap UI
  - Key class: `MinimapWidget`
- `graphite_plugin_context_menu.py`
  - Shared context menu for plugin nodes
  - Key class: `PluginNodeContextMenu`
- `graphite_prompts.py`
  - Global prompt constants and token-safe encoder
  - Key symbols: `BASE_SYSTEM_PROMPT`, `THINKING_INSTRUCTIONS_PROMPT`, `_TokenBytesEncoder`
- `graphite_pycoder.py`
  - Py-Coder node UI
  - Key classes: `PythonHighlighter`, `CodeEditor`, `PyCoderMode`, `StatusTrackerWidget`, `PyCoderNode`
- `graphite_reasoning.py`
  - Reasoning node UI
  - Key class: `ReasoningNode`
- `graphite_scene.py`
  - Scene/controller authority
  - Key class: `ChatScene`
- `graphite_styles.py`
  - QSS themes and palettes
  - Key classes/symbols: `StyleSheet`, `ColorPalette`, `THEMES`
- `graphite_ui_components.py`
  - Shared shell widgets
  - Key classes: `CustomTitleBar`, `NotificationBanner`, `DocumentViewerPanel`
- `graphite_view.py`
  - Canvas viewport
  - Key class: `ChatView`
- `graphite_web.py`
  - Web node UI and its specialized connection item
  - Key classes: `WebConnectionItem`, `WebNode`
- `graphite_welcome_screen.py`
  - Welcome screen and starter UI
  - Key classes: `GridBackgroundWidget`, `StarterNodeWidget`, `ProjectButton`, `WelcomeScreen`
- `graphite_window.py`
  - Main shell
  - Key class: `ChatWindow`
- `graphite_window_actions.py`
  - Action dispatch and plugin execution
  - Key class: `WindowActionsMixin`
- `graphite_window_navigation.py`
  - Keyboard/navigation mixin
  - Key class: `WindowNavigationMixin`

### `graphite_nodes/`

- `graphite_nodes/__init__.py`
  - Re-exports the split node classes and context menus
- `graphite_nodes/graphite_node_chat.py`
  - `ChatNode`
- `graphite_nodes/graphite_node_chat_menu.py`
  - `ChatNodeContextMenu`
- `graphite_nodes/graphite_node_code.py`
  - `CodeHighlighter`, `CodeNode`
- `graphite_nodes/graphite_node_code_menu.py`
  - `CodeNodeContextMenu`
- `graphite_nodes/graphite_node_document.py`
  - `DocumentNode`
- `graphite_nodes/graphite_node_document_menu.py`
  - `DocumentNodeContextMenu`
- `graphite_nodes/graphite_node_image.py`
  - `ImageNode`
- `graphite_nodes/graphite_node_image_menu.py`
  - `ImageNodeContextMenu`
- `graphite_nodes/graphite_node_thinking.py`
  - `ThinkingNode`
- `graphite_nodes/graphite_node_thinking_menu.py`
  - `ThinkingNodeContextMenu`

### `graphite_canvas/`

- `graphite_canvas/__init__.py`
  - Re-exports canvas items and dialogs
- `graphite_canvas/graphite_canvas_base.py`
  - Shared canvas helpers
  - Key classes/functions: `HoverAnimationMixin`, `GhostFrame`, `CanvasHeaderLineEdit`, `iter_scene_connection_lists()`, `update_connections_for_items()`
- `graphite_canvas/graphite_canvas_chart_item.py`
  - `ChartItem`
- `graphite_canvas/graphite_canvas_container.py`
  - `Container`
- `graphite_canvas/graphite_canvas_dialogs.py`
  - `ColorPickerDialog`, `PinEditDialog`
- `graphite_canvas/graphite_canvas_frame.py`
  - `Frame`
- `graphite_canvas/graphite_canvas_navigation_pin.py`
  - Persisted scene `NavigationPin`
- `graphite_canvas/graphite_canvas_note.py`
  - `Note`

### `graphite_plugins/`

- `graphite_plugins/__init__.py`
  - Partial convenience exports; do not treat as exhaustive plugin truth
- `graphite_plugins/graphite_plugin_artifact.py`
  - Markdown drafting node
  - Key classes: `ArtifactInstructionInput`, `ArtifactAgent`, `ArtifactWorkerThread`, `ArtifactConnectionItem`, `ArtifactNode`
- `graphite_plugins/graphite_plugin_code_review.py`
  - Structured review node for local or GitHub files
  - Key classes/functions: `CodeReviewComboPopup`, `CodeReviewPopupComboBox`, `CodeReviewAnalyzer`, `CodeReviewWorkerThread`, `CodeReviewConnectionItem`, `CodeReviewNode`, `_prepare_numbered_source()`, `_source_scope_summary()`
- `graphite_plugins/graphite_plugin_code_sandbox.py`
  - Isolated execution node UI
  - Key classes: `SandboxStatusTracker`, `CodeSandboxConnectionItem`, `CodeSandboxNode`
- `graphite_plugins/graphite_plugin_gitlink.py`
  - GitHub/local-repo context and proposal workflow
  - Key classes/functions: `GitlinkAgent`, `GitlinkWorkerThread`, `GitlinkConnectionItem`, `GitlinkNode`, `load_github_repositories()`, `load_repository_tree()`, `get_selected_paths()`, `get_task_prompt()`, `clear_proposal()`, `apply_approved_changes()`
- `graphite_plugins/graphite_plugin_graph_diff.py`
  - Branch comparison / Branch Lens
  - Key classes/functions: `GraphDiffAnalyzer`, `GraphDiffWorkerThread`, `GraphDiffConnectionItem`, `GraphDiffNode`, `build_branch_payload()`
- `graphite_plugins/graphite_plugin_picker.py`
  - Plugin picker UI
  - Key classes: `PluginCategoryButton`, `PluginEntryCard`, `PluginFlyoutPanel`
- `graphite_plugins/graphite_plugin_portal.py`
  - Plugin registry and node creation authority
  - Key class: `PluginPortal`
- `graphite_plugins/graphite_plugin_quality_gate.py`
  - Release-readiness review node
  - Key classes/functions: `QualityGateAnalyzer`, `QualityGateWorkerThread`, `QualityGateRecommendationCard`, `QualityGateConnectionItem`, `QualityGateNode`, `build_quality_gate_payload()`
- `graphite_plugins/graphite_plugin_workflow.py`
  - Plan/recommendation node
  - Key classes: `WorkflowArchitectAgent`, `WorkflowWorkerThread`, `WorkflowRecommendationCard`, `WorkflowConnectionItem`, `WorkflowNode`

### `graphite_ui_dialogs/`

- `graphite_ui_dialogs/__init__.py`
  - Re-exports library/settings/system dialogs
- `graphite_ui_dialogs/graphite_library_dialog.py`
  - `ChatLibraryDialog`
- `graphite_ui_dialogs/graphite_settings_dialogs.py`
  - `SettingsComboPopup`, `SettingsComboBox`, `OllamaSettingsWidget`, `ApiSettingsWidget`, `IntegrationsSettingsWidget`, `AppearanceSettingsWidget`, `SettingsCategoryButton`, `SettingsDialog`
- `graphite_ui_dialogs/graphite_system_dialogs.py`
  - `AboutDialog`, `HelpCategoryButton`, `HelpDialog`

### `graphite_widgets/`

- `graphite_widgets/__init__.py`
  - Re-exports the reusable widget package
- `graphite_widgets/controls.py`
  - `FontControl`, `GridControl`
- `graphite_widgets/overlays.py`
  - `LoadingAnimation`, `SearchOverlay`
- `graphite_widgets/pins.py`
  - Overlay-side `NavigationPin` data helper plus `PinOverlay`
- `graphite_widgets/scrolling.py`
  - `CustomScrollBar`, `CustomScrollArea`, `ScrollHandle`, `ScrollBar`
- `graphite_widgets/splash.py`
  - `SplashAnimationWidget`, `AnimatedWordLogo`, `SplashScreen`
- `graphite_widgets/text_inputs.py`
  - `SpellCheckLineEdit`, `_BlackHoleEditor`, `ComposerSurface`, `ContextAttachmentPill`, `ChatInputTextEdit`
- `graphite_widgets/tokens.py`
  - `TokenEstimator`, `TokenCounterWidget`
- `graphite_widgets/tooltips.py`
  - `CustomTooltip`

## Where To Edit When...

### You want to change app startup or persisted defaults

- `graphite_app/graphite_app.py`
- `graphite_app/graphite_licensing.py`
- `graphite_app/graphite_config.py`
- `graphite_app/graphite_styles.py`

### You want to change prompt send, response parsing, attachment handling, or execution dispatch

- `graphite_app/graphite_window_actions.py`
- `graphite_app/graphite_memory.py`
- `graphite_app/graphite_file_handler.py`
- `graphite_app/api_provider.py`

### You want to change canvas behavior, graph layout, selection, deletion, or visibility

- `graphite_app/graphite_view.py`
- `graphite_app/graphite_scene.py`
- `graphite_app/graphite_connections.py`
- `graphite_app/graphite_minimap.py`
- `graphite_app/graphite_canvas/*`

### You want to add or modify a node family

- Core chat/code/doc/image/thinking nodes:
  - `graphite_app/graphite_nodes/*`
- Specialized nodes:
  - `graphite_app/graphite_pycoder.py`
  - `graphite_app/graphite_web.py`
  - `graphite_app/graphite_conversation_node.py`
  - `graphite_app/graphite_reasoning.py`
  - `graphite_app/graphite_html_view.py`
- Then update:
  - `graphite_app/graphite_scene.py`
  - `graphite_app/graphite_core.py`
  - `graphite_app/graphite_window.py`
  - `graphite_app/graphite_window_actions.py`

### You want to add or modify a plugin

- Registration and category metadata:
  - `graphite_app/graphite_plugins/graphite_plugin_portal.py`
- Picker UI:
  - `graphite_app/graphite_plugins/graphite_plugin_picker.py`
- Shared plugin context menu:
  - `graphite_app/graphite_plugin_context_menu.py`
- Concrete plugin logic:
  - `graphite_app/graphite_plugins/graphite_plugin_*.py`
- Then verify:
  - `graphite_app/graphite_scene.py`
  - `graphite_app/graphite_core.py`
  - `graphite_app/graphite_window_actions.py`

### You want to change save/load compatibility

- `graphite_app/graphite_core.py`
- `graphite_app/graphite_scene.py`

### You want to change reusable widgets or overlays

- `graphite_app/graphite_widgets/*`
- `graphite_app/graphite_ui_components.py`

## Short Working Rules For Future Sessions

- Open the concrete package file before touching a compatibility wrapper.
- Treat `ChatScene` plus `ChatSessionManager` as the graph schema.
- Treat `WindowActionsMixin` as the execution dispatcher.
- Treat `PluginPortal` as the plugin catalog authority.
- Treat `graphite_memory.py` as the only safe place to define branch-history semantics.
- Be careful with duplicate names:
  - scene `NavigationPin` lives in `graphite_canvas`
  - overlay `NavigationPin` lives in `graphite_widgets`
- Be careful with legacy files:
  - `graphite_dialogs.py` is not the live canvas dialog authority
- Be careful with machine-specific paths:
  - the icon path is currently hardcoded to one local repo location
