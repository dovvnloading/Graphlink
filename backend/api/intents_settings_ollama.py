"""ADR-002 stage 2.7: Settings dialog - Ollama page (reasoning mode,
system model scan, per-task model assignment, model pull).

Relocated VERBATIM from backend/settings.py's former register_settings
(closures at its former lines 754-974) - pure code motion, no behavior
change.
"""

from __future__ import annotations

import asyncio

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

    async def pull_ollama_model(model_name: str):
        model_name = str(model_name).strip()
        if not model_name:
            state.ollama_notice = "Model name cannot be empty."
            await bus.publish("app-settings")
            return
        if state.ollama_pull_status == "running":
            return
        state.ollama_pull_status = "running"
        state.ollama_notice = ""
        await bus.publish("app-settings")

        try:
            await asyncio.to_thread(ollama.pull, model_name)
        except Exception as exc:  # noqa: BLE001 - mapped to a friendly message, matching ModelPullWorkerThread's own 3-way mapping; no secret here to redact (a model name is not a credential)
            text = str(exc).lower()
            if "not found" in text:
                message = f"Model '{model_name}' was not found on the Ollama hub."
            elif "connection refused" in text:
                message = "Could not connect to Ollama. Is Ollama running?"
            else:
                message = f"An unexpected error occurred: {exc}"
            state.ollama_pull_status = "error"
            state.ollama_notice = message
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            api_provider.invalidate_ollama_capability_cache(model_name)
            api_provider.set_current_ollama_model(model_name)

        try:
            # Belt-and-suspenders for the same reentrancy-gate hazard fixed
            # in scan_ollama_system's own _persist call above - unlike that
            # one, neither invalidate_ollama_capability_cache nor
            # set_current_model do disk I/O today (both are in-memory-only
            # global mutations), so this has no realistic failure mode right
            # now, but a stranded "running" gate is bad enough - and cheap
            # enough to guard against - that this doesn't wait for one of
            # them to grow a fallible path first.
            await asyncio.to_thread(run_locked, _persist)
        except Exception as exc:  # noqa: BLE001 - no secret here to redact (a model name is not a credential)
            state.ollama_pull_status = "error"
            state.ollama_notice = f"An unexpected error occurred: {exc}"
            await bus.publish("app-settings")
            return
        state.ollama_pull_status = "done"
        state.ollama_notice = ""
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "setOllamaReasoningLevel", set_ollama_reasoning_level)
    bus.register_intent("app-settings", "setOllamaModelAssignment", set_ollama_model_assignment)
    bus.register_intent("app-settings", "scanOllamaSystem", scan_ollama_system)
    bus.register_intent("app-settings", "pullOllamaModel", pull_ollama_model)
    bus.register_intent("app-settings", "pickOllamaScanFolder", pick_ollama_scan_folder)
