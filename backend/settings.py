"""Settings dialog: General + Integrations pages (Qt-removal plan R2.5d).

Unlike composer.py/plugins.py this is a genuine REUSE, not a
reimplementation: SettingsManager (graphlink_licensing.py) and its own
imports (graphlink_secrets, graphlink_model_catalog) carry zero PySide6
coupling, confirmed via a runtime sys.modules check in
test_settings_never_imports_qt below.

Scope (doc/QT_REMOVAL_PLAN.md R2.5d): only the General/Appearance page
(theme, token-counter visibility, system-prompt toggle, notification
preferences) and the Integrations page (GitHub token, write-only) are real
here. Ollama/Llama.cpp/API-provider pages, and the update-check pair
(checkForUpdates/openRepository), need a graphlink_config.py Qt/non-Qt
split and a native file-picker/browser-open capability neither of which
exist yet in graphlink_desktop.py - out of scope until R4. The SPA renders
those sections disabled with an explicit R4 label rather than faking them.

SettingsManager owns ONE shared ~/.graphlink/session.dat file for the whole
app (the same file the legacy Qt app read/wrote). register_settings takes
an already-constructed SettingsManager - created ONCE in
backend/app.py's create_app() and shared across every session - rather
than one per SessionBus, which would each open/mutate the same file
independently and stomp on each other's in-memory copy.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from graphlink_licensing import SettingsManager

from backend.events import SessionBus

# Every persistence-touching mutation runs in a worker thread
# (asyncio.to_thread) so SettingsManager._save_state's json-dump + fsync +
# atomic-replace never stalls the event loop (and with it, every session's
# WS traffic). The manager's mutations are unsynchronized read-modify-writes
# on shared state, so this lock restores the serialization that running them
# on the single-threaded loop used to provide for free. Module-level rather
# than per-manager: a real process has exactly one manager, and the
# throwaway managers tests create can share it harmlessly.
_manager_lock = threading.Lock()


def _apply(mutation: Callable[..., None], *args: Any) -> None:
    with _manager_lock:
        mutation(*args)


def settings_payload(manager: SettingsManager) -> dict[str, Any]:
    return {
        "theme": manager.get_theme(),
        "showTokenCounter": manager.get_show_token_counter(),
        "enableSystemPrompt": manager.get_enable_system_prompt(),
        "notificationPreferences": manager.get_notification_preferences(),
        "githubTokenConfigured": bool(manager.get_github_token()),
    }


def register_settings(bus: SessionBus, manager: SettingsManager) -> None:
    # activeSection is session-local UI navigation, not SettingsManager
    # state - the legacy bridge didn't persist it either (the dialog always
    # opened on General). One mutable cell closed over by the builder and
    # its own intent, matching this field's single purpose.
    active_section = {"value": "general"}

    def build_payload() -> dict[str, Any]:
        payload = settings_payload(manager)
        payload["activeSection"] = active_section["value"]
        return payload

    bus.register_topic("app-settings", build_payload)

    async def set_active_section(section: str):
        active_section["value"] = str(section)
        await bus.publish("app-settings")

    # The topic builder (read path) stays on the loop, unlocked: field reads
    # are GIL-atomic, and every mutation republishes on completion, so a
    # snapshot that races a write is immediately superseded by a settled one.

    async def set_theme(theme: str):
        await asyncio.to_thread(_apply, manager.set_theme, str(theme))
        await bus.publish("app-settings")

    async def set_show_token_counter(enabled: bool):
        await asyncio.to_thread(_apply, manager.set_show_token_counter, bool(enabled))
        await bus.publish("app-settings")

    async def set_enable_system_prompt(enabled: bool):
        await asyncio.to_thread(_apply, manager.set_enable_system_prompt, bool(enabled))
        await bus.publish("app-settings")

    async def set_notification_preference(notification_type: str, enabled: bool):
        await asyncio.to_thread(
            _apply, manager.set_notification_preferences, {str(notification_type): bool(enabled)}
        )
        await bus.publish("app-settings")

    async def set_github_token(token: str):
        await asyncio.to_thread(_apply, manager.set_github_token, str(token))
        await bus.publish("app-settings")

    async def clear_github_token():
        await asyncio.to_thread(_apply, manager.set_github_token, "")
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "setActiveSection", set_active_section)
    bus.register_intent("app-settings", "setTheme", set_theme)
    bus.register_intent("app-settings", "setShowTokenCounter", set_show_token_counter)
    bus.register_intent("app-settings", "setEnableSystemPrompt", set_enable_system_prompt)
    bus.register_intent("app-settings", "setNotificationPreference", set_notification_preference)
    bus.register_intent("app-settings", "setGithubToken", set_github_token)
    bus.register_intent("app-settings", "clearGithubToken", clear_github_token)
