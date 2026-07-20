"""Package for app-level dialogs such as the chat library.

The legacy settings dialog stack (SettingsDialog + its five page widgets)
was deleted in Phase 3 increment 10 - settings are now the React island in
graphlink_settings_web.py. The pre-deletion code is recoverable at the
`legacy-settings-final` git tag.

AboutDialog/HelpDialog (formerly graphlink_system_dialogs.py) were deleted in
Phase 4 increment 5 - both surfaces are now WebIslandHost-based islands
(graphlink_about_web.py/graphlink_help_web.py). The pre-deletion code is
recoverable at the `phase4-shims-final` git tag.
"""

from graphlink_ui_dialogs.graphlink_library_dialog import ChatLibraryDialog

__all__ = [
    "ChatLibraryDialog",
]
