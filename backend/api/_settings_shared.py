"""ADR-002 stage 2.7: the cross-cutting pieces every Settings-dialog
intents_settings_*.py module needs.

Mirrors backend/api/_shared.py's own reason for existing (see its
docstring): register_settings' former ~901-line body split into one
register_settings_*_intents(bus, manager, ..., state) function per
Settings page (backend/api/intents_settings_general.py,
intents_settings_api_provider.py, intents_settings_ollama.py,
intents_settings_llama_cpp.py). backend/settings.py itself still owns
register_settings (now a thin orchestrator), settings_payload, and the
wire-payload builder - but it CANNOT be the home for anything the split
modules need, or backend/settings importing them (to call them from
register_settings) while they import IT would be a circular import. This
leaf module has no dependency on backend.settings or backend.api.* in
either direction, breaking that cycle the same way _shared.py does for
the canvas split.

apply_ollama_reasoning_level/apply_ollama_chat_model/
apply_llama_cpp_reasoning_level/apply_anthropic_reasoning_level/
apply_gemini_reasoning_level/apply_openai_reasoning_level relocate here
for the same reason even though they predate this split and are not
per-page intents themselves: backend/composer.py's own setReasoningLevel/
setModel intents import all six directly (`from backend.settings import
...`), and the new intents_settings_ollama.py/intents_settings_llama_cpp.py
modules need to call apply_ollama_reasoning_level/
apply_llama_cpp_reasoning_level from their own moved intents - so they too
would create the same cycle if left in backend/settings.py.
backend/settings.py re-exports all six verbatim so composer.py's existing
import keeps working unchanged.

run_locked/locked_llama_cpp_settings/republish_composer_reasoning/
redact_secret were private (leading-underscore) helpers before this split,
each now crossing a real module boundary for the first time - renamed
public per the same "private cross-imports become named public constants
in the module that owns the concept" rule ADR-002's own renames-and-
deletions section already applies elsewhere (graphlink_licensing.py's own
rename, the _CODE_EXEC_RUN_CLAIM_PLACEHOLDER-style constants). Every
other originally-private helper in backend/settings.py
(_api_model_catalog_for_wire, _flatten_ollama_assignment,
_ollama_model_assignments_for_wire, _ollama_scan_summary,
_llama_cpp_scan_summary) is used ONLY by the wire-payload builder that
stays in backend/settings.py, so those stay right where they are,
unrenamed.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import api_provider
import graphlink_task_config as config
from graphlink_settings_store import SettingsManager

from backend.events import SessionBus

# The six per-task model-assignment slots the API-provider page exposes,
# in the same order the legacy widget built its combo boxes - not load-
# bearing (payload is a dict), but keeps save_api_configuration's required-
# task walk in a stable, readable order.
API_TASK_KEYS = (
    config.TASK_TITLE,
    config.TASK_CHAT,
    config.TASK_CHART,
    config.TASK_IMAGE_GEN,
    config.TASK_WEB_VALIDATE,
    config.TASK_WEB_SUMMARIZE,
)

# The three providers the page can actually display. Used to bound the
# per-provider catalog-state dict: without it, that dict is keyed by a
# raw client-supplied string and grows without limit.
KNOWN_API_PROVIDERS = (
    config.API_PROVIDER_OPENAI,
    config.API_PROVIDER_ANTHROPIC,
    config.API_PROVIDER_GEMINI,
)

# Ollama's five per-task model-assignment slots - task_image_gen is
# deliberately absent (Ollama has no image-generation path, matching
# legacy: OLLAMA_TASKS in graphlink_settings_bridge.py has the same 5).
OLLAMA_TASK_KEYS = (
    config.TASK_CHAT,
    config.TASK_TITLE,
    config.TASK_CHART,
    config.TASK_WEB_VALIDATE,
    config.TASK_WEB_SUMMARIZE,
)
# R8a: ONE shared vocabulary for all 5 providers' reasoning levels -
# deliberately consolidated from what used to be per-provider "distinct
# constant, not reused" tuples (Ollama/Llama.cpp each had their own
# identical 2-value set). That separation existed because the two
# providers' valid-mode sets only happened to match, with no shared
# meaning behind the values - here the whole point is the opposite: one
# real, shared vocabulary every provider's mapping function in
# api_provider.py translates from, so keeping five copies of the same
# tuple would be the ad-hoc-duplication problem, not a safeguard against one.
REASONING_LEVELS = api_provider.REASONING_LEVELS

# Every persistence-touching mutation runs in a worker thread
# (asyncio.to_thread) so SettingsManager._save_state's json-dump + fsync +
# atomic-replace never stalls the event loop (and with it, every session's
# WS traffic). The manager's mutations are unsynchronized read-modify-writes
# on shared state, so this lock restores the serialization that running them
# on the single-threaded loop used to provide for free. Module-level rather
# than per-manager: a real process has exactly one manager, and the
# throwaway managers tests create can share it harmlessly.
_manager_lock = threading.Lock()


def run_locked(mutation: Callable[..., None], *args: Any) -> None:
    with _manager_lock:
        mutation(*args)


def locked_llama_cpp_settings(manager: SettingsManager) -> dict[str, Any]:
    # Adversarial-review finding: SettingsManager.get_llama_cpp_settings()
    # does 7 separate unsynchronized dict reads - calling it outside
    # _manager_lock let a concurrent setLlamaCppNCtx/NGpuLayers/NThreads/
    # ChatFormat call (each correctly run_locked-guarded) interleave mid-
    # read, so a live-reapply could combine e.g. a brand-new n_ctx with a
    # stale chat_format. Doesn't corrupt the persisted file (this is a
    # read, never written back), but it's exactly the class of race this
    # file's own comments already claim to guard against elsewhere.
    with _manager_lock:
        return manager.get_llama_cpp_settings()


async def republish_composer_reasoning(bus: SessionBus) -> None:
    """Push a fresh composer snapshot after a reasoning-mode change here.

    The composer's own Reasoning control displays the SAME persisted value
    these intents edit (backend/composer.py derives it rather than keeping a
    private copy, matching legacy). It therefore has to be told to rebuild,
    or the Settings dialog and the composer disagree until some unrelated
    composer event happens to republish. Guarded because a focused
    test_settings.py bus registers no composer topic.
    """
    if bus.has_topic("app-composer"):
        await bus.publish("app-composer")


async def apply_ollama_reasoning_level(manager: SettingsManager, level: str) -> None:
    """Persist `level` and, if Ollama is still the live provider, re-apply it to
    api_provider's module state so the very next chat()/chat_stream() call
    picks it up. `level` MUST already be one of api_provider.REASONING_LEVELS -
    this function does not re-validate it (every caller's own normalization
    is total, so there is no path that could hand it anything else).
    Checked-and-applied inside the SAME asyncio.to_thread hop as a
    deliberate race-preemption: see set_ollama_reasoning_level's inline
    comment for why. Shared by backend/api/intents_settings_ollama.py's own
    setOllamaReasoningLevel intent (Settings-dialog Ollama page) and
    backend/composer.py's setReasoningLevel intent (composer's own
    quick-access popover) so both surfaces apply a change identically."""
    await asyncio.to_thread(run_locked, manager.set_ollama_reasoning_level, level)

    def _reapply_if_ollama_is_still_the_live_provider() -> None:
        if api_provider.is_local_ollama_mode():
            api_provider.initialize_local_provider(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_level": level})

    await asyncio.to_thread(_reapply_if_ollama_is_still_the_live_provider)


async def apply_ollama_chat_model(manager: SettingsManager, model_id: str) -> None:
    """Assign the Ollama chat-task model, race-safely (R8a).

    Extracted from register_settings' own set_ollama_model_assignment intent
    so the composer's model picker and the Settings > Ollama page write through
    ONE implementation instead of two that can drift. Same precedent as
    apply_ollama_reasoning_level above.

    The read-modify-write stays inside a single run_locked closure, not
    split across an await: that split is the R7.4a race where a concurrent
    assignment change for a different task gets silently reverted by this
    call's stale pre-read of the whole assignments dict.
    """
    chosen = str(model_id or "").strip()
    if not chosen:
        return
    assignment = {"mode": "explicit", "model_id": chosen}

    def _persist() -> None:
        assignments = manager.get_ollama_model_assignments()
        assignments[config.TASK_CHAT] = assignment
        manager.set_ollama_model_assignments(assignments)
        # ADR-006 stage 6.5 (H6): locked writer wrappers - see
        # api_provider.sync_ollama_models.
        api_provider.sync_ollama_models(manager)
        api_provider.set_current_ollama_model(chosen)

    await asyncio.to_thread(run_locked, _persist)


async def apply_llama_cpp_reasoning_level(manager: SettingsManager, level: str) -> str | None:
    """Same contract as apply_ollama_reasoning_level, for Llama.cpp. Returns a
    human-readable failure message if the live re-apply fails (the level is
    ALREADY persisted regardless - only the live effect is delayed to the
    next mode switch/restart), or None on success / when Llama.cpp is not the
    active provider. Returns rather than writes a notice directly:
    backend/api/intents_settings_llama_cpp.py's own setLlamaCppReasoningLevel
    intent writes the message into its page-local llama_notice cell;
    backend/composer.py's intent has no such cell and surfaces it through
    the shared NotificationState banner instead - that decision belongs to
    each caller, not to this function."""
    await asyncio.to_thread(run_locked, manager.set_llama_cpp_reasoning_level, level)

    def _reapply_if_llama_cpp_is_still_the_live_provider() -> None:
        if api_provider.is_local_llama_cpp_mode():
            settings = locked_llama_cpp_settings(manager)
            settings["reasoning_level"] = level
            api_provider.initialize_local_provider(config.LOCAL_PROVIDER_LLAMACPP, settings)

    try:
        await asyncio.to_thread(_reapply_if_llama_cpp_is_still_the_live_provider)
    except Exception as exc:  # noqa: BLE001 - no secret in this path (a local file path, not a credential)
        return f"Reasoning level saved, but could not be applied to the live model: {exc}"
    return None


# R8a: the three cloud providers' reasoning levels are each an INDEPENDENT
# api_provider global (ANTHROPIC_REASONING_LEVEL/GEMINI_REASONING_LEVEL/
# OPENAI_REASONING_LEVEL) read only at the moment of an actual API call for
# that provider - unlike Ollama/Llama.cpp above, there is no "is this still
# the live provider" gate needed: setting the value is always safe
# regardless of which provider is currently active, so these three are
# simpler than their local-provider counterparts, not an oversight.


async def apply_anthropic_reasoning_level(manager: SettingsManager, level: str) -> None:
    await asyncio.to_thread(run_locked, manager.set_anthropic_reasoning_level, level)
    await asyncio.to_thread(api_provider.set_anthropic_reasoning_level, level)


async def apply_gemini_reasoning_level(manager: SettingsManager, level: str) -> None:
    await asyncio.to_thread(run_locked, manager.set_gemini_reasoning_level, level)
    await asyncio.to_thread(api_provider.set_gemini_reasoning_level, level)


async def apply_openai_reasoning_level(manager: SettingsManager, level: str) -> None:
    await asyncio.to_thread(run_locked, manager.set_openai_reasoning_level, level)
    await asyncio.to_thread(api_provider.set_openai_reasoning_level, level)


def redact_secret(text: str, secret: Any) -> str:
    # Post-review fix: some HTTP client libraries embed request parameters
    # (including a rejected API key) directly in exception text - the
    # legacy bridge this page replaces redacted for exactly this reason
    # (graphlink_settings_bridge.py: str(exc).replace(api_key, '***')).
    # Applied before any exception text reaches a WS-broadcast message, so
    # the write-only-key guarantee holds on the failure path too, not just
    # the happy path.
    #
    # Second-pass fix: the isinstance check is load-bearing, not defensive
    # noise. Intent arguments arrive as raw JSON off the wire with no
    # coercion anywhere in the dispatch path, and both call sites are
    # inside `except` blocks - where a TypeError from str.replace would
    # NOT be caught by its own `try` and would escape the handler, leaving
    # the catalog pinned at "loading" (which disables the Load button) for
    # the life of the process.
    if not isinstance(secret, str) or not secret:
        return text
    return text.replace(secret, "***")


@dataclass
class SettingsSessionState:
    """Per-session mutable state register_settings' own intents read and
    write, shared across the 4 backend/api/intents_settings_*.py modules
    stage 2.7 split its former ~901-line body into. Replaces the
    {"value": ...} single-cell-dict pattern register_settings used
    before the split (needed there only to work around Python closures
    being unable to rebind an outer local without `nonlocal`) - a shared
    object passed explicitly as a parameter has no such restriction, so
    plain attributes are the more direct representation of the exact same
    per-session state, not a behavior change. Field defaults match every
    cell's own former default; register_settings still seeds
    viewing_api_provider/llama_staged_*_path from the manager itself
    (session-dependent, can't be a class-level default).
    """

    active_section: str = "general"
    viewing_api_provider: str = ""
    api_catalog_state: dict[str, dict[str, str]] = field(default_factory=dict)
    ollama_scan_status: str = "idle"
    ollama_pull_status: str = "idle"
    ollama_notice: str = ""
    llama_scan_status: str = "idle"
    llama_notice: str = ""
    llama_staged_chat_path: str = ""
    llama_staged_title_path: str = ""
