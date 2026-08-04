"""ADR-002 stage 2.7: Settings dialog - General/Appearance page, plus the
Integrations page's GitHub token (write-only).

Relocated VERBATIM from backend/settings.py's former register_settings
(closures at its former lines 476-504) - pure code motion, no behavior
change. The smallest and lowest-risk of the 4 page-area splits: every
intent here is a straight persist-then-republish, with no cross-page
state, native-dialog, or subprocess/thread-race concerns.
"""

from __future__ import annotations

import asyncio

from backend.api._settings_shared import SettingsSessionState, run_locked
from backend.events import SessionBus
from graphlink_settings_store import SettingsManager


def register_settings_general_intents(
    bus: SessionBus, manager: SettingsManager, state: SettingsSessionState
) -> None:
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

    bus.register_intent("app-settings", "setActiveSection", set_active_section)
    bus.register_intent("app-settings", "setShowTokenCounter", set_show_token_counter)
    bus.register_intent("app-settings", "setEnableSystemPrompt", set_enable_system_prompt)
    bus.register_intent("app-settings", "setNotificationPreference", set_notification_preference)
    bus.register_intent("app-settings", "setGithubToken", set_github_token)
    bus.register_intent("app-settings", "clearGithubToken", clear_github_token)
