"""ADR-002 stage 2.7: Settings dialog - General/Appearance page, plus the
Integrations page's GitHub token (write-only).

Relocated VERBATIM from backend/settings.py's former register_settings
(closures at its former lines 476-504) - pure code motion, no behavior
change. The smallest and lowest-risk of the 4 page-area splits: every
intent here is a straight persist-then-republish, with no cross-page
state, native-dialog, or subprocess/thread-race concerns.

ADR-006 stage 6.5 adds setProviderMode - the first runtime path back to
Ollama/Llama.cpp from API mode without a restart. It routes through
backend.agents.apply_provider_mode (the exact same three-way dispatch
bootstrap_provider_state uses at startup) and persists the choice.
"""

from __future__ import annotations

import asyncio

import graphlink_task_config as config

from backend.api._settings_shared import SettingsSessionState, run_locked
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.observability import apply_log_level
from graphlink_settings_store import SettingsManager

# ADR-006 stage 6.5: the three persisted provider-mode strings
# apply_provider_mode dispatches on - the closed vocabulary setProviderMode
# validates against before touching any live state.
_KNOWN_PROVIDER_MODES = (
    config.MODE_OLLAMA_LOCAL,
    config.MODE_LLAMACPP_LOCAL,
    config.MODE_API_ENDPOINT,
)


def register_settings_general_intents(
    bus: SessionBus,
    manager: SettingsManager,
    state: SettingsSessionState,
    notifications: NotificationState | None = None,
) -> None:
    # ADR-006 stage 6.5: notifications is optional (trailing, defaulted) so
    # the pre-6.5 tests that call register_settings_general_intents(bus,
    # manager, state) keep working unchanged - backend/settings.py's real
    # call site always passes one. This registrar receives no per-session
    # ProviderRuntime on purpose: apply_provider_mode acts on api_provider's
    # module-level functions, i.e. the DEFAULT session's runtime - correct
    # while the shipped app has exactly one session; per-session settings
    # routing is deferred to 6.5b/ADR-012.
    async def set_active_section(section: str):
        state.active_section = str(section)
        await bus.publish("app-settings")

    # The topic builder (read path) stays on the loop, unlocked: field reads
    # are GIL-atomic, and every mutation republishes on completion, so a
    # snapshot that races a write is immediately superseded by a settled one.

    async def set_show_token_counter(enabled: bool):
        await asyncio.to_thread(run_locked, manager.set_show_token_counter, bool(enabled))
        await bus.publish("app-settings")

    async def set_enable_system_prompt(enabled: bool):
        await asyncio.to_thread(run_locked, manager.set_enable_system_prompt, bool(enabled))
        await bus.publish("app-settings")

    async def set_log_level(level: str):
        # ADR-016 stage 16.1: persists AND applies live - unlike the boot-time
        # read in graphlink_desktop.py's main(), a user flipping this in a
        # running app expects the next log line to honor it immediately, not
        # after a restart. apply_log_level no-ops on an unrecognized string
        # (same closed-vocabulary posture as manager.set_log_level itself),
        # so a malformed intent arg is silently ignored rather than raising.
        level = str(level)
        await asyncio.to_thread(run_locked, manager.set_log_level, level)
        apply_log_level(level)
        await bus.publish("app-settings")

    async def set_notification_preference(notification_type: str, enabled: bool):
        await asyncio.to_thread(
            run_locked, manager.set_notification_preferences, {str(notification_type): bool(enabled)}
        )
        await bus.publish("app-settings")

    async def set_github_token(token: str):
        await asyncio.to_thread(run_locked, manager.set_github_token, str(token))
        await bus.publish("app-settings")

    async def clear_github_token():
        await asyncio.to_thread(run_locked, manager.set_github_token, "")
        await bus.publish("app-settings")

    async def set_provider_mode(mode: str):
        # Coerce before use - intent args reach here as raw JSON with no
        # validation anywhere in the dispatch path (same posture as
        # intents_settings_api_provider.py's own coercions).
        mode = str(mode)
        if mode not in _KNOWN_PROVIDER_MODES:
            if notifications is not None:
                notifications.show(f"Unknown provider mode: {mode}", "warning")
                await bus.publish("notification")
            return

        # Imported here, not at module top: backend.agents is the heaviest
        # module in backend/ (it pulls the whole agent/plugin layer), and
        # this intent is the only thing in the settings split that needs it.
        from backend.agents import apply_provider_mode

        try:
            # apply_provider_mode reads the manager and writes api_provider's
            # provider state - run under the same manager lock every other
            # settings mutation uses, off the event loop (initialize_* can
            # block on client construction).
            await asyncio.to_thread(run_locked, apply_provider_mode, mode, manager)
        except Exception as exc:  # noqa: BLE001 - surfaced, matching the neighboring intents' error posture
            if notifications is not None:
                notifications.show(f"Failed to switch provider mode: {exc}", "error")
                await bus.publish("notification")
            return

        # Persist only after the live switch succeeded - a failed switch must
        # not change what the next restart boots into.
        await asyncio.to_thread(run_locked, manager.set_current_mode, mode)
        if notifications is not None:
            notifications.show(f"Provider mode switched to {mode}.", "success")
            await bus.publish("notification")
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "setActiveSection", set_active_section)
    bus.register_intent("app-settings", "setShowTokenCounter", set_show_token_counter)
    bus.register_intent("app-settings", "setEnableSystemPrompt", set_enable_system_prompt)
    bus.register_intent("app-settings", "setLogLevel", set_log_level)
    bus.register_intent("app-settings", "setNotificationPreference", set_notification_preference)
    bus.register_intent("app-settings", "setGithubToken", set_github_token)
    bus.register_intent("app-settings", "clearGithubToken", clear_github_token)
    bus.register_intent("app-settings", "setProviderMode", set_provider_mode)
