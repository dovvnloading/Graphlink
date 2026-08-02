# Graphlink Repo Navigation

Living navigation document for the Graphlink codebase.

Primary goal: give future work a reliable, current map of where behavior actually lives so we do not need to re-discover the repo from scratch every session.

Last refreshed: 2026-07-27 (post R7.6b Qt-removal cutover)

## Repo Snapshot

- Product name in the UI: `Graphlink`
- Repo / module naming in code: `Graphlink`
- **The Qt/PySide6 desktop app (`graphlink_app/`) is gone.** It was deleted in full at the R7.6b cutover, along with 3 Qt-coupled plugin files (`graphlink_plugin_context_menu.py`, `graphlink_plugin_portal.py`, `web_research/worker.py`). Zero files in the repo import PySide6/PyQt today - `qt_burndown.json` is pinned at `0/0/0`, enforced permanently by `tests/test_no_qt_anywhere.py`. (Stray `graphlink_app/__pycache__`/`.pytest_cache`/`.egg-info` directories may still exist on a given disk as untracked build litter - `git ls-files graphlink_app/` returns nothing; there is no real source there anymore.)
- Real top-level layout:
  - `backend/` - real Python package (`__init__.py` present). ALL application/domain logic: the FastAPI app, the WebSocket event bus, and every node/canvas/agent/settings/persistence model. `backend/tests/` holds its pytest suite (25 files).
  - `web_ui/` - a Vite + React + TypeScript SPA. The real app lives at `web_ui/src/app/`; shared infra lives at `web_ui/src/lib/`.
  - `contracts/` - repo-root, NEW at R7.6b, build-time-only codegen package (not a runtime dependency of the app). Generates the TS types + JSON Schemas the SPA imports from the Python payload dataclasses. `contracts/tests/` holds its own pytest suite.
  - `graphlink_plugins/` - real Python package, unchanged location from before the cutover. Qt-free plugin domain logic only.
  - 18 loose top-level `.py` modules at the repo root (see list below) - unchanged content, just living at the repo root now instead of inside `graphlink_app/`.
  - `tests/` - not a package (no `__init__.py`). Currently one file, `test_no_qt_anywhere.py`, the permanent Qt-removal gate.
  - `doc/` - **gitignored** (`.gitignore` has `/doc/`), local-only planning scratch. It is never pushed to the remote and is not part of what a clone or contributor sees. Treat it as a historical record of past planning, not shipped documentation - do not "fix" its content as if it were user-facing.
- 18 loose top-level `.py` modules (unchanged content, relocated over R7.2 and earlier increments): `api_provider.py`, `graphlink_artifact_agent.py`, `graphlink_audio.py`, `graphlink_chart_agent.py`, `graphlink_chart_data.py`, `graphlink_chart_rendering.py`, `graphlink_chat_agent.py`, `graphlink_desktop.py`, `graphlink_grid_view_settings.py`, `graphlink_memory.py`, `graphlink_model_catalog.py`, `graphlink_navigation_pins.py`, `graphlink_prompts.py`, `graphlink_secrets.py`, `graphlink_settings_store.py`, `graphlink_task_config.py`, `graphlink_token_estimator.py`, `graphlink_version.py`.
- Real entry point: `graphlink_desktop.py` (repo root). `pyproject.toml`'s `[project.gui-scripts]` reads `graphlink = "graphlink_desktop:main"`.
- Runtime modes exposed in Settings: `Ollama (Local)`, `Llama.cpp (Local)`, `API Endpoint` (OpenAI-Compatible / Anthropic Claude / Google Gemini). The AppBar's own provider-mode `<select>` is still hardcoded-disabled to one option (`Ollama (Local)`) with `title="Switching provider modes isn't available yet"` - see the Architecture Truths section.
- Runtime persistence outside the repo:
  - chats database: `~/.graphlink/chats.db` (same file format the deleted Qt app wrote - `backend/chat_library.py` reads/writes it for compatibility)
  - settings/session state: `~/.graphlink/session.dat` (`backend/settings.py` owns one shared instance)
  - crash sentinel: `~/.graphlink/running.lock` (JSON, `backend/crash_recovery.py`)
  - rotating log: `~/.graphlink/graphlink.log` (2MB cap, `backend/crash_recovery.py`)
- CI (`.github/workflows/ci.yml`): two jobs, both `windows-latest`. "Python tests (offscreen)" runs `pip install -r requirements.txt`, `python -m compileall -q .`, then `python -m pytest -q` from the repo root (961 tests, confirmed by direct collection: `backend/tests/`, `contracts/tests/`, `tests/`). "Frontend checks" runs `npm ci` then `npm run check` inside `web_ui/` (confirmed by direct run: 779 vitest tests across 41 files, all passing). `windows-latest` is now pinned **solely** for real Windows DPAPI tests (`backend/tests/test_backend_secrets_at_rest.py`) - there is no remaining Qt/offscreen-mode reason.

## Read This First

If you need to rebuild the mental model quickly, open files in this order:

1. `graphlink_desktop.py` - the whole launch story
2. `backend/app.py` - FastAPI app factory, WS event bus wiring, WS origin validation
3. `backend/domain/graph.py` - the node/graph/connection domain model (`SceneDocument`, composed from `backend/domain/branches.py` + `backend/domain/groups.py` mixins; data model in `backend/domain/model.py`) - split out of `backend/canvas.py` at ADR-002 stage 2.2, purity enforced by `tests/test_domain_purity.py`
4. `backend/agents.py` - LLM dispatch (`AgentDispatcher`)
5. `backend/composer.py` - composer draft/reasoning state
6. `backend/settings.py` - provider/model settings, DPAPI-backed secrets
7. `backend/session_load.py` / `backend/session_save.py` - `~/.graphlink/chats.db` compatibility
8. `backend/autosave.py` / `backend/crash_recovery.py` - background save + crash sentinel
9. `web_ui/src/app/App.tsx` - the SPA shell: WS transport, topic subscriptions, overlay/shortcut wiring
10. `web_ui/src/app/canvas/SceneCanvas.tsx` + `sceneStore.ts` - the React Flow graph surface
11. `web_ui/src/app/chrome/Composer.tsx` - the composer UI
12. `contracts/codegen.py` - the payload-to-TS/JSON-Schema codegen (`GENERATED_ARTIFACTS`, 11 entries)

That path shows boot, backend domain ownership, agent dispatch, persistence, and the SPA's own transport/canvas/chrome wiring, plus the contract layer connecting the two sides.

## Architecture Truths That Matter

### 1. The migration is DONE - this is no longer a "mid-migration" codebase

Every prior revision of this document described a flat-import Qt app with package islands, "still mid-migration toward split packages." That framing is now false. `graphlink_app/` and its 6 sub-packages (`graphlink_canvas/`, `graphlink_nodes/`, `graphlink_plugins/` (the old in-app one), `graphlink_session/`, `graphlink_ui_dialogs/`, `graphlink_widgets/`) are gone. There are no compatibility wrapper modules anywhere in the repo - the R7.6b cutover deleted the last of them along with `graphlink_app/` itself. The real architecture is now a clean two-process-in-one-process split: a Python FastAPI backend (`backend/` + the loose root modules + `graphlink_plugins/`) and a React SPA (`web_ui/src/app/`), talking over one WebSocket.

### 2. One process, one window, one origin

`graphlink_desktop.py` starts the FastAPI backend (via `uvicorn.Server(...).run()`) on a free localhost port inside a background daemon thread of the SAME process, waits for `GET /api/health` to return 200, then opens exactly one native OS webview window via `pywebview` (WebView2 on Windows) pointed at that backend's own URL. This is not a browser tab and not Qt - there is no separate frontend process, no separate dev server, and no separate window-manager layer. The backend serves the built SPA's static files (`web_ui/dist/app/`), the REST API, and the WebSocket, all from that single origin. If `web_ui/dist/app/index.html` doesn't exist, `graphlink_desktop.py` logs an error telling you to run `cd web_ui && npm run build` and exits with code 1 - it does NOT auto-build.

### 3. The event bus is the entire client/server contract

`backend/app.py`'s `create_app()` wires one `/ws?session=<id>` WebSocket endpoint carrying two message shapes: `{"kind": "subscribe", "topics": [...]}` (server replies with full-state `"state"` snapshots per topic) and `{"kind": "intent", "topic": ..., "intent": ..., "args": [...]}` (server replies with `"result"` or `"error"`). Real topics registered today (see `backend/app.py::_configure_session`, in registration order): `system`, `notification`, `token-counter`, `app-composer`, `scene`, `grid-control`, `drag-speed`, `font-control`, `app-about`, `app-plugins`, `app-settings`, `app-chat-library`. The SPA's `web_ui/src/lib/ws/transport.ts` (`WsTransport`) is the one client-side implementation of this protocol; `web_ui/src/app/App.tsx` subscribes to `system`/`app-settings` directly and hands the transport to per-domain stores (`SceneStore`, `ComposerStore`) that subscribe to their own topics and dispatch intents back. `web_ui/src/lib/bridge-core/generated/*` holds the codegen'd TS types + JSON Schemas used to validate every incoming snapshot before it's trusted (`TOPIC_VALIDATORS` in `web_ui/src/lib/api-contract/topics.ts`).

### 4. The WS origin check is a real, load-bearing security control, not boilerplate

`backend/app.py::_is_allowed_ws_origin` defends against cross-site WebSocket hijacking (a malicious page in the user's regular browser opening a raw socket to `ws://127.0.0.1:<port>/ws` - browsers don't apply same-origin policy to WebSocket connects). It requires exact-string match against `http://127.0.0.1:<port>` (never substring/startswith), with one opt-in escape hatch: the `GRAPHLINK_DEV_WS_ORIGIN` env var, which lets a manually-run backend accept connections from a separately-run `npm run dev` Vite server. `graphlink_desktop.py` never sets this var, so the escape hatch is dead in the shipped app by construction.

### 5. Runtime mode handling is real for configuration, deferred for in-toolbar switching

`web_ui/src/app/chrome/AppBar.tsx` has a `<select disabled title="Switching provider modes isn't available yet">` with exactly one hardcoded option (`Ollama (Local)`) and a no-op `onChange`. This is confirmed current, not stale documentation. However, configuring each provider's credentials/models IS fully real and reachable: `web_ui/src/app/chrome/SettingsDialog.tsx` has genuine tabs `General`, `Ollama (Local)`, `Llama.cpp (Local)`, `API Endpoint`, `Integrations` (one file, no per-provider split), each wired to real `backend/settings.py` intents (e.g. `setLlamaCppChatModelPath`, `scanLlamaCppSystem`, `pickOllamaScanFolder`, `saveApiConfiguration`). So: you can fully configure Ollama, Llama.cpp, and API-Endpoint (OpenAI-Compatible / Anthropic Claude / Google Gemini) providers today, but switching which one is *active* from the toolbar is not wired yet.

### 6. Connections are one unified edge model now, not 13 parallel lists

The old Qt app kept a separate `ConnectionItem`-family list per relationship kind (`content_connections`, `document_connections`, `pycoder_connections`, `gitlink_connections`, ... 13 in total) plus a distinct `children` object-tree relationship. `backend/domain/graph.py`'s `SceneDocument` (split out of `backend/canvas.py` at ADR-002 stage 2.2) collapses all of that into one `nodes: dict[str, SceneNode]` + `edges: dict[str, SceneEdge]` model - an edge is just an edge, created via `SceneDocument.connect(source, target)`. The old distinctions (structural parent vs. child-list vs. connection-line) only resurface at the save/load boundary: `backend/session_save.py`'s `_classify_edges` reconstructs the legacy four-bucket split purely to write a byte-compatible `chats.db` row: it is a save-time projection, not a live in-memory structure.

### 7. Every plugin picker entry is now a real, working node-creation path

As of R7.5a, all 8 items in the plugin picker create real nodes - there is no more "still deferred" plugin. `System Prompt` creates a `note` node with `is_system_prompt=True` attached above the selected node's branch root (not a distinct node kind); the other 7 each create their own real node kind as a branch-point child of the selected node. See `backend/plugins.py::execute_plugin` for the exact per-plugin logic and required-parent validation.

### 8. "Reimplement, don't import" is the standing precedent for anything touching legacy shapes

Several `backend/` modules (`composer.py`, `chat_library.py`, `plugins.py`, `session_load.py`, `session_save.py`, `crash_recovery.py`) explicitly reimplement an algorithm that used to live in the deleted `graphlink_app/` tree, rather than importing anything from it (there is nothing left to import from - it's gone). Their module docstrings document exactly which legacy file/algorithm they ported and why a straight import was never viable (Qt-coupling in the source, or the source no longer existing). This precedent still matters if you ever need to cross-check a persistence field name or algorithm detail: the ground truth is the CURRENT backend module's own docstring and the git history of the deleted file, not assumption.

### 9. Windows is still a real, load-bearing target - not legacy inertia

`graphlink_secrets.py` (repo root, unchanged location) wraps Windows DPAPI (`CryptProtectData`/`CryptUnprotectData` via ctypes) to encrypt secrets at rest in `~/.graphlink/session.dat`, storing them as `"dpapi:" + base64(blob)`. It falls back to plaintext on non-Windows platforms or if DPAPI errors, and legacy unprefixed plaintext values remain readable regardless of platform. This is the sole reason CI still pins `windows-latest` for both jobs.

### 10. Attachments/ingest exist in the data model but are not reachable from the UI today

`backend/domain/` has a full document-node model (pdf/docx/audio metadata, `backend/domain/model.py` + `graph.py`) and a wired `addDocumentNode` WS intent, but `web_ui/src/app/canvas/sceneStore.ts`'s `addDocumentNode()` is only ever called from its own test file - no real UI component invokes it. `backend/composer.py`'s payload also hardcodes `"attachments": False` in its capabilities. `pypdf`, `python-docx`, and `reportlab` are present in `requirements.in`/`pyproject.toml` but are not imported anywhere in the current codebase - they read as unused/legacy-holdover dependencies right now, not evidence of a working ingest path.

## Runtime Ownership Map

### Boot and application shell

- `graphlink_desktop.py`
  - `main()`, `_start_backend()`, `_free_port()`, `_wait_for_health()`
  - Starts the FastAPI backend in a daemon thread, waits for `/api/health`, opens the one `pywebview` window. Owns the crash-sentinel calls (`mark_running`/`mark_clean_exit`/`previous_run_crashed`) and logging setup (`configure_logging`/`install_exception_handlers`), all from `backend/crash_recovery.py`.
  - Real env vars (confirmed by grep, only these 3 exist): `GRAPHLINK_BACKEND_PORT` (pin the port), `GRAPHLINK_DEBUG_WEBVIEW` (enable webview devtools), `GRAPHLINK_DEV_WS_ORIGIN` (opt-in trusted WS origin for a separately-run dev-server workflow).
- `backend/app.py`
  - `create_app()` - the FastAPI app factory: mounts `/api/health`, the `/ws` endpoint, `backend/assets.py`'s asset-serving route, and (when `web_ui/dist/app/` exists) the built SPA as static files with a client-side-routing fallback.
  - `_configure_session()` - registers every topic/intent onto a fresh per-connection `SessionBus`, in a deliberately load-bearing order (notifications before composer before agents before canvas before plugins/settings/chat-library - see the function's own comments for exactly why each ordering matters).
  - `_is_allowed_ws_origin()` - the WS handshake origin allowlist (see Architecture Truths #4).
- `backend/events.py`
  - `EventBus`, `SessionBus` - the pub/sub primitive: `register_topic`, `register_intent`, `publish`, `dispatch_intent`, per-session WebSocket attach/detach and disconnect-triggered cleanup.

### Canvas, graph surface, and layout

- `backend/domain/` + `backend/canvas.py` (split at ADR-002 stage 2.2; before it, canvas.py was one ~5,100-line file owning both halves)
  - `backend/domain/`: `SceneNode`/`SceneEdge` (`model.py`), `SceneDocument` (`graph.py`, composed as `SceneDocument(BranchOps, GroupOps)` from `branches.py`/`groups.py`) - every node kind's creation/deletion/reparenting, the unified edge model, frame/container/note/chart creation, branch-root resolution, collapse/expand, view-state. Purity gated by `tests/test_domain_purity.py`.
  - `backend/canvas.py`: `register_canvas()` - every scene/grid topic + intent wrapper, navigation-pin intents, and the `sendMessage`/`regenerateResponse`/`generateImage` entry points that hand off into `backend/agents.py`.
- `web_ui/src/app/canvas/SceneCanvas.tsx` + `sceneStore.ts`
  - The React Flow graph surface: node/edge rendering, drag/connect/select, view-state sync, LOD-ish rendering handled per-node-component rather than a shared proxy layer.
- `web_ui/src/app/canvas/smartGuides.ts`, `treeNavigation.ts`, `exportCanvasPng.ts`, `downloadTextFile.ts`
  - Pure-function geometry/navigation helpers plus client-side export (PNG whole-canvas export, per-node Chat `.md` / Code-with-guessed-extension export). There is no server-side export format.
- `web_ui/src/app/canvas/*NodeView.tsx` (one file per node kind, see the Taxonomy section below)

### Persistence, context, and attachments

- `backend/session_load.py` / `backend/session_save.py`
  - Load/save against the SAME `~/.graphlink/chats.db` the deleted Qt app wrote. `session_load.py` restores nodes in a single forward pass (parent must already be resolved or the node is skipped, matching the legacy algorithm exactly); `session_save.py`'s `_classify_edges` is the save-time mirror, reconstructing the legacy four-bucket relationship split from the unified `edges` dict.
- `backend/chat_library.py`
  - `register_chat_library()` - the `app-chat-library` topic and its list/rename/delete/load/new-chat intents, reading/writing the same `chats.db` rows `session_load.py`/`session_save.py` operate on.
- `backend/autosave.py`
  - A net-new (no legacy equivalent existed) background asyncio task per session, ticking every 30s, change-guarded by a content+chat-id hash so an idle session doesn't rewrite its own unchanged row forever. Silent on success, notifies on failure.
- `backend/crash_recovery.py`
  - `~/.graphlink/running.lock` sentinel (write on launch, remove on clean exit), `~/.graphlink/graphlink.log` rotating file log (2MB x3 backups), `sys.excepthook`/`threading.excepthook`/`faulthandler` installation, and `maybe_show_crash_notice()` (an in-app notification shown once if the previous run's sentinel was still present at this launch).
- `graphlink_secrets.py` (repo root)
  - Windows DPAPI-backed secret encryption for `~/.graphlink/session.dat` (see Architecture Truths #9).

### Providers, prompts, settings, and agents

- `api_provider.py` (repo root)
  - Provider/runtime abstraction for Ollama, direct Llama.cpp (GGUF), OpenAI-compatible endpoints, Anthropic Claude, and Gemini. Local model scanning (Ollama manifests, GGUF files), modality handling, `chat()`, `generate_image()`.
- `backend/settings.py`
  - `SettingsManager`-backed `register_settings()`: the `app-settings` topic and every settings intent (General, Ollama, Llama.cpp, API Endpoint, Integrations, GitHub token). Reads defaults from `graphlink_task_config.py`'s task-keyed model dict.
- `backend/agents.py` (now the largest file in the repo, ~156KB)
  - `AgentDispatcher` (one instance per session, never a module-level singleton) - owns in-flight request tracking/cancellation across its 13 dispatch surfaces, `bootstrap_provider_state()` (process-global `api_provider` state, set up once per process from the shared `SettingsManager`), and `register_agents()`. As of ADR-002 stage 2.4 (complete), all 12 in-flight-request dicts are gone - every dispatch surface (chat/conversation, image, artifact, web research, gitlink run, gitlink apply, pycoder, code sandbox, chart, note, branch comparison, branch synthesis) claims into one shared `self._runs` (a `backend/run_lifecycle.py` `RunRegistry`), closing audit finding C3 structurally: `cancel_all()` now walks every in-flight run in the session regardless of kind. Chat/artifact/gitlink_run/pycoder/code_sandbox are `cancel_event`-bearing; web research uses `RunHandle.on_cancel` (a `CancellationToken`, not a `threading.Event`); gitlink run/apply/pycoder/code_sandbox's real busy guard is `node.pending_request_id` (a per-SceneNode field), not the registry's own `is_busy()` - the registry there is pure task/cancel_event/approval_future bookkeeping; pycoder/code_sandbox are the only two kinds carrying `RunHandle.approval_future` (Py-Coder/Execution Sandbox's human-approval-pause mechanism), mutated in place on every repair-loop iteration.
- `backend/run_lifecycle.py`
  - `RunHandle`/`RunRegistry` (claim/release/cancel/cancel_all/is_busy) and `run_single_shot()` - the ADR-002 stage 2.3 primitive `backend/agents.py`'s pilot surfaces claim into, see that module's own docstring. Stage 2.4b extended `RunHandle` with `on_cancel` (a generic cancellation hook for kinds whose cancellation primitive isn't a `threading.Event`, e.g. web research's `CancellationToken`) and `approval_future` (Py-Coder/Execution Sandbox's human-approval pause) plus `RunRegistry.cancel_all_pending_approvals()` and a `kind=` filter on `cancel()`, ahead of migrating the remaining 7 fire-and-forget dispatch surfaces.
- `backend/response_parsing.py`
  - `parse_response()` - splits a flat LLM reply into ordered thinking/text/code parts, shared by the ordinary send path and the regenerate path (both in `backend/canvas.py`). `ConversationNode` is the one confirmed exception - it never routes through this parser.
- `graphlink_task_config.py`, `graphlink_settings_store.py`, `graphlink_prompts.py`, `graphlink_model_catalog.py` (repo root)
  - Task keys/mode labels, persisted `SettingsManager` state, global prompt text, and the model-catalog helpers `api_provider.py`/`backend/settings.py` consume.

### Shared chrome, dialogs, and overlays

- `web_ui/src/app/overlays/overlays.tsx`
  - `OverlayProvider`/`useOverlays()` - the single-open, Escape-closes, outside-click-dismisses, focus-trapped overlay coordinator every dialog/popover in `chrome/` mounts through. This file is infrastructure only - it does not itself render any dialog.
- `web_ui/src/app/chrome/*.tsx`
  - `AppBar.tsx`, `Composer.tsx`, `ViewPopover.tsx`, `CommandPalette.tsx`, `SearchOverlay.tsx`, `PinOverlay.tsx`, `PluginPicker.tsx`, `SettingsDialog.tsx`, `ChatLibraryDialog.tsx`, `AboutDialog.tsx`, `HelpDialog.tsx`, `NotificationBanner.tsx`, `TokenCounter.tsx` - every piece of app chrome, including Settings, lives here (not under `overlays/`, which is only the coordinator).
- `backend/about.py`, `backend/plugins.py` (listing only), `backend/notifications.py`, `backend/token_counter.py`, `backend/composer.py`
  - Their respective topics' backend state and intents.

## Concrete Node and Connection Taxonomy

### Real node kinds today (verified directly against the `kind=` literals now in `backend/domain/graph.py`, 16 total)

| `kind` string | User-facing name (plugin picker, where applicable) | React component |
|---|---|---|
| `chat` | Chat | `ChatNodeView.tsx` |
| `code` | Code | `CodeNodeView.tsx` |
| `document` | Document/attachment | `DocumentNodeView.tsx` |
| `thinking` | Thinking | `ThinkingNodeView.tsx` |
| `html` | HTML Renderer | `HtmlNodeView.tsx` |
| `image` | Image | `ImageNodeView.tsx` |
| `conversation` | Conversation Node | `ConversationNodeView.tsx` |
| `web_research` | Web Research | `WebResearchNodeView.tsx` |
| `artifact` | Artifact / Drafter | `ArtifactNodeView.tsx` |
| `gitlink` | Gitlink | `GitlinkNodeView.tsx` |
| `pycoder` | Py-Coder | `PyCoderNodeView.tsx` |
| `code_sandbox` | Virtual Environment Runner | `CodeSandboxNodeView.tsx` |
| `note` | (System Prompt picker entry creates one) | `NoteNodeView.tsx` |
| `frame` | (Create Frame command) | `GroupNodeView.tsx` (shared with `container`, distinguished by `data.groupKind`) |
| `container` | (Create Container command) | `GroupNodeView.tsx` |
| `chart` | Chart | `ChartNodeView.tsx` |

"System Prompt" is a plugin-picker entry, not a distinct node kind - it creates a `note` node with `is_system_prompt=True`. There is no separate `reasoning`/`workflow`/`graph_diff`/`quality_gate`/`code_review` node kind - those plugin categories were removed before the Qt-removal effort even began and were never ported.

### Connections: one unified model, not 13 parallel lists

`SceneDocument.edges: dict[str, SceneEdge]` is the entire connection model at runtime - see Architecture Truths #6. The legacy split (structural parent index / child list / 13 named `*_connections` lists) only exists as a save-time classification inside `backend/session_save.py::_classify_edges`, to keep `chats.db` byte-compatible with what the deleted Qt app could read.

### Other persisted scene objects

- notes, frames, containers, charts, navigation pins - all live as `SceneNode`/dedicated-model entries in `backend/domain/` (`model.py`/`graph.py`), same as every node kind above.

## Core Runtime Flows

### 1. Application boot

1. `graphlink_desktop.py:main()` - configure logging/exception handlers, check the crash sentinel, mark running.
2. Verify `web_ui/dist/app/index.html` exists (exit 1 with a build instruction if not).
3. Pick a port (`GRAPHLINK_BACKEND_PORT` env var or an OS-assigned free port), start `backend.app.create_app()` under `uvicorn` in a daemon thread.
4. Poll `GET /api/health` until it returns 200 (`STARTUP_TIMEOUT_SECONDS = 15.0`).
5. Open one `pywebview` window pointed at the backend's own origin; `webview.start()` blocks until the window closes.
6. On clean close, `mark_clean_exit()` removes the crash sentinel.

### 2. Provider-mode handling

- Active provider state (`api_provider`'s module-level state) is bootstrapped ONCE per process from the shared `SettingsManager`, via `backend/agents.py::bootstrap_provider_state()`, called once from `create_app()`.
- Configuring each provider's models/credentials happens through `backend/settings.py`'s intents, driven by `SettingsDialog.tsx`'s real Ollama/Llama.cpp/API Endpoint/Integrations tabs.
- Switching which provider is *active* from the toolbar is NOT wired - `AppBar.tsx`'s provider `<select>` is hardcoded-disabled (see Architecture Truths #5).

### 3. Prompt send / response flow

1. User types in `Composer.tsx`; draft state lives in `ComposerStore`/`backend/composer.py`.
2. Send dispatches the `scene` topic's `sendMessage` intent (`backend/canvas.py::send_message`, wired via `register_canvas`).
3. `send_message` creates the real user `chat` `SceneNode`, resolves branch history/system prompt, and hands off to `backend/agents.py`'s `AgentDispatcher`.
4. `AgentDispatcher` calls `api_provider.chat(...)` against whichever provider is currently active, streaming tokens back over the same session's WebSocket as incremental `scene` topic publishes.
5. `backend/response_parsing.py::parse_response()` splits the completed reply into text/thinking/code parts; `send_message` creates the corresponding child nodes (chat/thinking/code) from those parts.
6. The SPA's `sceneStore.ts` receives each `scene` snapshot and re-renders the React Flow graph; `regenerateResponse`/`generateImage`/`regenerateImage` intents follow the same dispatch-and-stream shape for their respective actions.
7. A client that disconnects while a request is in flight has that request cancelled server-side once its session's last WebSocket connection drops (`backend/app.py`'s `ws_endpoint` finally-block calls `agent_dispatcher.cancel_all()` and `cancel_all_pending_approvals()`).

### 4. Save / load flow

1. Explicit save: the `app-chat-library` topic's `saveChat` intent (`backend/chat_library.py`) calls into `backend/session_save.py`'s `build_chat_data`/`save_chat_atomically_row` primitives, writing a legacy-compatible row into `~/.graphlink/chats.db`.
2. Autosave: `backend/autosave.py` reuses those SAME save primitives on a 30s per-session timer, change-guarded against redundant writes.
3. Load: the `app-chat-library` topic's `loadChat` intent restores a `chats.db` row into a fresh `SceneDocument` via `backend/session_load.py`'s single-forward-pass restoration algorithm.

### 5. Crash recovery flow

1. `graphlink_desktop.py:main()` calls `mark_running()` (writes `~/.graphlink/running.lock`) before opening the webview, and checks `previous_run_crashed()` (was the sentinel already there from a prior run that never reached a clean exit) first.
2. If the previous run crashed, `backend/app.py::_configure_session` calls `maybe_show_crash_notice()`, surfacing a real in-app notification on the `notification` topic.
3. `configure_logging()`/`install_exception_handlers()` (both idempotent, process-wide) route unhandled exceptions and native/segfault crashes into `~/.graphlink/graphlink.log` for post-mortem, since a windowed app with no console would otherwise lose them entirely.
4. `mark_clean_exit()` removes the sentinel on a normal window close.

### 6. Plugin lifecycle

1. `backend/plugins.py::get_plugin_categories()` supplies the `app-plugins` topic's static category/plugin listing (a from-scratch reimplementation of the deleted `PluginPortal`'s algorithm, not an import).
2. `PluginPicker.tsx` renders that listing and dispatches the `app-plugins` topic's `executePlugin` intent with the selected plugin name and the currently-selected node id.
3. `execute_plugin()` validates the plugin name and required parent, then calls the matching `SceneDocument.add_*_node()` method (defined in `backend/domain/graph.py`, invoked via canvas.py's wrappers) and publishes `scene` - every one of the 8 picker entries does real node creation today (see Architecture Truths #7).

## Plugin Catalog As Registered Today

This is the live registration order in `backend/plugins.py::_PLUGINS` / `_CATEGORY_META` (an independent Qt-free reimplementation of the deleted `PluginPortal.get_plugin_categories()`, verified field-for-field against `backend/plugins.py` directly).

### Branch Foundations

- `System Prompt` - creates a `note` node with `is_system_prompt=True`, attached above the selected node's branch root.
- `Conversation Node` - creates a `conversation` node.

### Reasoning & Research

- `Web Research` - creates a `web_research` node.

### Build & Execution

- `Gitlink` - creates a `gitlink` node.
- `Py-Coder` - creates a `pycoder` node.
- `Virtual Environment Runner` - creates a `code_sandbox` node.
- `HTML Renderer` - creates an `html` node (starts with empty content).

### Workflow & Drafting

- `Artifact / Drafter` - creates an `artifact` node.

`Validation & Delivery` is defined in `_CATEGORY_META` but has zero plugins mapped to it today, so `get_plugin_categories()` filters it out of the returned listing (same "skip empty categories" algorithm the deleted `PluginPortal` used). There is no `Reasoning`/`Workflow Architect`/`Quality Gate`/`Code Review Agent`/`Branch Lens (GraphDiff)` plugin - those were removed well before the Qt-removal effort began and were never carried into `backend/plugins.py`.

## Concrete File Index

This is the practical lookup map for where code actually lives today.

### `backend/` (all Python domain logic - no UI code anywhere in this package)

- `app.py` - FastAPI app factory, `/ws` endpoint, WS origin validation, static SPA serving.
- `canvas.py` - the canvas ORCHESTRATION/WIRE layer only since ADR-002 stage 2.2: `register_canvas()` (every scene/grid topic + intent) and the wire-only helpers. Still the compatibility import surface - `from backend.canvas import SceneDocument/SceneNode/_content_codec/...` all keep working (canvas re-imports them for its own use).
- `domain/` - the pure scene domain, split out of canvas.py at ADR-002 stage 2.2 and permanently gated by `tests/test_domain_purity.py` (AST check: no fastapi/events/notifications/agents/token_counter imports, no bus-shaped calls): `model.py` (`SceneNode`, `SceneEdge`, errors, layout/appearance constants), `content_codec.py` (the shared `_content_codec` namespace - ONE instance), `graph.py` (`SceneDocument`, composed as `class SceneDocument(BranchOps, GroupOps)` with core node/edge/chart/view-state methods), `branches.py` (`BranchOps`: branch-tree semantics, `send_message`/`chat_branch_history`/`delete_chat_node`/branch status), `groups.py` (`GroupOps`: frame/container geometry). Patch seams: names bound in `domain/*` resolve in THOSE modules' namespaces - patch `backend.domain.graph.<name>`, never `backend.canvas.<name>`, for document behavior.
- `agents.py` - `AgentDispatcher`, provider bootstrap, per-request cancellation.
- `composer.py` - composer draft/reasoning-level state, `app-composer` topic.
- `settings.py` - `register_settings()`, `app-settings` topic, every provider's settings intents.
- `chat_library.py` - `app-chat-library` topic: list/rename/delete/load/new-chat intents against `~/.graphlink/chats.db`.
- `session_load.py` / `session_save.py` - the load/save algorithms proper (called by `chat_library.py` and `autosave.py`, not topics themselves).
- `autosave.py` - the 30s per-session background save task.
- `crash_recovery.py` - sentinel file, rotating log, exception handlers, crash notice.
- `response_parsing.py` - `parse_response()`, the thinking/text/code splitter.
- `plugins.py` - `app-plugins` topic, plugin catalog + `executePlugin`.
- `about.py`, `notifications.py`, `token_counter.py` - their respective small, focused topics.
- `assets.py` - `GET /api/assets/{id}`, the image-node byte-serving route.
- `events.py` - `EventBus`/`SessionBus`, the pub/sub primitive every topic module registers onto.
- `native_dialogs.py` - native OS file/folder picker support (used by Llama.cpp GGUF scanning).
- `tests/` - the backend pytest suite (25 files as of this writing).

### `web_ui/src/app/`

- `App.tsx` - the shell: WS transport setup, `system`/`app-settings` subscriptions, global keyboard shortcuts, top-level layout mounting every chrome/overlay/canvas piece.
- `main.tsx` - the React root/entry point.
- `canvas/` - `SceneCanvas.tsx` (the React Flow surface), `sceneStore.ts` (the `scene` topic client), one `*NodeView.tsx` per node kind (see Taxonomy table), `smartGuides.ts`, `treeNavigation.ts`, `exportCanvasPng.ts`, `downloadTextFile.ts`.
- `chrome/` - `AppBar.tsx`, `Composer.tsx` + `composerStore.ts`, `ViewPopover.tsx`, `CommandPalette.tsx` + `commands.ts`, `SearchOverlay.tsx`, `PinOverlay.tsx`, `PluginPicker.tsx`, `SettingsDialog.tsx`, `ChatLibraryDialog.tsx`, `AboutDialog.tsx`, `HelpDialog.tsx` (+ `help-data/sections.ts`), `NotificationBanner.tsx`, `TokenCounter.tsx`, `shortcuts.ts`.
- `overlays/overlays.tsx` - the `OverlayProvider` coordinator only (no dialogs live here).

### `web_ui/src/lib/`

- `ws/transport.ts` - `WsTransport`, the one WebSocket client implementation.
- `api-contract/topics.ts` - `TOPIC_VALIDATORS`, validating every incoming snapshot against its generated JSON Schema before use.
- `bridge-core/generated/` - codegen'd TS types + JSON Schemas, one pair per `contracts/` payload dataclass (11 pairs).
- `bridge-core/islandState.ts`, `schemaVersion.ts`, `textFocus.ts` - small shared bridge helpers (naming is a holdover from the pre-SPA per-island era; there is only one app target now).
- `tokens/gl-theme.css`, `gl-vars-dev.css` - the design-token CSS variables.
- `ui/BridgeErrorState.tsx`, `base.css` - shared error-state UI and the CSS reset.

### `contracts/` (build-time codegen, not runtime application code)

- `codegen.py` - `GENERATED_ARTIFACTS` (11 entries), `--check`/`--write` CLI, the TS/JSON-Schema generation logic.
- `payload_schema.py` - JSON Schema generation from Python dataclasses.
- `graphlink_app_*_payload.py` / `graphlink_*_payload.py` - the 11 payload dataclasses (about, chat_library, composer, plugins, settings, drag_speed, font_control, grid_control, notification, scene, token_counter).
- `tests/test_generated_artifacts.py` - parametrized over all 11 entries plus the `--check`/`--write` CLI drift tests.

### `graphlink_plugins/` (Qt-free plugin domain logic only)

- `web_research/` - `domain.py`, `ports.py`, `fetch_policy.py`, `providers.py`, `service.py` (no `worker.py` - that was the Qt-coupled file deleted at the cutover).
- `gitlink/` - `agent.py`, `repository.py`.
- `pycoder/` - `domain.py`.
- `code_sandbox/` - `domain.py`.
- `common/` - `github_client.py`, `llm_json.py` (shared helpers).

### Loose top-level modules that matter most

- `api_provider.py` - provider abstraction for Ollama/Llama.cpp/OpenAI-compatible/Anthropic/Gemini; local model scanning; `chat()`/`generate_image()`.
- `graphlink_secrets.py` - Windows DPAPI secret encryption for `~/.graphlink/session.dat`.
- `graphlink_settings_store.py` - `SettingsManager`, the persisted-settings authority `backend/settings.py` wraps.
- `graphlink_task_config.py` - task keys, mode labels, `API_PROVIDER_*` constants.
- `graphlink_desktop.py` - the real entry point (see Runtime Ownership Map).
- `graphlink_memory.py` - branch/history helpers used by `backend/canvas.py::send_message`.
- `graphlink_chart_agent.py`, `graphlink_chart_data.py`, `graphlink_chart_rendering.py` - the chart-node pipeline (spec extraction/repair, rendering to PNG).
- `graphlink_artifact_agent.py`, `graphlink_chat_agent.py` - LLM-facing agent logic `backend/agents.py`/`backend/canvas.py` call into.

## Where To Edit When...

### You want to change startup or the desktop shell

- `graphlink_desktop.py`
- `backend/app.py` (app factory, WS origin policy)
- `backend/crash_recovery.py` (sentinel/logging)

### You want to change provider configuration or default local models

- `api_provider.py`
- `graphlink_task_config.py`
- `graphlink_settings_store.py`
- `backend/settings.py` (+ `web_ui/src/app/chrome/SettingsDialog.tsx`)

### You want to change prompt send, response parsing, or agent dispatch

- `backend/canvas.py` (`send_message`, `regenerate_response`)
- `backend/agents.py` (`AgentDispatcher`)
- `backend/response_parsing.py`
- `web_ui/src/app/chrome/Composer.tsx` + `composerStore.ts`

### You want to change canvas behavior, graph layout, or view-state (grid/drag-speed/fade/orthogonal/smart-guides)

- `backend/domain/graph.py` (`SceneDocument`, the view-state model) + `backend/canvas.py` (the view-state intents)
- `web_ui/src/app/canvas/SceneCanvas.tsx` + `sceneStore.ts`
- `web_ui/src/app/canvas/smartGuides.ts` (pure geometry)
- `web_ui/src/app/chrome/ViewPopover.tsx` (the consolidated drag/grid/font popover)

### You want to add or modify a node kind

- Backend model: `backend/domain/` (`model.py` fields, `graph.py` methods); intents: `backend/canvas.py`
- React component: a new `web_ui/src/app/canvas/*NodeView.tsx`
- Persistence: `backend/session_load.py` and `backend/session_save.py` (both need the new kind's restore/classify logic)
- Contract: a new payload field/shape in `contracts/graphlink_scene_payload.py` if the node needs new wire fields, then `python contracts/codegen.py --write` to regenerate the TS side

### You want to add or modify a plugin

- Domain logic: `graphlink_plugins/<name>/` (new package) or an existing one
- Registration + node-creation wiring: `backend/plugins.py`
- Picker UI: `web_ui/src/app/chrome/PluginPicker.tsx` (usually needs no change - it renders whatever `app-plugins` returns)
- New node kind (if the plugin creates one): see "add or modify a node kind" above

### You want to change save/load compatibility with `~/.graphlink/chats.db`

- `backend/session_load.py`
- `backend/session_save.py`
- `backend/chat_library.py` (the topic/intents that call into both)
- `backend/autosave.py` (reuses the same save primitives)

### You want to change crash recovery or logging

- `backend/crash_recovery.py`
- `graphlink_desktop.py` (the calls into it at boot/shutdown)

### You want to change settings, secrets, or provider credential storage

- `backend/settings.py`
- `graphlink_settings_store.py`
- `graphlink_secrets.py` (DPAPI encryption)
- `web_ui/src/app/chrome/SettingsDialog.tsx`

### You want to change the WS wire contract (topics/payload shapes)

- The relevant `contracts/graphlink_*_payload.py` dataclass
- `contracts/codegen.py` (`GENERATED_ARTIFACTS` registry, if adding a new topic entirely)
- Run `python contracts/codegen.py --write` from repo root, then `cd web_ui && npm run check:schema` to confirm no drift
- The backend topic's own `register_topic`/`register_intent` calls (`backend/<module>.py`)
- The SPA's consuming store/component under `web_ui/src/app/`

### You want to change overlays, dialogs, or chrome UI

- `web_ui/src/app/overlays/overlays.tsx` (the coordinator itself - rarely needs touching)
- `web_ui/src/app/chrome/*.tsx` (every actual dialog/popover/bar lives here, including Settings)

## Short Working Rules For Future Sessions

- The migration is complete. Do not describe this codebase as "mid-migration" or reference compatibility wrappers - there are none, and `graphlink_app/` does not exist.
- Treat `backend/domain/`'s `SceneDocument` (`graph.py`, data model in `model.py`) as the graph schema authority - nodes, edges, and view-state all live there, in one unified model (not 13 parallel connection lists). `backend/canvas.py` is the wire/orchestration layer and the stable import surface.
- Treat `backend/agents.py`'s `AgentDispatcher` as the execution/cancellation authority for every LLM-backed request; it is instantiated once per session, never as a module-level singleton.
- Treat `backend/plugins.py` as the plugin catalog authority - every entry is a real node-creation path today, not a deferred notice.
- Treat `backend/app.py::_is_allowed_ws_origin` as a real security control, not incidental scaffolding - do not relax its exact-match policy without understanding the DNS-rebinding threat model in its own docstring.
- Treat `contracts/` as build-time-only: it generates code the SPA imports, but it is never itself imported at runtime by `backend/` or `web_ui/`. Regenerate with `python contracts/codegen.py --write` after changing any payload dataclass, and verify with `--check` (or `npm run check:schema`) before shipping.
- Verify with the CI command, not a narrowed one: `python -m pytest -q` from the repo root (961 tests across `backend/tests/`, `contracts/tests/`, and `tests/`), plus `npm run check` from inside `web_ui/` (schema-drift check, typecheck, lint, 779 vitest tests, build). A subset run can look green while missing real regressions in a directory it never collected.
- `tests/test_no_qt_anywhere.py` is a permanent gate, not a migration-era placeholder - it fails the build if any file anywhere imports PySide6/PyQt, or if any requirements/pyproject manifest declares one of the removed Qt packages.
- Remember `doc/` is gitignored and local-only - never treat it as shipped documentation, and don't "fix" it as if a contributor will ever see it.
- Remember Windows DPAPI (`graphlink_secrets.py`) is still real and still the reason CI is pinned to `windows-latest` - this is not leftover Qt-era inertia.
- Remember the AppBar's provider-mode `<select>` is deliberately still disabled - don't assume it's wired just because Settings has real per-provider pages underneath it.
