"""ADR-002 stage 2.7: Settings dialog - Ollama page (reasoning mode,
system model scan, per-task model assignment, model pull).

Relocated VERBATIM from backend/settings.py's former register_settings
(closures at its former lines 754-974) - pure code motion, no behavior
change.
"""

from __future__ import annotations

import asyncio
import threading

import ollama

import api_provider
import graphlink_task_config as config
from graphlink_model_catalog import AUTO_MODEL, INHERIT_MODEL

from backend.api._settings_shared import (
    OLLAMA_TASK_KEYS,
    REASONING_LEVELS,
    SettingsSessionState,
    apply_ollama_reasoning_level,
    pick_scan_folder_and_run,
    republish_composer_reasoning,
    run_locked,
)
from backend.events import SessionBus
from graphlink_settings_store import SettingsManager


def register_settings_ollama_intents(
    bus: SessionBus, manager: SettingsManager, state: SettingsSessionState
) -> None:
    async def set_ollama_reasoning_level(level: str):
        # Checked and applied inside the SAME to_thread hop, not split across
        # an await boundary: a concurrent mode switch (the toolbar's provider
        # selector) landing in a gap between a separate check and a later
        # apply could otherwise force the live provider back to Ollama after
        # the user had already switched away - the same class of
        # stale-check-then-await race the R7.4a audit found and fixed
        # elsewhere in this file.
        # Legacy-parity note (corrected after adversarial review): this live
        # re-apply is NOT what legacy's own settings dialog did.
        # graphlink_settings_bridge.py's setOllamaReasoningMode called
        # _reinitialize_main_window_agent() unconditionally, which just
        # rebuilds ChatAgent - it never touched the live
        # OLLAMA_REASONING_MODE global api_provider.py's chat dispatch
        # actually reads. Legacy's ONLY path that ever updated that global
        # was a completely separate control - the composer toolbar's
        # setReasoningLevel - which the Settings dialog was never wired to.
        # So in real legacy usage, changing reasoning mode via Settings
        # persisted the value but left the live think= kwarg stale until the
        # next provider-mode switch or restart. This re-apply is a genuine
        # fix for that gap, not a port of existing legacy behavior - gated on
        # is_local_ollama_mode() so it can't be the mechanism that forces a
        # user who already switched to a different provider back onto
        # Ollama.
        #
        # R7.5d: this apply/re-apply sequence is shared with
        # backend/composer.py's own setReasoningLevel intent - see
        # apply_ollama_reasoning_level's own docstring
        # (backend/api/_settings_shared.py).
        level = str(level)
        if level not in REASONING_LEVELS:
            return
        await apply_ollama_reasoning_level(manager, level)
        await bus.publish("app-settings")
        await republish_composer_reasoning(bus)

    async def set_ollama_model_assignment(task: str, value: str):
        # Coerced before use, matching every intent in this file post the
        # R7.4a audit: task ends up as a dict key below, and a client can
        # send any JSON type over the wire with zero validation anywhere
        # in the dispatch path.
        task = str(task)
        value = str(value).strip()
        if task not in OLLAMA_TASK_KEYS:
            return
        if task == config.TASK_CHAT and value == INHERIT_MODEL:
            # Adversarial-review finding: task_chat has no "inherit"
            # concept - it IS the base chat model, and its <select> in
            # SettingsDialog.tsx never renders an "inherit" option (task !==
            # "task_chat" guards it there). Without this normalization, a
            # stray or hand-edited "inherit" for this one task would persist
            # fine but leave the frontend's <select> showing a value with no
            # matching <option> - falling back here to auto instead, which
            # the UI CAN represent.
            value = AUTO_MODEL
        if not value or value in (INHERIT_MODEL, AUTO_MODEL):
            assignment = {"mode": value or AUTO_MODEL, "model_id": ""}
        else:
            assignment = {"mode": "explicit", "model_id": value}

        def _persist() -> None:
            # Read-modify-write, all inside this one run_locked closure -
            # not read-before-await-then-write-after. Splitting those across
            # an await boundary is exactly the R7.4a save_api_configuration
            # race: a concurrent assignment change for a DIFFERENT task
            # (two browser tabs on the same session) landing in the gap
            # would get its own freshly-persisted change silently reverted
            # by this call's stale pre-read of the whole assignments dict.
            assignments = manager.get_ollama_model_assignments()
            assignments[task] = assignment
            manager.set_ollama_model_assignments(assignments)
            # ADR-006 stage 6.5 (H6): locked writer wrappers - see
            # api_provider.sync_ollama_models.
            api_provider.sync_ollama_models(manager)
            if task == config.TASK_CHAT and assignment["mode"] == "explicit":
                api_provider.set_current_ollama_model(assignment["model_id"])

        await asyncio.to_thread(run_locked, _persist)
        await bus.publish("app-settings")

    async def _run_ollama_scan(scan_path: str | None) -> None:
        # Extracted (R7.4c) from what used to be scan_ollama_system's own
        # monolithic body, unchanged in behavior - factored out so
        # pick_ollama_scan_folder (below) can share it with a real path
        # instead of duplicating the scan/persist/report sequence.
        state.ollama_scan_status = "running"
        state.ollama_notice = ""
        await bus.publish("app-settings")

        try:
            # scan_local_ollama_models never raises for "Ollama not
            # running" - that's reported via the returned server_reachable/
            # server_error fields (which legacy's own worker/bridge never
            # surfaced either; matched here, not newly dropped). Only a
            # genuine, unexpected exception (e.g. a filesystem error
            # walking a manifest folder) reaches this except.
            results = await asyncio.to_thread(api_provider.scan_local_ollama_models, scan_path)
        except Exception as exc:  # noqa: BLE001 - no secret in this path to redact
            state.ollama_scan_status = "error"
            state.ollama_notice = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            manager.set_ollama_model_scan_cache(
                results.get("models", []),
                results.get("scan_mode", ""),
                results.get("scan_path", ""),
                results.get("locations", []),
            )
            api_provider.sync_ollama_models(manager)

        try:
            # Adversarial-review finding: set_ollama_model_scan_cache does a
            # real disk write (SettingsManager._save_state - json dump +
            # fsync + atomic replace), which CAN fail (locked file,
            # permission denied, disk full). Before this fix, a failure here
            # propagated uncaught out of the intent handler - the WS layer
            # logs and survives it, but ollama_scan_status was left at
            # "running" forever, so the "already running" reentrancy guard
            # at the top of this function would silently no-op every future
            # scan for the rest of the session. Legacy never had this
            # failure mode: its reentrancy check was tied to the QThread
            # object, nulled at the start of its finished-handler regardless
            # of whether persistence succeeded.
            await asyncio.to_thread(run_locked, _persist)
        except Exception as exc:  # noqa: BLE001 - no secret in this path to redact
            state.ollama_scan_status = "error"
            state.ollama_notice = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return
        state.ollama_scan_status = "done"
        await bus.publish("app-settings")

    async def scan_ollama_system():
        # Check-then-set with no await between them - safe under asyncio's
        # single-threaded event loop (unlike the two genuinely async-gapped
        # races above), matching legacy's own isRunning()-guarded no-op.
        if state.ollama_scan_status == "running":
            return
        await _run_ollama_scan(None)

    async def pick_ollama_scan_folder():
        # R7.4c: the retroactive un-defer of this page's own "Scan
        # Folder..." button, now that native_dialogs exists. Matches
        # legacy's pickOllamaScanFolder(): a cancelled dialog is a quiet
        # no-op, not an error. The reentrancy guard is set BEFORE the
        # (blocking, potentially long-lived-until-the-user-decides) native
        # dialog opens, not after - two near-simultaneous clicks must not
        # both reach a native file-dialog call.
        #
        # R8b: this guard/dialog/error-mapping sequence is shared with
        # intents_settings_llama_cpp.py's own pickLlamaCppScanFolder - see
        # pick_scan_folder_and_run's own docstring
        # (backend/api/_settings_shared.py). The Adversarial-review finding
        # that motivated the try/except here (the native dialog call itself
        # can raise - a per-platform GTK/COM/file-type-parsing failure
        # inside pywebview's create_file_dialog, confirmed reachable via its
        # own source, not theoretical) now lives in that shared function.
        await pick_scan_folder_and_run(
            bus, state,
            status_field="ollama_scan_status", notice_field="ollama_notice",
            saved_scan_path=manager.get_ollama_model_scan_path(),
            run_scan=_run_ollama_scan,
        )

    def _installed_model_names() -> list[str]:
        """Only the Ollama daemon's installed models are removable.

        The saved system/folder scan cache may include manifests from other
        directories or stale entries, so it is not an authority for deletion.
        """
        response = ollama.list()
        entries = response.get("models", []) if isinstance(response, dict) else getattr(response, "models", [])
        models = set()
        for entry in entries or []:
            name = (entry.get("model") or entry.get("name", "")) if isinstance(entry, dict) else (
                getattr(entry, "model", None) or getattr(entry, "name", "")
            )
            if name:
                models.add(str(name).strip())
        return sorted(models - {""}, key=str.lower)

    def _update_scan_cache(model_name: str, *, remove: bool = False) -> None:
        """Update the existing discovery cache without erasing scan provenance."""
        existing = manager.get_ollama_scanned_models()
        if remove:
            models = [name for name in existing if name != model_name]
        else:
            models = [*existing, model_name]
        manager.set_ollama_model_scan_cache(
            models,
            manager.get_ollama_model_scan_mode() or "system",
            manager.get_ollama_model_scan_path(),
            manager.get_ollama_model_scan_locations(),
        )

    async def refresh_ollama_installed_models():
        if state.ollama_installed_status == "running" or state.ollama_remove_status == "running":
            return
        state.ollama_installed_status = "running"
        state.ollama_installed_error = ""
        await bus.publish("app-settings")
        try:
            names = await asyncio.to_thread(_installed_model_names)
        except Exception as exc:  # noqa: BLE001 - report daemon/unavailable errors to the user
            state.ollama_installed_status = "error"
            state.ollama_installed_error = f"Could not list installed Ollama models: {exc}"
        else:
            state.ollama_installed_models = names
            state.ollama_installed_status = "done"
        await bus.publish("app-settings")

    active_pull_cancel: threading.Event | None = None

    async def pull_ollama_model(model_name: str):
        nonlocal active_pull_cancel
        model_name = str(model_name).strip()
        if not model_name:
            state.ollama_notice = "Model name cannot be empty."
            await bus.publish("app-settings")
            return
        if state.ollama_pull_status in ("running", "cancelling") or state.ollama_remove_status == "running":
            return

        stop = threading.Event()
        active_pull_cancel = stop
        state.ollama_pull_status = "running"
        state.ollama_pull_model = model_name
        state.ollama_pull_message = "Connecting to Ollama..."
        state.ollama_pull_completed = 0
        state.ollama_pull_total = 0
        state.ollama_notice = ""
        await bus.publish("app-settings")

        loop = asyncio.get_running_loop()
        last_progress: tuple[str, int] | None = None

        async def _progress(update: object) -> None:
            nonlocal last_progress
            # A cancelled job must never return to "running" when a queued
            # progress notification arrives after cancelOllamaPull.
            if state.ollama_pull_status != "running":
                return
            value = lambda key, default: (
                update.get(key, default) if isinstance(update, dict) else getattr(update, key, default)
            )
            status = str(value("status", "") or "")
            total = max(0, int(value("total", 0) or 0))
            completed = max(0, int(value("completed", 0) or 0))
            percent = min(100, 100 * completed // total) if total else -1
            # Ollama can send many chunks per percentage point. Publish at
            # most one state update per percentage/status change.
            key = (status, percent)
            if key == last_progress:
                return
            last_progress = key
            state.ollama_pull_message = status
            state.ollama_pull_completed = min(completed, total) if total else 0
            state.ollama_pull_total = total
            await bus.publish("app-settings")

        def _stream_pull() -> bool:
            # Existing pull support remains the single download path; stream
            # it in a worker thread to keep the WebSocket event loop live.
            stream = ollama.pull(model_name, stream=True)
            try:
                for update in stream:
                    if stop.is_set():
                        return False
                    asyncio.run_coroutine_threadsafe(_progress(update), loop).result()
                return not stop.is_set()
            finally:
                # Closing the generator releases its HTTP streaming response.
                close = getattr(stream, "close", None)
                if callable(close):
                    close()

        try:
            completed = await asyncio.to_thread(_stream_pull)
            if not completed:
                state.ollama_pull_status = "cancelled"
                state.ollama_pull_message = "Download cancelled."
                return

            def _persist() -> None:
                _update_scan_cache(model_name)
                api_provider.invalidate_ollama_capability_cache(model_name)
                api_provider.sync_ollama_models(manager)
                # Preserve the old pull action's immediate current-model
                # selection, even for a freshly-downloaded embedding model.
                api_provider.set_current_ollama_model(model_name)

            await asyncio.to_thread(run_locked, _persist)
            state.ollama_installed_models = sorted(
                {*state.ollama_installed_models, model_name}, key=str.lower
            )
            state.ollama_installed_status = "done"
            state.ollama_installed_error = ""
            state.ollama_pull_status = "done"
            state.ollama_pull_message = "Download complete."
            state.ollama_notice = ""
        except asyncio.CancelledError:
            stop.set()
            raise
        except Exception as exc:  # noqa: BLE001 - map Ollama errors to the existing user-facing notice
            if stop.is_set():
                state.ollama_pull_status = "cancelled"
                state.ollama_pull_message = "Download cancelled."
            else:
                text = str(exc).lower()
                if "not found" in text:
                    message = f"Model '{model_name}' was not found on the Ollama hub."
                elif "connection refused" in text:
                    message = "Could not connect to Ollama. Is Ollama running?"
                else:
                    message = f"An unexpected error occurred: {exc}"
                state.ollama_pull_status = "error"
                state.ollama_notice = message
        finally:
            active_pull_cancel = None
            await bus.publish("app-settings")

    async def cancel_ollama_pull():
        # The running worker notices this flag before processing its next
        # chunk; the download cannot restart until it has actually stopped.
        if state.ollama_pull_status == "running" and active_pull_cancel is not None:
            active_pull_cancel.set()
            state.ollama_pull_status = "cancelling"
            state.ollama_pull_message = "Cancelling download..."
            await bus.publish("app-settings")

    async def remove_ollama_model(model_name: str):
        model_name = str(model_name).strip()
        if not model_name:
            state.ollama_notice = "Model name cannot be empty."
            await bus.publish("app-settings")
            return
        if state.ollama_remove_status == "running" or state.ollama_pull_status in ("running", "cancelling"):
            return
        state.ollama_remove_status = "running"
        state.ollama_remove_model = model_name
        state.ollama_notice = ""
        await bus.publish("app-settings")
        try:
            await asyncio.to_thread(ollama.delete, model_name)
            # The daemon has deleted it even if a subsequent settings write
            # fails; keep the displayed installed list truthful.
            state.ollama_installed_models = [
                name for name in state.ollama_installed_models if name != model_name
            ]

            def _persist() -> None:
                _update_scan_cache(model_name, remove=True)
                assignments = manager.get_ollama_model_assignments()
                dirty = False
                for task, assignment in assignments.items():
                    if assignment.get("mode") == "explicit" and assignment.get("model_id") == model_name:
                        assignments[task] = {
                            "mode": AUTO_MODEL if task == config.TASK_CHAT else INHERIT_MODEL,
                            "model_id": "",
                        }
                        dirty = True
                if dirty:
                    manager.set_ollama_model_assignments(assignments)
                api_provider.invalidate_ollama_capability_cache(model_name)
                api_provider.sync_ollama_models(manager)

            await asyncio.to_thread(run_locked, _persist)
            state.ollama_remove_status = "done"
        except Exception as exc:  # noqa: BLE001 - report filesystem/server/settings errors
            state.ollama_remove_status = "error"
            state.ollama_notice = f"Could not remove '{model_name}': {exc}"
        finally:
            state.ollama_remove_model = ""
            await bus.publish("app-settings")

    bus.register_intent("app-settings", "setOllamaReasoningLevel", set_ollama_reasoning_level)
    bus.register_intent("app-settings", "setOllamaModelAssignment", set_ollama_model_assignment)
    bus.register_intent("app-settings", "scanOllamaSystem", scan_ollama_system)
    bus.register_intent("app-settings", "pullOllamaModel", pull_ollama_model)
    bus.register_intent("app-settings", "cancelOllamaPull", cancel_ollama_pull)
    bus.register_intent("app-settings", "refreshOllamaInstalledModels", refresh_ollama_installed_models)
    bus.register_intent("app-settings", "removeOllamaModel", remove_ollama_model)
    bus.register_intent("app-settings", "pickOllamaScanFolder", pick_ollama_scan_folder)
