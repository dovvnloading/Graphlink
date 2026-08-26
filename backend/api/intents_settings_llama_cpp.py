"""ADR-002 stage 2.7: Settings dialog - Llama.cpp page (reasoning mode,
runtime tunables, GGUF model scan/browse, chat/naming model paths).

Relocated VERBATIM from backend/settings.py's former register_settings
(closures at its former lines 976-1231) - pure code motion, no behavior
change. The largest of the 4 page-area splits (Llama.cpp has no analog of
the Ollama page's separate "assignment" concept, but needs its own native
file-picker paths and a 5-way legacy-parity error split
save_llama_cpp_settings alone accounts for), so importers should not be
surprised this module runs closer to the ADR's 300-line cap than its 3
siblings.
"""

from __future__ import annotations

import asyncio
import os

import api_provider
import graphlink_task_config as config

from backend import native_dialogs
from backend.api._settings_shared import (
    REASONING_LEVELS,
    SettingsSessionState,
    apply_llama_cpp_reasoning_level,
    locked_llama_cpp_settings,
    pick_scan_folder_and_run,
    republish_composer_reasoning,
    run_locked,
)
from backend.events import SessionBus
from graphlink_settings_store import SettingsManager


def register_settings_llama_cpp_intents(
    bus: SessionBus, manager: SettingsManager, state: SettingsSessionState
) -> None:
    async def set_llama_cpp_reasoning_level(level: str):
        # Same shape as set_ollama_reasoning_level's own live re-apply: gated
        # on is_local_llama_cpp_mode(), checked and applied inside the SAME
        # to_thread hop so a concurrent provider-mode switch can't be
        # clobbered back to Llama.cpp. Unlike Ollama's version, this
        # genuinely CAN fail: initialize_local_provider's Llama.cpp branch
        # re-validates chat_model_path (must still be a real, existing .gguf
        # file) every time it runs - if that file was deleted/moved from
        # under an already-active session since Llama.cpp was last
        # activated, this raises. The level is already persisted regardless,
        # so a failure here only means it takes effect on the next mode
        # switch/restart instead of immediately - not a lost setting.
        #
        # R7.5d: this apply/re-apply sequence is shared with
        # backend/composer.py's own setReasoningLevel intent - see
        # apply_llama_cpp_reasoning_level's own docstring
        # (backend/api/_settings_shared.py).
        level = str(level)
        if level not in REASONING_LEVELS:
            return
        failure = await apply_llama_cpp_reasoning_level(manager, level)
        if failure is not None:
            state.llama_notice = failure
        # NOTE: on success, do NOT clear llama_notice to "" - this preserves
        # the exact pre-existing behavior (today's code never clears it on
        # success either); do not "improve" this as a drive-by fix in this
        # increment.
        await bus.publish("app-settings")
        await republish_composer_reasoning(bus)

    async def set_llama_cpp_chat_format(chat_format: str):
        await asyncio.to_thread(run_locked, manager.set_llama_cpp_chat_format, str(chat_format))
        await bus.publish("app-settings")

    def _set_llama_cpp_runtime_field(field: str, value: int) -> None:
        # Read-modify-write entirely inside this one run_locked closure -
        # SettingsManager.set_llama_cpp_runtime requires all 4 kwargs every
        # call (no partial-update variant), so the "current" read must
        # happen in the SAME locked critical section as the write, not
        # before scheduling it - exactly the class of race the R7.4a
        # save_api_configuration bug and R7.4b's model-assignment test both
        # already covered for other read-modify-writes in this file.
        current = manager.get_llama_cpp_settings()
        current[field] = value
        manager.set_llama_cpp_runtime(
            n_ctx=current["n_ctx"],
            n_gpu_layers=current["n_gpu_layers"],
            n_threads=current["n_threads"],
            chat_format=current["chat_format"],
        )

    async def set_llama_cpp_n_ctx(n_ctx: int):
        try:
            n_ctx = int(n_ctx)
        except (TypeError, ValueError):
            return
        await asyncio.to_thread(run_locked, _set_llama_cpp_runtime_field, "n_ctx", n_ctx)
        await bus.publish("app-settings")

    async def set_llama_cpp_n_gpu_layers(n_gpu_layers: int):
        try:
            n_gpu_layers = int(n_gpu_layers)
        except (TypeError, ValueError):
            return
        await asyncio.to_thread(run_locked, _set_llama_cpp_runtime_field, "n_gpu_layers", n_gpu_layers)
        await bus.publish("app-settings")

    async def set_llama_cpp_n_threads(n_threads: int):
        try:
            n_threads = int(n_threads)
        except (TypeError, ValueError):
            return
        await asyncio.to_thread(run_locked, _set_llama_cpp_runtime_field, "n_threads", n_threads)
        await bus.publish("app-settings")

    def _initial_gguf_directory(staged_path: str) -> str:
        # Matches legacy's own _pick_gguf_file: prefer the staged path's
        # own directory (so re-browsing starts where the current selection
        # already lives), else a saved scan path, else home - never leaves
        # it to whatever the OS defaults to.
        if staged_path:
            directory = os.path.dirname(staged_path)
            if directory:
                return directory
        return manager.get_llama_cpp_model_scan_path() or os.path.expanduser("~")

    async def pick_llama_cpp_chat_model_file():
        # Stages only - matches legacy's pickLlamaCppChatModelFile(), which
        # never persists until the user clicks Save. A cancelled dialog
        # (path is None) is a quiet no-op.
        directory = _initial_gguf_directory(state.llama_staged_chat_path)
        path = await native_dialogs.pick_file(
            file_types=("GGUF files (*.gguf)", "All Files (*.*)"), directory=directory
        )
        if path:
            state.llama_staged_chat_path = path
            await bus.publish("app-settings")

    async def pick_llama_cpp_title_model_file():
        # Legacy's own initial-dir fallback order for the TITLE picker
        # specifically: the staged title path, else the staged CHAT path
        # (not the title one) - matches _pick_gguf_file's caller passing
        # `self._llama_title_model_path or self._llama_chat_model_path`.
        directory = _initial_gguf_directory(
            state.llama_staged_title_path or state.llama_staged_chat_path
        )
        path = await native_dialogs.pick_file(
            file_types=("GGUF files (*.gguf)", "All Files (*.*)"), directory=directory
        )
        if path:
            state.llama_staged_title_path = path
            await bus.publish("app-settings")

    async def set_llama_cpp_chat_model_path(path: str):
        # The non-native counterpart (selecting from the scanned-models
        # dropdown) - also stages only, matching pick_llama_cpp_chat_model_file.
        state.llama_staged_chat_path = str(path).strip()
        await bus.publish("app-settings")

    async def set_llama_cpp_title_model_path(path: str):
        state.llama_staged_title_path = str(path).strip()
        await bus.publish("app-settings")

    async def _run_llama_cpp_scan(scan_path: str | None) -> None:
        state.llama_scan_status = "running"
        state.llama_notice = ""
        await bus.publish("app-settings")

        try:
            # Unlike scan_local_ollama_models, this DOES raise for a real,
            # reachable failure: an explicit scan_path that doesn't exist or
            # isn't a directory (api_provider.py's own scan_local_llama_cpp_models).
            results = await asyncio.to_thread(api_provider.scan_local_llama_cpp_models, scan_path)
        except Exception as exc:  # noqa: BLE001 - no secret in this path (a local folder path, not a credential)
            state.llama_scan_status = "error"
            state.llama_notice = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            manager.set_llama_cpp_model_scan_cache(
                results.get("models", []),
                results.get("scan_mode", ""),
                results.get("scan_path", ""),
                results.get("locations", []),
            )

        try:
            # Same reentrancy-gate-recovery fix as scan_ollama_system's own
            # persist step - a disk-write failure here must not strand this
            # scan status at "running" forever.
            await asyncio.to_thread(run_locked, _persist)
        except Exception as exc:  # noqa: BLE001 - no secret in this path
            state.llama_scan_status = "error"
            state.llama_notice = f"Scan failed: {exc}"
            await bus.publish("app-settings")
            return

        state.llama_scan_status = "done"
        if results.get("truncated"):
            # A deliberate small improvement over legacy (never surfaced
            # this): the scan collector is bounded (50k directories / 30s -
            # see api_provider.py's _GGUF_SCAN_MAX_DIRECTORIES/_MAX_SECONDS)
            # specifically because the default system-wide roots include
            # the user's whole Downloads/Documents/Desktop trees, which can
            # be huge. Silently reporting an incomplete scan as complete
            # would be misleading; this doesn't change any persisted field.
            state.llama_notice = "Scan stopped early (too many folders or took too long) - results may be incomplete."
        await bus.publish("app-settings")

    async def scan_llama_cpp_system():
        if state.llama_scan_status == "running":
            return
        await _run_llama_cpp_scan(None)

    async def pick_llama_cpp_scan_folder():
        # R8b: same reentrancy-gate hazard fixed for pick_ollama_scan_folder
        # (intents_settings_ollama.py) - both now share
        # pick_scan_folder_and_run (backend/api/_settings_shared.py).
        await pick_scan_folder_and_run(
            bus, state,
            status_field="llama_scan_status", notice_field="llama_notice",
            saved_scan_path=manager.get_llama_cpp_model_scan_path(),
            run_scan=_run_llama_cpp_scan,
        )

    async def save_llama_cpp_settings():
        # Sequencing matches legacy's saveLlamaCppSettings() exactly:
        # (1) validate the staged paths locally - chat is required, title
        # is optional but validated if non-empty; (2) if Llama.cpp is the
        # CURRENTLY LIVE provider, re-initialize it with the new settings,
        # aborting without persisting anything on failure (a real, useful
        # abort: this is what catches "that .gguf file doesn't actually
        # exist" before it's saved); (3) only then persist.
        chat_path = state.llama_staged_chat_path.strip()
        title_path = state.llama_staged_title_path.strip()

        # Legacy-parity fix: restores the 5 distinct legacy error messages
        # (graphlink_settings_bridge.py's saveLlamaCppSettings) instead of 2
        # generic ones - a user gets the ACTUAL problem (empty vs. not-found
        # vs. wrong-extension), not just "must be a real .gguf file" for all
        # three.
        if not chat_path:
            state.llama_notice = "Chat Model File cannot be empty."
            await bus.publish("app-settings")
            return
        if not os.path.isfile(chat_path):
            state.llama_notice = f"Chat model file was not found: {chat_path}"
            await bus.publish("app-settings")
            return
        if not chat_path.lower().endswith(".gguf"):
            state.llama_notice = "Chat Model File must point to a .gguf file."
            await bus.publish("app-settings")
            return
        if title_path:
            if not os.path.isfile(title_path):
                state.llama_notice = f"Chat naming model file was not found: {title_path}"
                await bus.publish("app-settings")
                return
            if not title_path.lower().endswith(".gguf"):
                state.llama_notice = "Chat Naming File must point to a .gguf file."
                await bus.publish("app-settings")
                return

        def _maybe_reapply_live() -> None:
            # Checked and applied inside the SAME to_thread hop - the same
            # race-preemption shape as set_llama_cpp_reasoning_level's own
            # live re-apply above.
            if api_provider.is_local_llama_cpp_mode():
                settings = locked_llama_cpp_settings(manager)
                settings["chat_model_path"] = chat_path
                settings["title_model_path"] = title_path
                api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, settings)

        try:
            await asyncio.to_thread(_maybe_reapply_live)
        except Exception as exc:  # noqa: BLE001 - no secret in this path (local file paths, not credentials)
            state.llama_notice = f"Invalid Llama.cpp configuration: {exc}"
            await bus.publish("app-settings")
            return

        def _persist() -> None:
            manager.set_llama_cpp_chat_model_path(chat_path)
            manager.set_llama_cpp_title_model_path(title_path)

        await asyncio.to_thread(run_locked, _persist)
        state.llama_notice = ""
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "setLlamaCppReasoningLevel", set_llama_cpp_reasoning_level)
    bus.register_intent("app-settings", "setLlamaCppChatFormat", set_llama_cpp_chat_format)
    bus.register_intent("app-settings", "setLlamaCppNCtx", set_llama_cpp_n_ctx)
    bus.register_intent("app-settings", "setLlamaCppNGpuLayers", set_llama_cpp_n_gpu_layers)
    bus.register_intent("app-settings", "setLlamaCppNThreads", set_llama_cpp_n_threads)
    bus.register_intent("app-settings", "pickLlamaCppChatModelFile", pick_llama_cpp_chat_model_file)
    bus.register_intent("app-settings", "pickLlamaCppTitleModelFile", pick_llama_cpp_title_model_file)
    bus.register_intent("app-settings", "setLlamaCppChatModelPath", set_llama_cpp_chat_model_path)
    bus.register_intent("app-settings", "setLlamaCppTitleModelPath", set_llama_cpp_title_model_path)
    bus.register_intent("app-settings", "scanLlamaCppSystem", scan_llama_cpp_system)
    bus.register_intent("app-settings", "pickLlamaCppScanFolder", pick_llama_cpp_scan_folder)
    bus.register_intent("app-settings", "saveLlamaCppSettings", save_llama_cpp_settings)
