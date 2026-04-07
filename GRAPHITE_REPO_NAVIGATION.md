# Graphite Repo Navigation

Living navigation document for the Graphite / Graphlink codebase.

Primary goal: give future work a reliable, current map of where behavior actually lives so we do not need to re-discover the repo from scratch every session.

Last refreshed: 2026-04-07

## Repo Snapshot

- Product name in the UI: `Graphlink`
- Repo / module naming in code: `Graphite`
- Code root: `graphite_app/`
- Startup project: `graphite_app/graphite_app.pyproj`
- Solution file: `graphite_app.sln`
- Python files under `graphite_app/` excluding `__pycache__`: `107`
- Top-level Python modules under `graphite_app/`: `55`
- Real package directories with `__init__.py`: `6`
  - `graphite_canvas/` (`8` Python files)
  - `graphite_nodes/` (`11` Python files)
  - `graphite_plugins/` (`10` Python files)
  - `graphite_session/` (`9` Python files)
  - `graphite_ui_dialogs/` (`4` Python files)
  - `graphite_widgets/` (`10` Python files)
- Runtime modes exposed in the shell:
  - `Ollama (Local)`
  - `Llama.cpp (Local)`
  - `API Endpoint`
- Runtime persistence outside the repo:
  - chats database: `~/.graphlink/chats.db`
  - settings/session state: `~/.graphlink/session.dat`
- Hardcoded repo-local asset paths still exist in UI code:
  - `C:\Users\Admin\source\repos\graphite_app\assets\graphite.ico`
  - `C:\Users\Admin\source\repos\graphite_app\assets\check.png`
  - `C:\Users\Admin\source\repos\graphite_app\assets\down_arrow.png`

## Read This First

If you need to rebuild the mental model quickly, open files in this order:

1. `graphite_app/graphite_app.py`
2. `graphite_app/graphite_window.py`
3. `graphite_app/graphite_window_actions.py`
4. `graphite_app/api_provider.py`
5. `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`
6. `graphite_app/graphite_scene.py`
7. `graphite_app/graphite_session/manager.py`
8. `graphite_app/graphite_session/serializers.py`
9. `graphite_app/graphite_session/deserializers.py`
10. `graphite_app/graphite_session/scene_index.py`
11. `graphite_app/graphite_plugins/graphite_plugin_portal.py`
12. `graphite_app/graphite_memory.py`
13. `graphite_app/graphite_lod.py`

That path shows boot, shell ownership, provider/mode initialization, live settings UI, scene authority, persistence, schema indexing, plugin registration, branch-memory rules, and the shared zoom-based render fallback system.

## Architecture Truths That Matter

### 1. This is still a flat-import app with package islands

- Root modules still import each other by top-level names such as `from graphite_window import ChatWindow`.
- The split packages are real, but the running app is not yet a clean package-first namespaced design.
- Compatibility wrappers still matter because much of the repo enters package code through those top-level modules.

### 2. The repo is still mid-migration toward split packages

- Concrete implementations increasingly live in `graphite_nodes/`, `graphite_canvas/`, `graphite_plugins/`, `graphite_session/`, `graphite_ui_dialogs/`, and `graphite_widgets/`.
- Top-level wrappers still preserve import stability.
- When both a wrapper and a concrete package module exist, edit the concrete package module unless you are intentionally changing the import surface.

### 3. Runtime mode handling is now a first-class architecture seam

- `graphite_config.py` defines task keys and user-facing mode labels.
- `graphite_licensing.py` persists per-mode settings, scan caches, update-check state, and current mode.
- `graphite_ui_dialogs/graphite_settings_dialogs.py` is the live configuration surface for Ollama, Llama.cpp, API providers, integrations, and update controls.
- `graphite_window.py` owns startup mode initialization and toolbar mode switching.
- `api_provider.py` is the real execution authority for:
  - Ollama local runtime
  - direct `llama-cpp-python` GGUF runtime
  - OpenAI-compatible endpoints
  - Gemini endpoints

### 4. `Llama.cpp (Local)` is direct GGUF execution, not Ollama reuse

- The app scans for `.gguf` files directly.
- Settings persist:
  - chat model path
  - optional title model path
  - chat format override
  - `n_ctx`
  - `n_gpu_layers`
  - `n_threads`
- Ollama manifests/blobs are not valid `Llama.cpp` model files in this mode.
- Graphlink intentionally defers GGUF loading until the first request instead of blocking mode switching or Save Settings.

### 5. Attachments are now multimodal, and document nodes also carry audio

- `graphite_window.py` stages image, document, and audio attachments.
- `graphite_window_actions.py` turns:
  - image attachments into `ImageNode`
  - document attachments into `DocumentNode`
  - audio attachments into `DocumentNode` with `attachment_kind='audio'`
- `graphite_audio.py` validates audio files, MIME types, duration limits, and preview labels.
- `graphite_nodes/graphite_node_document.py` is now the live UI for both document and audio attachment nodes.
- Graphlink-level limitation:
  - `Llama.cpp` local mode is text-only inside the app right now
  - Ollama and Gemini are the multimodal paths for audio or image-backed requests

### 6. `ChatScene`, `ChatSessionManager`, and `graphite_session/scene_index.py` now form the schema triangle

- `graphite_scene.py` still owns the live runtime lists and creation/deletion behavior.
- `graphite_session/serializers.py` and `graphite_session/deserializers.py` still decide save/load compatibility.
- `graphite_session/scene_index.py` now centralizes:
  - node list names
  - save-guard node list names
  - child-link-capable node types
  - serializer/deserializer item indexing helpers
- If you add a new persisted node family, update all three places:
  - `graphite_scene.py`
  - `graphite_session/scene_index.py`
  - session serializer/deserializer code

### 7. A shared LoD/proxy render layer now matters across many node families

- `graphite_lod.py` owns zoom thresholds, summary/glyph fallback rendering, preview text helpers, and proxy visibility rules.
- Many node UIs now rely on it for readable zoomed-out behavior instead of each node hand-rolling its own fallback.
- If a node looks wrong when zoomed out, `graphite_lod.py` is usually as important as the node class itself.

### 8. Shared visuals are a little more centralized than before

- `graphite_widgets/loading_visuals.py` now owns the shared orbital spinner painting used by both the splash and loading overlays.
- `graphite_update.py` plus `graphite_version.py` own the update-check signal and local version metadata.

### 9. There are still easy-to-misread legacy seams

- `graphite_dialogs.py` duplicates canvas dialog classes but does not appear to be the live authority.
- `graphite_widgets/pins.py` defines overlay-side `NavigationPin`; `graphite_canvas/graphite_canvas_navigation_pin.py` defines the persisted scene item with the same name.
- `graphite_widgets/*.py` still use UTF-8 BOM in places; direct parsing tools should be BOM-aware.

## Runtime Ownership Map

### Boot and application shell

- `graphite_app/graphite_app.py`
  - `main()`
  - Creates `QApplication`, loads persisted settings, applies theme/model, creates `ChatWindow`, `WelcomeScreen`, and `SplashScreen`.
- `graphite_app/graphite_window.py`
  - `ChatWindow`
  - Main shell, toolbar, document viewer panel, pin overlay, mode switching, update checks, plugin picker, attachment staging, shortcuts, and session lifecycle.
- `graphite_app/graphite_window_actions.py`
  - `WindowActionsMixin`
  - Core prompt send flow, attachment packing, response parsing, regeneration, charts, images, and all plugin execution entry points.
- `graphite_app/graphite_window_navigation.py`
  - `WindowNavigationMixin`
  - Command registration, collapse/expand/delete/focus commands, note creation, directional navigation, command palette.
- `graphite_app/graphite_command_palette.py`
  - `CommandManager`, `CommandPaletteDialog`
  - Searchable command palette.
- `graphite_app/graphite_update.py`
  - `UpdateCheckWorker`, version comparison helpers, update-signal fetch.
- `graphite_app/graphite_version.py`
  - `APP_VERSION`

### Canvas, graph surface, and layout

- `graphite_app/graphite_view.py`
  - `ChatView`
  - `QGraphicsView` wrapper, panning/zooming, drag-and-drop attachments, overlay widgets, minimap mounting, background grid, keyboard pan.
- `graphite_app/graphite_scene.py`
  - `ChatScene`
  - Node registries, connection registries, node creation helpers, search, frame/container/note/chart creation, delete logic, branch visibility, font propagation.
- `graphite_app/graphite_connections.py`
  - Core connection families and shared pin/path behavior.
- `graphite_app/graphite_minimap.py`
  - `MinimapWidget`
  - Graph overview and jump navigation.
- `graphite_app/graphite_lod.py`
  - Shared level-of-detail thresholds, preview text helpers, zoom-aware proxy visibility, and fallback card painting.

### Persistence, context, and attachments

- `graphite_app/graphite_session/`
  - Concrete persistence package.
  - Key files: `content_codec.py`, `database.py`, `deserializers.py`, `manager.py`, `scene_index.py`, `serializers.py`, `title_generator.py`, `workers.py`
- `graphite_app/graphite_core.py`
  - Compatibility facade for session persistence.
- `graphite_app/graphite_memory.py`
  - Branch-memory utilities; do not hand-roll history mutation when these helpers already exist.
- `graphite_app/graphite_file_handler.py`
  - Attachment readability checks and text extraction for plain text, code, PDF, and DOCX.
- `graphite_app/graphite_audio.py`
  - Audio validation, duration probing, MIME inference, and duration formatting.
- `graphite_app/graphite_exporter.py`
  - Export helpers used by node context menus.

### Providers, prompts, settings, updates, and themes

- `graphite_app/api_provider.py`
  - Provider/runtime abstraction for Ollama, direct Llama.cpp, OpenAI-compatible APIs, and Gemini.
  - Also owns local model scanning:
    - Ollama manifest scanning
    - GGUF scanning for `Llama.cpp`
  - Also owns modality handling rules and local runtime initialization.
- `graphite_app/graphite_prompts.py`
  - Global prompt text and token-safe JSON encoding helpers.
- `graphite_app/graphite_config.py`
  - Task keys, mode labels, local provider constants, theme palette getters, semantic colors, current model assignment.
- `graphite_app/graphite_licensing.py`
  - `SettingsManager`
  - Persisted user settings:
    - theme
    - welcome screen
    - token counter
    - system prompt toggle
    - current runtime mode
    - Ollama model settings and scan cache
    - Llama.cpp GGUF settings and scan cache
    - API endpoint/provider settings
    - GitHub token
    - update-check state
- `graphite_app/graphite_styles.py`
  - QSS themes and shared palette definitions.

### Shared UI, welcome flow, and dialogs

- `graphite_app/graphite_ui_components.py`
  - `NotificationBanner`, `DocumentViewerPanel`, `CustomTitleBar`
- `graphite_app/graphite_welcome_screen.py`
  - `WelcomeScreen`, starter templates, recent chats.
- `graphite_app/graphite_ui_dialogs/graphite_library_dialog.py`
  - `ChatLibraryDialog`
- `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`
  - Real settings surface.
  - Key sections:
    - `AppearanceSettingsWidget`
    - `OllamaSettingsWidget`
    - `LlamaCppSettingsWidget`
    - `ApiSettingsWidget`
    - `IntegrationsSettingsWidget`
    - `SettingsDialog`
- `graphite_app/graphite_ui_dialogs/graphite_system_dialogs.py`
  - `HelpDialog`, `AboutDialog`
- `graphite_app/graphite_widgets/loading_visuals.py`
  - Shared spinner painter for splash and overlay loading states.

### Agents and background workers

- `graphite_app/graphite_agents.py`
  - Broad facade used by shell and settings code.
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

### Persisted node types in the session payload

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

### Important current node-shape detail

- `document` nodes now carry both normal file attachments and audio attachments.
- Audio-backed `document` nodes persist extra fields such as:
  - `attachment_kind`
  - `file_path`
  - `mime_type`
  - `duration_seconds`
  - `byte_size`
  - `preview_label`

### Other persisted scene objects

- frames
- containers
- notes
- charts
- navigation pins

### Connection families present in `ChatScene` and session save/load

- `connections`
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
6. `ChatWindow._initialize_saved_mode_on_startup()`
7. Mode-specific initialization goes through `api_provider.initialize_local_provider()` or `api_provider.initialize_api()`
8. `graphite_welcome_screen.WelcomeScreen`
9. `graphite_widgets.SplashScreen`

### 2. Runtime mode initialization

1. `graphite_window.ChatWindow._initialize_mode()`
2. If `Ollama (Local)`:
   - `api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA)`
3. If `Llama.cpp (Local)`:
   - settings come from `SettingsManager.get_llama_cpp_settings()`
   - `api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, ..., preload_model=False)`
4. If `API Endpoint`:
   - provider/model settings come from `SettingsManager`
   - `api_provider.initialize_api(...)`
5. The settings flyout mirrors these same seams in `graphite_ui_dialogs/graphite_settings_dialogs.py`

### 3. Normal prompt send / attachment flow

1. `WindowActionsMixin.send_message()`
2. `graphite_memory.resolve_branch_parent()` and `get_node_history()`
3. `ChatScene.add_chat_node()` creates the user node
4. Pending attachments are expanded:
   - images become `ImageNode`
   - documents become `DocumentNode`
   - audio becomes `DocumentNode` with audio metadata
5. `trim_history()` bounds the context window
6. `ChatWorkerThread` runs the agent request
7. `api_provider.chat()` executes the chosen runtime path
8. `WindowActionsMixin.handle_response()` parses plain text, code blocks, and thinking blocks
9. `ChatScene.add_chat_node()`, `add_code_node()`, and `add_thinking_node()` create result structure
10. `ChatWindow.save_chat()` persists the updated graph

### 4. Save / load flow

1. `ChatWindow.save_chat()`
2. `ChatSessionManager.save_current_chat()`
3. `SceneSerializer.serialize_chat_data()`
4. `graphite_session.scene_index` supplies node list and indexing helpers
5. `ChatDatabase.save_chat()` or `update_chat()`
6. Notes and pins are stored in dedicated SQLite tables
7. `SceneDeserializer.restore_chat()` recreates nodes first, then notes/charts/frames/containers, then connections, then pins

### 5. Plugin lifecycle

1. `PluginPortal` registers plugin metadata and categories
2. The plugin picker surfaces that catalog
3. `_create_*_node()` methods add plugin nodes and specialized connections to `ChatScene`
4. `WindowActionsMixin.execute_*_node()` starts the relevant worker thread
5. Worker thread updates the node UI
6. `ChatSessionManager` serializes the node and its specialized connections
7. Delete logic in `ChatScene` and plugin `dispose()` methods performs cleanup

### 6. Title generation flow

1. `ChatSessionManager` delegates naming to `TitleGenerator`
2. If runtime is API mode or local Llama.cpp mode:
   - `TitleGenerator.generate_title()` routes through `api_provider.chat(task=config.TASK_TITLE, ...)`
3. If runtime is local Ollama mode:
   - `TitleGenerator` prefers configured/local Ollama naming models and falls back across installed candidates

### 7. Update-check flow

1. `graphite_window.ChatWindow.check_for_updates()`
2. `graphite_update.UpdateCheckWorker`
3. Remote version signal fetched from GitHub
4. `graphite_licensing.SettingsManager.record_update_check_result()`
5. `AppearanceSettingsWidget` surfaces the saved status and manual re-check action

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

### Top-level concrete modules that changed or matter most

- `api_provider.py`
  - Provider abstraction for Ollama, direct `Llama.cpp`, OpenAI-compatible chat/image APIs, and Gemini.
  - Key responsibilities:
    - local/runtime initialization
    - GGUF scanning
    - Ollama model scanning
    - modality preparation
    - `chat()`
    - `generate_image()`
- `graphite_audio.py`
  - Audio attachment validation and duration probing.
  - Key functions/classes: `AudioValidationError`, `is_supported_audio_file()`, `guess_audio_mime_type()`, `inspect_audio_file()`, `format_duration()`
- `graphite_lod.py`
  - Shared zoom-dependent render helpers.
  - Key helpers: `lod_mode_for_item()`, `preview_text()`, `sync_proxy_render_state()`, `draw_lod_card()`
- `graphite_update.py`
  - Update signal fetch and comparison logic.
  - Key symbols: `UPDATE_SIGNAL_URL`, `UPDATE_REPOSITORY_URL`, `UpdateCheckWorker`, `build_update_result()`
- `graphite_version.py`
  - Current local app version constant.
- `graphite_window.py`
  - Main shell, mode switching, toolbar, update checks, settings flyout, attachment staging.
- `graphite_window_actions.py`
  - Prompt dispatch, attachment packaging, response parsing, plugin execution.
- `graphite_scene.py`
  - Scene/controller authority.
- `graphite_memory.py`
  - Branch/history helpers.
- `graphite_plugin_context_menu.py`
  - Shared context menu for plugin nodes.

### `graphite_session/`

- `content_codec.py`
  - Serialization helpers for history and binary image content.
- `database.py`
  - SQLite persistence.
- `deserializers.py`
  - Concrete load compatibility and graph restoration.
- `manager.py`
  - `ChatSessionManager`
  - Coordinates save/load/title generation and delegates runtime helpers.
- `scene_index.py`
  - Centralized node-list/index helpers used by persistence code.
- `serializers.py`
  - Concrete save payload authority.
- `title_generator.py`
  - Chat naming strategy across Ollama, Llama.cpp, and API modes.
- `workers.py`
  - Background save worker.

### `graphite_ui_dialogs/`

- `graphite_library_dialog.py`
  - Recent chat browser.
- `graphite_settings_dialogs.py`
  - Live settings flyout.
  - This is the real place to edit:
    - Ollama scans
    - GGUF scans
    - API provider/model settings
    - GitHub token settings
    - update-check controls
- `graphite_system_dialogs.py`
  - About/help UI.

### `graphite_widgets/`

- `loading_visuals.py`
  - Shared spinner painter.
- `overlays.py`
  - `LoadingAnimation`, `SearchOverlay`
- `pins.py`
  - Overlay-side pin helper plus `PinOverlay`
- `splash.py`
  - Splash screen and animation
- `text_inputs.py`
  - Composer surface and attachment pills
- `tokens.py`
  - Token estimator and token counter widget

## Where To Edit When...

### You want to change startup, current mode handling, or mode switching

- `graphite_app/graphite_window.py`
- `graphite_app/graphite_config.py`
- `graphite_app/graphite_licensing.py`
- `graphite_app/api_provider.py`
- `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`

### You want to change Ollama scanning or default local models

- `graphite_app/api_provider.py`
- `graphite_app/graphite_config.py`
- `graphite_app/graphite_licensing.py`
- `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`

### You want to change direct `Llama.cpp` / GGUF behavior

- `graphite_app/api_provider.py`
- `graphite_app/graphite_licensing.py`
- `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`
- `graphite_app/graphite_window.py`
- `graphite_app/graphite_session/title_generator.py`

### You want to change prompt send, response parsing, attachment handling, or execution dispatch

- `graphite_app/graphite_window_actions.py`
- `graphite_app/graphite_memory.py`
- `graphite_app/graphite_file_handler.py`
- `graphite_app/graphite_audio.py`
- `graphite_app/api_provider.py`

### You want to change attachment staging or supported attachment kinds

- `graphite_app/graphite_window.py`
- `graphite_app/graphite_window_actions.py`
- `graphite_app/graphite_audio.py`
- `graphite_app/graphite_nodes/graphite_node_document.py`
- `graphite_app/graphite_session/serializers.py`
- `graphite_app/graphite_session/deserializers.py`

### You want to change canvas behavior, graph layout, or zoomed-out rendering

- `graphite_app/graphite_view.py`
- `graphite_app/graphite_scene.py`
- `graphite_app/graphite_connections.py`
- `graphite_app/graphite_minimap.py`
- `graphite_app/graphite_lod.py`
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
  - `graphite_app/graphite_session/scene_index.py`
  - `graphite_app/graphite_session/serializers.py`
  - `graphite_app/graphite_session/deserializers.py`
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
  - `graphite_app/graphite_session/scene_index.py`
  - `graphite_app/graphite_session/serializers.py`
  - `graphite_app/graphite_session/deserializers.py`
  - `graphite_app/graphite_window_actions.py`

### You want to change save/load compatibility

- `graphite_app/graphite_session/scene_index.py`
- `graphite_app/graphite_session/serializers.py`
- `graphite_app/graphite_session/deserializers.py`
- `graphite_app/graphite_session/manager.py`
- `graphite_app/graphite_core.py`
- `graphite_app/graphite_scene.py`

### You want to change update checks or version reporting

- `graphite_app/graphite_update.py`
- `graphite_app/graphite_version.py`
- `graphite_app/graphite_licensing.py`
- `graphite_app/graphite_window.py`
- `graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py`

### You want to change reusable widgets or loading visuals

- `graphite_app/graphite_widgets/*`
- `graphite_app/graphite_ui_components.py`

## Short Working Rules For Future Sessions

- Open the concrete package file before touching a compatibility wrapper.
- Treat `api_provider.py` as the runtime execution authority for every model mode.
- Treat `ChatScene` plus session serializer/deserializer code as the graph schema.
- Treat `graphite_session/scene_index.py` as the central list/index helper whenever you add a new persisted node family.
- Treat `WindowActionsMixin` as the execution dispatcher.
- Treat `PluginPortal` as the plugin catalog authority.
- Treat `graphite_memory.py` as the only safe place to define branch-history semantics.
- Treat `graphite_lod.py` as shared render infrastructure, not optional polish.
- Remember that `Llama.cpp` mode expects direct `.gguf` files, not Ollama blobs/manifests.
- Remember that audio attachments persist through `DocumentNode`, not a separate audio node type.
- Be careful with duplicate names:
  - scene `NavigationPin` lives in `graphite_canvas`
  - overlay `NavigationPin` lives in `graphite_widgets`
- Be careful with legacy files:
  - `graphite_dialogs.py` is not the live canvas dialog authority
- Be careful with machine-specific paths:
  - several asset paths are still hardcoded to one local repo location
