"""Package for app-level dialogs such as library and system help.

The legacy settings dialog stack (SettingsDialog + its five page widgets)
was deleted in Phase 3 increment 10 - settings are now the React island in
graphlink_settings_web.py. The pre-deletion code is recoverable at the
`legacy-settings-final` git tag.
"""

from graphlink_ui_dialogs.graphlink_library_dialog import ChatLibraryDialog
from graphlink_ui_dialogs.graphlink_system_dialogs import AboutDialog, HelpDialog

__all__ = [
    "ChatLibraryDialog",
    "AboutDialog",
    "HelpDialog",
]
