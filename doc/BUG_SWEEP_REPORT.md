# Graphite App Bug Sweep Findings

## Scope

Static inspection performed across the Python application entrypoints, session persistence path, background worker lifecycle, window lifecycle, and core utility modules under `graphite_app/`.

## Method

- Repository-wide keyword scans for risky patterns (`TODO`, broad `except`, thread lifecycle symbols).
- Targeted call-path tracing for user-facing entry points (chat load, new chat, shutdown, background workers).
- Line-level validation of high-risk functions.

## Findings

### 1) High: Session load returns success even when scene restore failed

- **Location:** [graphite_app/graphite_session/manager.py:28-43](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/manager.py#L28), [graphite_app/graphite_session/deserializers.py:656-733](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/deserializers.py#L656)
- **Issue:** `load_chat()` sets `current_chat_id` only when `restore_chat()` succeeds, but still returns the raw chat object on failure.
- **Impact:** Callers can treat a corrupted or partially restored chat as a successful load, leaving scene state inconsistent.
- **Status:** ✅ Fixed in [graphite_app/graphite_session/manager.py:28-43](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/manager.py#L28).
- **Fix applied:** `load_chat()` now returns `None` when restore fails and only publishes `current_chat_id` after successful restore.

### 2) High: Save worker is not cooperatively cancellable

- **Location:** [graphite_app/graphite_session/workers.py:4-37](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/workers.py#L4), [graphite_app/graphite_session/manager.py:68-102](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/manager.py#L68)
- **Issue:** `SaveWorkerThread` had no `stop`/cancellation path, and no signal path for cancellation completion.
- **Impact:** Shutdown could stall waiting on the worker even after a cancel request.
- **Status:** ✅ Fixed in [graphite_app/graphite_session/workers.py:4-37](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/workers.py#L4) and [graphite_app/graphite_session/manager.py:68-83](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/manager.py#L68).
- **Fix applied:** Added cooperative stop event and `cancelled` signal to save worker, plus manager callback handling for cleanup.

### 3) High: Subprocess output loop can ignore cancellation/timeout for non-newline output

- **Location:** [graphite_app/graphite_agents_code_sandbox.py:154-177](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_code_sandbox.py#L154).
- **Issue:** `_run_subprocess()` called `stdout.readline()` in a blocking loop; cancellation/timeout checks could miss timely interruption when output is sparse or buffered.
- **Impact:** Stop requests and timeout enforcement appeared unresponsive for some sandbox runs.
- **Status:** ✅ Fixed in [graphite_app/graphite_agents_code_sandbox.py:154-197](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_code_sandbox.py#L154).
- **Fix applied:** Replaced blocking read loop with background reader thread + queue and timeout-aware polling.

### 4) Medium: New chat does not cancel in-flight chat request

- **Location:** [graphite_app/graphite_window.py:1329-1336](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window.py#L1329), [graphite_app/graphite_window_actions.py:321-322](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L321)
- **Issue:** `new_chat()` cleared scene state without canceling the main request thread.
- **Impact:** Late responses from a previous request could attach to stale or detached nodes.
- **Status:** ✅ Fixed in [graphite_app/graphite_window.py:1329-1336](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window.py#L1329), [graphite_app/graphite_window_actions.py:321-326](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L321).
- **Fix applied:** `new_chat()` now cancels active main requests before scene reset; `handle_response()` now ignores detached/stale target nodes.

### 5) Medium: Update-version comparator fallback comparator is lexicographic

- **Location:** [graphite_app/graphite_update.py:21-39](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_update.py#L21).
- **Issue:** Fallback version comparison used raw string ordering, which can misorder numeric-like values.
- **Impact:** Incorrect update availability messaging in edge-case version formats.
- **Status:** ✅ Fixed in [graphite_app/graphite_update.py:21-39](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_update.py#L21).
- **Fix applied:** Primary comparison now uses `packaging.version` when available and falls back to numeric-token comparison.

### 6) Low: `PyCoderStage` duplicate enum value

- **Location:** [graphite_app/graphite_agents_pycoder.py:14-20](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_pycoder.py#L14).
- **Issue:** `PyCoderStage.REPAIR` and `PyCoderStage.EXECUTE` shared numeric value `3`.
- **Impact:** Serialized/logged statuses could collapse distinct workflow stages.
- **Status:** ✅ Fixed in [graphite_app/graphite_agents_pycoder.py:14-20](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_pycoder.py#L14).
- **Fix applied:** Assigned unique enum values to all stage constants.

## Fix status

- 2026-07-07: All seven findings marked fixed.
- Documentation and fix trace were updated in this report.

## 7) Bug: Drag slider style sheet NameError from unresolved semantic color interpolation
- **Location:** [graphite_app/graphite_view.py:299-305](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_view.py#L299)
- **Issue:** `setStyleSheet` in drag control used an interpolated expression in a style string in a way that evaluated `{...}` as formatting placeholder, causing `NameError: name 'background' is not defined` on startup.
- **Fix applied:** Captured the semantic color in `drag_slider_color` and built the stylesheet using an explicit string concat with an f-string for only the color token.
- **Status:** Fixed

### 8) Improvement: Ollama settings model list now auto-detects local availability and excludes stale entries

- **Location:** [graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py:331-335](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py#L331), [graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py:432-440](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py#L432), [graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py:537-546](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_ui_dialogs/graphite_settings_dialogs.py#L537)
- **Issue:** Ollama model combo boxes still reflected legacy/static values and could include models that were not available in local scan results.
- **Impact:** Users could pick models that do not currently exist in local Ollama storage.
- **Status:** Fixed
- **Fix applied:** The dialog now refreshes the local Ollama scan cache when opened and builds model lists solely from discovered local models; no hardcoded/default fallback is used for the settings dropdowns.

### 9) High: Ollama scan path normalization missed custom OLLAMA_MODELS roots

- **Location:** [graphite_app/api_provider.py:97-107](C:/Users/Admin/source/repos/graphite_app/graphite_app/api_provider.py#L97), [graphite_app/api_provider.py:241-257](C:/Users/Admin/source/repos/graphite_app/graphite_app/api_provider.py#L241)
- **Issue:** `_normalize_ollama_models_root` always appended `/models/manifests` unless the path name was exactly `manifests` or `models`, so a custom `OLLAMA_MODELS` root like `D:\OllamaModels` resolved to `D:\OllamaModels\models\manifests` instead of `D:\OllamaModels\manifests`.
- **Impact:** Local model scan returned no results even when manifests were present in the configured root folder, causing empty model dropdowns.
- **Status:** Fixed
- **Fix applied:** Updated normalization to detect and use existing `manifests` directories before appending extra path segments, and to fall back only when the expected manifest path is not already present.

### 10) High: Chat restore crashes when saved chat payload lacks `nodes`

- **Location:** [graphite_app/graphite_session/deserializers.py:656-670](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/deserializers.py#L656)
- **Issue:** `restore_chat()` indexed `chat_data["nodes"]` directly, which raised `KeyError` for malformed/corrupt saves that only stored legacy or partial payloads.
- **Impact:** Opening a corrupted/older chat could terminate restoration with an exception and leave the user without clear recovery.
- **Status:** Fixed
- **Fix applied:** Restores now read node payloads through `chat_data.get("nodes")`, then `chat_data.get("items")`, and finally a nested `chat_data["data"]` fallback for legacy wrappers. If no valid list exists, restore continues with an empty chat instead of throwing, and shows a warning banner so malformed sessions still open safely.

### 11) High: Chat persistence could stall permanently after first serialization failure

- **Location:** [graphite_app/graphite_session/manager.py:18-95](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/manager.py#L18), [graphite_app/graphite_session/manager.py:97-139](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/manager.py#L97)
- **Issue:** `save_current_chat()` set `_is_saving = True` before serializing chat data, but did not handle serializer errors. If serialization raised, `_is_saving` remained True indefinitely, so every later save request was silently ignored.
- **Impact:** Users could send multiple updates with no persisted chat row changes and saw no reliable save success signal, matching the “not saving” complaint.
- **Status:** Fixed
- **Fix applied:** Added save-queueing and robust error handling around payload serialization:
  - Save requests arriving while a background save is active are queued and retried after completion.
  - Serialization exceptions now reset `_is_saving`, show a user-visible error, and return cleanly.
  - Successful save callbacks clear the in-flight state and flush any queued save.
  - `window.save_chat()` now only shows a success toast when a save request was actually started.

### 12) Medium: Restore should skip invalid node payload entries

- **Location:** [graphite_app/graphite_session/deserializers.py:205-223](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/deserializers.py#L205), [graphite_app/graphite_session/deserializers.py:680-690](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/deserializers.py#L680)
- **Issue:** `restore_chat()` attempted to deserialize malformed entries (non-dict payloads) while rebuilding nodes and children, which could abort the entire restore on old/corrupt data.
- **Impact:** One bad node payload could prevent any session restore and force a blank scene.
- **Status:** Fixed
- **Fix applied:** `deserialize_node()` and `_restore_children()` now ignore invalid node payloads and continue restoring valid entries.

### 13) Critical: New chat saves could be blocked before the database insert

- **Location:** [graphite_app/graphite_session/workers.py:1-59](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/workers.py#L1), [graphite_app/graphite_window_actions.py:286-300](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L286), [graphite_app/graphite_ui_dialogs/graphite_library_dialog.py:347-374](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_ui_dialogs/graphite_library_dialog.py#L347)
- **Issue:** New chat saves generated an AI title before inserting the chat row. If title generation stalled or failed through the local model path, the save worker never reached `save_chat()`, so no row appeared in the chat library.
- **Impact:** Chats appeared to never save, and the library remained empty except for old rows.
- **Status:** Fixed
- **Fix applied:** New saves now use a deterministic local fallback title and write the row immediately. The main send flow also triggers a silent save as soon as the user message node exists, so a failed assistant response no longer prevents the conversation from appearing in the library. Library timestamp formatting is now tolerant of unexpected row values so one malformed timestamp cannot break the list refresh.
