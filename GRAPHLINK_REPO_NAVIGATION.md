# Graphlink Repo Navigation

Living navigation document for the Graphlink codebase.

Primary goal: give future work a reliable, current map of where behavior actually lives so we do not need to re-discover the repo from scratch every session.

Last refreshed: 2026-07-08

## Repo Snapshot

- Product name in the UI: `Graphlink`
- Repo / module naming in code: `Graphlink`
- Code root: `graphlink_app/`
- Startup project: `graphlink_app/graphlink_app.pyproj`
- Solution file: `graphlink_app.sln`
- Python files under `graphlink_app/` excluding `__pycache__`: `114` (`42` top-level, `72` inside package/test directories)
- Top-level Python modules directly in `graphlink_app/` (not in any subdirectory): `42`
- Real package directories with `__init__.py`: `6`
  - `graphlink_canvas/` (`8` Python files)
  - `graphlink_nodes/` (`11` Python files)
  - `graphlink_plugins/` (`13` Python files, including the `common/` and `gitlink/` sub-packages)
  - `graphlink_session/` (`9` Python files)
  - `graphlink_ui_dialogs/` (`4` Python files)
  - `graphlink_widgets/` (`10` Python files)
- `tests/` (not a package - no `__init__.py`): `17` Python files
- Runtime modes exposed in the shell:
  - `Ollama (Local)`
  - `Llama.cpp (Local)`
  - `API Endpoint`
- Runtime persistence outside the repo:
  - chats database: `~/.graphlink/chats.db`
  - settings/session state: `~/.graphlink/session.dat`
- Hardcoded repo-local asset paths still exist in UI code:
  - `C:\Users\Admin\source\repos\graphlink_app\assets\graphlink.ico`
  - `C:\Users\Admin\source\repos\graphlink_app\assets\check.png`
  - `C:\Users\Admin\source\repos\graphlink_app\assets\down_arrow.png`

## Read This First

If you need to rebuild the mental model quickly, open files in this order:

1. `graphlink_app/graphlink_app.py`
2. `graphlink_app/graphlink_window.py`
3. `graphlink_app/graphlink_window_actions.py`
4. `graphlink_app/api_provider.py`
5. `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`
6. `graphlink_app/graphlink_scene.py`
7. `graphlink_app/graphlink_session/manager.py`
8. `graphlink_app/graphlink_session/serializers.py`
9. `graphlink_app/graphlink_session/deserializers.py`
10. `graphlink_app/graphlink_session/scene_index.py`
11. `graphlink_app/graphlink_plugins/graphlink_plugin_portal.py`
12. `graphlink_app/graphlink_memory.py`
13. `graphlink_app/graphlink_lod.py`

That path shows boot, shell ownership, provider/mode initialization, live settings UI, scene authority, persistence, schema indexing, plugin registration, branch-memory rules, and the shared zoom-based render fallback system.

## Architecture Truths That Matter

### 1. This is still a flat-import app with package islands

- Root modules still import each other by top-level names such as `from graphlink_window import ChatWindow`.
- The split packages are real, but the running app is not yet a clean package-first namespaced design.
- Compatibility wrappers still matter because much of the repo enters package code through those top-level modules.

### 2. The repo is still mid-migration toward split packages

- Concrete implementations increasingly live in `graphlink_nodes/`, `graphlink_canvas/`, `graphlink_plugins/`, `graphlink_session/`, `graphlink_ui_dialogs/`, and `graphlink_widgets/`.
- Top-level wrappers still preserve import stability.
- When both a wrapper and a concrete package module exist, edit the concrete package module unless you are intentionally changing the import surface.

### 3. Runtime mode handling is now a first-class architecture seam

- `graphlink_config.py` defines task keys and user-facing mode labels.
- `graphlink_licensing.py` persists per-mode settings, scan caches, update-check state, and current mode.
- `graphlink_ui_dialogs/graphlink_settings_dialogs.py` is the live configuration surface for Ollama, Llama.cpp, API providers, integrations, and update controls.
- `graphlink_window.py` owns startup mode initialization and toolbar mode switching.
- `api_provider.py` is the real execution authority for:
  - Ollama local runtime
  - direct `llama-cpp-python` GGUF runtime
  - OpenAI-compatible endpoints
  - Anthropic Claude endpoints
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

- `graphlink_window.py` stages image, document, and audio attachments.
- `graphlink_window_actions.py` turns:
  - image attachments into `ImageNode`
  - document attachments into `DocumentNode`
  - audio attachments into `DocumentNode` with `attachment_kind='audio'`
- `graphlink_audio.py` validates audio files, MIME types, duration limits, and preview labels.
- `graphlink_nodes/graphlink_node_document.py` is now the live UI for both document and audio attachment nodes.
- Graphlink-level limitation:
  - `Llama.cpp` local mode is text-only inside the app right now
  - Ollama and Gemini support both audio and image attachments
  - Anthropic Claude supports image attachments but explicitly rejects audio attachments (`_anthropic_content_block_from_part` raises, telling the user to switch to Gemini or Ollama)

### 6. `ChatScene`, `ChatSessionManager`, and `graphlink_session/scene_index.py` now form the schema triangle

- `graphlink_scene.py` still owns the live runtime lists and creation/deletion behavior.
- `graphlink_session/serializers.py` and `graphlink_session/deserializers.py` still decide save/load compatibility.
- `graphlink_session/scene_index.py` now centralizes:
  - node list names
  - save-guard node list names
  - child-link-capable node types
  - serializer/deserializer item indexing helpers
- If you add a new persisted node family, update all three places:
  - `graphlink_scene.py`
  - `graphlink_session/scene_index.py`
  - session serializer/deserializer code

### 7. A shared LoD/proxy render layer now matters across many node families

- `graphlink_lod.py` owns zoom thresholds, summary/glyph fallback rendering, preview text helpers, and proxy visibility rules.
- Many node UIs now rely on it for readable zoomed-out behavior instead of each node hand-rolling its own fallback.
- If a node looks wrong when zoomed out, `graphlink_lod.py` is usually as important as the node class itself.

### 8. Shared visuals are a little more centralized than before

- `graphlink_widgets/loading_visuals.py` now owns the shared orbital spinner painting used by both the splash and loading overlays.
- `graphlink_update.py` plus `graphlink_version.py` own the update-check signal and local version metadata.

### 9. There are still easy-to-misread legacy seams

- `graphlink_dialogs.py` duplicates canvas dialog classes but does not appear to be the live authority.
- `graphlink_widgets/pins.py` defines overlay-side `NavigationPin`; `graphlink_canvas/graphlink_canvas_navigation_pin.py` defines the persisted scene item with the same name.
- `graphlink_widgets/*.py` still use UTF-8 BOM in places; direct parsing tools should be BOM-aware.

## Runtime Ownership Map

### Boot and application shell

- `graphlink_app/graphlink_app.py`
  - `main()`
  - Creates `QApplication`, loads persisted settings, applies theme/model, and creates `ChatWindow` and `SplashScreen`.
- `graphlink_app/graphlink_window.py`
  - `ChatWindow`
  - Main shell, toolbar, document viewer panel, pin overlay, mode switching, update checks, plugin picker, attachment staging, shortcuts, and session lifecycle.
- `graphlink_app/graphlink_window_actions.py`
  - `WindowActionsMixin`
  - Core prompt send flow, attachment packing, response parsing, regeneration, charts, images, and all plugin execution entry points.
- `graphlink_app/graphlink_window_navigation.py`
  - `WindowNavigationMixin`
  - Command registration, collapse/expand/delete/focus commands, note creation, directional navigation, command palette.
- `graphlink_app/graphlink_command_palette.py`
  - `CommandManager`, `CommandPaletteDialog`
  - Searchable command palette.
- `graphlink_app/graphlink_update.py`
  - `UpdateCheckWorker`, version comparison helpers, update-signal fetch.
- `graphlink_app/graphlink_version.py`
  - `APP_VERSION`

### Canvas, graph surface, and layout

- `graphlink_app/graphlink_view.py`
  - `ChatView`
  - `QGraphicsView` wrapper, panning/zooming, drag-and-drop attachments, overlay widgets, minimap mounting, background grid, keyboard pan.
- `graphlink_app/graphlink_scene.py`
  - `ChatScene`
  - Node registries, connection registries, node creation helpers, search, frame/container/note/chart creation, delete logic, branch visibility, font propagation.
- `graphlink_app/graphlink_connections.py`
  - Core connection families and shared pin/path behavior.
- `graphlink_app/graphlink_minimap.py`
  - `MinimapWidget`
  - Graph overview and jump navigation.
- `graphlink_app/graphlink_lod.py`
  - Shared level-of-detail thresholds, preview text helpers, zoom-aware proxy visibility, and fallback card painting.

### Persistence, context, and attachments

- `graphlink_app/graphlink_session/`
  - Concrete persistence package.
  - Key files: `content_codec.py`, `database.py`, `deserializers.py`, `manager.py`, `scene_index.py`, `serializers.py`, `title_generator.py`, `workers.py`
- `graphlink_app/graphlink_core.py`
  - Compatibility facade for session persistence.
- `graphlink_app/graphlink_memory.py`
  - Branch-memory utilities; do not hand-roll history mutation when these helpers already exist.
- `graphlink_app/graphlink_file_handler.py`
  - Attachment readability checks and text extraction for plain text, code, PDF, and DOCX.
- `graphlink_app/graphlink_audio.py`
  - Audio validation, duration probing, MIME inference, and duration formatting.
- `graphlink_app/graphlink_exporter.py`
  - Export helpers used by node context menus.

### Providers, prompts, settings, updates, and themes

- `graphlink_app/api_provider.py`
  - Provider/runtime abstraction for Ollama, direct Llama.cpp, OpenAI-compatible APIs, Anthropic Claude, and Gemini.
  - Also owns local model scanning:
    - Ollama manifest scanning
    - GGUF scanning for `Llama.cpp`
  - Also owns modality handling rules and local runtime initialization.
- `graphlink_app/graphlink_prompts.py`
  - Global prompt text and token-safe JSON encoding helpers.
- `graphlink_app/graphlink_config.py`
  - Task keys, mode labels, local provider constants, theme palette getters, semantic colors, current model assignment.
- `graphlink_app/graphlink_licensing.py`
  - `SettingsManager`
  - Persisted user settings:
    - theme
    - token counter
    - model settings
    - system prompt toggle
    - current runtime mode
    - Ollama model settings and scan cache
    - Llama.cpp GGUF settings and scan cache
    - API endpoint/provider settings
    - GitHub token
    - update-check state
- `graphlink_app/graphlink_styles.py`
  - QSS themes and shared palette definitions.

### Shared UI and dialogs

- `graphlink_app/graphlink_ui_components.py`
  - `NotificationBanner`, `DocumentViewerPanel`
- `graphlink_app/graphlink_welcome_screen.py` was removed.
  - Startup now opens `ChatWindow` directly after `SplashScreen`; starter templates and recent chat launch paths are no longer part of startup.
- `graphlink_app/graphlink_ui_dialogs/graphlink_library_dialog.py`
  - `ChatLibraryDialog`
- `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`
  - Real settings surface.
  - Key sections:
    - `AppearanceSettingsWidget`
    - `OllamaSettingsWidget`
    - `LlamaCppSettingsWidget`
    - `ApiSettingsWidget`
    - `IntegrationsSettingsWidget`
    - `SettingsDialog`
- `graphlink_app/graphlink_ui_dialogs/graphlink_system_dialogs.py`
  - `HelpDialog`, `AboutDialog`
- `graphlink_app/graphlink_widgets/loading_visuals.py`
  - Shared spinner painter for splash and overlay loading states.

### Agents and background workers

- `graphlink_app/graphlink_agents.py`
  - Broad facade used by shell and settings code.
- `graphlink_app/graphlink_agents_core.py`
  - Standard chat, explainer, takeaway, and group-summary agents plus worker threads.
- `graphlink_app/graphlink_agents_tools.py`
  - Chart data extraction/repair, image generation, model pull workers.
- `graphlink_app/graphlink_agents_pycoder.py`
  - Python REPL, execution/repair/analysis agents, Py-Coder workers.
- `graphlink_app/graphlink_agents_code_sandbox.py`
  - Virtualenv sandbox, generation/repair agents, isolated execution worker.
- `graphlink_app/graphlink_agents_web.py`
  - Search/fetch/validate/summarize worker for the web node.

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
- `html`
- `artifact`
- `gitlink`

`reasoning`, `workflow`, `graph_diff`, `quality_gate`, and `code_review` node types no
longer exist - their plugins were removed. The deserializer still initializes `node =
None` and only matches known types, so an old saved session containing one of these is
skipped gracefully rather than crashing.

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
- `group_summary_connections`
- `html_connections`
- `artifact_connections`
- `gitlink_connections`

## Core Runtime Flows

### 1. Application boot

1. `graphlink_app/graphlink_app.py:main()`
2. `graphlink_licensing.SettingsManager()`
3. `graphlink_config.apply_theme()`
4. `graphlink_config.set_current_model()`
5. `graphlink_window.ChatWindow`
6. `ChatWindow._initialize_saved_mode_on_startup()`
7. Mode-specific initialization goes through `api_provider.initialize_local_provider()` or `api_provider.initialize_api()`
8. `graphlink_widgets.SplashScreen`

### 2. Runtime mode initialization

1. `graphlink_window.ChatWindow._initialize_mode()`
2. If `Ollama (Local)`:
   - `api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA)`
3. If `Llama.cpp (Local)`:
   - settings come from `SettingsManager.get_llama_cpp_settings()`
   - `api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, ..., preload_model=False)`
4. If `API Endpoint`:
   - provider/model settings come from `SettingsManager`
   - `api_provider.initialize_api(...)`
5. The settings flyout mirrors these same seams in `graphlink_ui_dialogs/graphlink_settings_dialogs.py`

### 3. Normal prompt send / attachment flow

1. `WindowActionsMixin.send_message()`
2. `graphlink_memory.resolve_branch_parent()` and `get_node_history()`
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
4. `graphlink_session.scene_index` supplies node list and indexing helpers
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

1. `graphlink_window.ChatWindow.check_for_updates()`
2. `graphlink_update.UpdateCheckWorker`
3. Remote version signal fetched from GitHub
4. `graphlink_licensing.SettingsManager.record_update_check_result()`
5. `AppearanceSettingsWidget` surfaces the saved status and manual re-check action

## Plugin Catalog As Registered Today

This is the live registration order in `graphlink_plugins/graphlink_plugin_portal.py`.

### Branch Foundations

- `System Prompt`
- `Conversation Node`

### Reasoning & Research

- `Graphlink-Web`

### Build & Execution

- `Gitlink`
- `Py-Coder`
- `Execution Sandbox`
- `HTML Renderer`

### Workflow & Drafting

- `Artifact / Drafter`

Reasoning, Workflow Architect, Quality Gate, Code Review Agent, and Branch Lens
(GraphDiff) were removed - see the "Remove the 5 advisor plugins" commit. The
"Validation & Delivery" category had no members left after that removal and is gone
from `PLUGIN_CATEGORY_META` entirely.

## Compatibility Wrapper Map

These top-level files are import-stability shims, not the main implementation.

### Node wrapper

- `graphlink_app/graphlink_node.py` -> `graphlink_app/graphlink_nodes/*`

### Canvas wrappers

- `graphlink_app/graphlink_canvas_items.py` -> `graphlink_app/graphlink_canvas/__init__.py`
- `graphlink_app/graphlink_canvas_groups.py` -> `graphlink_app/graphlink_canvas/__init__.py`
- `graphlink_app/graphlink_canvas_note_items.py` -> `graphlink_app/graphlink_canvas/__init__.py`
- `graphlink_app/graphlink_canvas_dialogs.py` -> `graphlink_app/graphlink_canvas/graphlink_canvas_dialogs.py`

### Dialog wrappers

- `graphlink_app/graphlink_library_dialog.py` -> `graphlink_app/graphlink_ui_dialogs/graphlink_library_dialog.py`
- `graphlink_app/graphlink_settings_dialogs.py` -> `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`
- `graphlink_app/graphlink_system_dialogs.py` -> `graphlink_app/graphlink_ui_dialogs/graphlink_system_dialogs.py`

### Agent facade

- `graphlink_app/graphlink_agents.py` re-exports the split `graphlink_agents_*` modules

## Concrete File Index

This is the practical lookup map for where code actually lives today.

### Top-level concrete modules that changed or matter most

- `api_provider.py`
  - Provider abstraction for Ollama, direct `Llama.cpp`, OpenAI-compatible chat/image APIs, Anthropic Claude, and Gemini.
  - Key responsibilities:
    - local/runtime initialization
    - GGUF scanning
    - Ollama model scanning
    - modality preparation
    - `chat()`
    - `generate_image()`
- `graphlink_audio.py`
  - Audio attachment validation and duration probing.
  - Key functions/classes: `AudioValidationError`, `is_supported_audio_file()`, `guess_audio_mime_type()`, `inspect_audio_file()`, `format_duration()`
- `graphlink_lod.py`
  - Shared zoom-dependent render helpers.
  - Key helpers: `lod_mode_for_item()`, `preview_text()`, `sync_proxy_render_state()`, `draw_lod_card()`
- `graphlink_update.py`
  - Update signal fetch and comparison logic.
  - Key symbols: `UPDATE_SIGNAL_URL`, `UPDATE_REPOSITORY_URL`, `UpdateCheckWorker`, `build_update_result()`
- `graphlink_version.py`
  - Current local app version constant.
- `graphlink_window.py`
  - Main shell, mode switching, toolbar, update checks, settings flyout, attachment staging.
- `graphlink_window_actions.py`
  - Prompt dispatch, attachment packaging, response parsing, plugin execution.
- `graphlink_scene.py`
  - Scene/controller authority.
- `graphlink_memory.py`
  - Branch/history helpers.

### `graphlink_session/`

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

### `graphlink_ui_dialogs/`

- `graphlink_library_dialog.py`
  - Recent chat browser.
- `graphlink_settings_dialogs.py`
  - Live settings flyout.
  - This is the real place to edit:
    - Ollama scans
    - GGUF scans
    - API provider/model settings
    - GitHub token settings
    - update-check controls
- `graphlink_system_dialogs.py`
  - About/help UI.

### `graphlink_widgets/`

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
- `controls.py`
  - `FontControl`, `GridControl`
- `scrolling.py`
  - `CustomScrollBar`, `ScrollHandle`, `ScrollBar`

## Where To Edit When...

### You want to change startup, current mode handling, or mode switching

- `graphlink_app/graphlink_window.py`
- `graphlink_app/graphlink_config.py`
- `graphlink_app/graphlink_licensing.py`
- `graphlink_app/api_provider.py`
- `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`

### You want to change Ollama scanning or default local models

- `graphlink_app/api_provider.py`
- `graphlink_app/graphlink_config.py`
- `graphlink_app/graphlink_licensing.py`
- `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`

### You want to change direct `Llama.cpp` / GGUF behavior

- `graphlink_app/api_provider.py`
- `graphlink_app/graphlink_licensing.py`
- `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`
- `graphlink_app/graphlink_window.py`
- `graphlink_app/graphlink_session/title_generator.py`

### You want to change prompt send, response parsing, attachment handling, or execution dispatch

- `graphlink_app/graphlink_window_actions.py`
- `graphlink_app/graphlink_memory.py`
- `graphlink_app/graphlink_file_handler.py`
- `graphlink_app/graphlink_audio.py`
- `graphlink_app/api_provider.py`

### You want to change attachment staging or supported attachment kinds

- `graphlink_app/graphlink_window.py`
- `graphlink_app/graphlink_window_actions.py`
- `graphlink_app/graphlink_audio.py`
- `graphlink_app/graphlink_nodes/graphlink_node_document.py`
- `graphlink_app/graphlink_session/serializers.py`
- `graphlink_app/graphlink_session/deserializers.py`

### You want to change canvas behavior, graph layout, or zoomed-out rendering

- `graphlink_app/graphlink_view.py`
- `graphlink_app/graphlink_scene.py`
- `graphlink_app/graphlink_connections.py`
- `graphlink_app/graphlink_minimap.py`
- `graphlink_app/graphlink_lod.py`
- `graphlink_app/graphlink_canvas/*`

### You want to add or modify a node family

- Core chat/code/doc/image/thinking nodes:
  - `graphlink_app/graphlink_nodes/*`
- Specialized nodes:
  - `graphlink_app/graphlink_pycoder.py`
  - `graphlink_app/graphlink_web.py`
  - `graphlink_app/graphlink_conversation_node.py`
  - `graphlink_app/graphlink_html_view.py`
- Then update:
  - `graphlink_app/graphlink_scene.py`
  - `graphlink_app/graphlink_session/scene_index.py`
  - `graphlink_app/graphlink_session/serializers.py`
  - `graphlink_app/graphlink_session/deserializers.py`
  - `graphlink_app/graphlink_window.py`
  - `graphlink_app/graphlink_window_actions.py`

### You want to add or modify a plugin

- Registration and category metadata:
  - `graphlink_app/graphlink_plugins/graphlink_plugin_portal.py`
- Picker UI:
  - `graphlink_app/graphlink_plugins/graphlink_plugin_picker.py`
- Shared plugin context menu:
  - `graphlink_app/graphlink_plugins/graphlink_plugin_context_menu.py`
- Concrete plugin logic:
  - `graphlink_app/graphlink_plugins/graphlink_plugin_*.py`
- Then verify:
  - `graphlink_app/graphlink_scene.py`
  - `graphlink_app/graphlink_session/scene_index.py`
  - `graphlink_app/graphlink_session/serializers.py`
  - `graphlink_app/graphlink_session/deserializers.py`
  - `graphlink_app/graphlink_window_actions.py`

### You want to change save/load compatibility

- `graphlink_app/graphlink_session/scene_index.py`
- `graphlink_app/graphlink_session/serializers.py`
- `graphlink_app/graphlink_session/deserializers.py`
- `graphlink_app/graphlink_session/manager.py`
- `graphlink_app/graphlink_core.py`
- `graphlink_app/graphlink_scene.py`

### You want to change update checks or version reporting

- `graphlink_app/graphlink_update.py`
- `graphlink_app/graphlink_version.py`
- `graphlink_app/graphlink_licensing.py`
- `graphlink_app/graphlink_window.py`
- `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`

### You want to change reusable widgets or loading visuals

- `graphlink_app/graphlink_widgets/*`
- `graphlink_app/graphlink_ui_components.py`

## Short Working Rules For Future Sessions

- Open the concrete package file before touching a compatibility wrapper.
- Treat `api_provider.py` as the runtime execution authority for every model mode.
- Treat `ChatScene` plus session serializer/deserializer code as the graph schema.
- Treat `graphlink_session/scene_index.py` as the central list/index helper whenever you add a new persisted node family.
- Treat `WindowActionsMixin` as the execution dispatcher.
- Treat `PluginPortal` as the plugin catalog authority.
- Treat `graphlink_memory.py` as the only safe place to define branch-history semantics.
- Treat `graphlink_lod.py` as shared render infrastructure, not optional polish.
- Remember that `Llama.cpp` mode expects direct `.gguf` files, not Ollama blobs/manifests.
- Remember that audio attachments persist through `DocumentNode`, not a separate audio node type.
- Be careful with duplicate names:
  - scene `NavigationPin` lives in `graphlink_canvas`
  - overlay `NavigationPin` lives in `graphlink_widgets`
- Be careful with legacy files:
  - `graphlink_dialogs.py` is not the live canvas dialog authority
- Be careful with machine-specific paths:
  - several asset paths are still hardcoded to one local repo location
