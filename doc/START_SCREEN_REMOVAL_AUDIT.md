# Start Screen Removal Audit

## Completed: 2026-07-07

### Scope
Removed the startup Welcome screen flow and all related runtime/state/help/documentation references after the prior startup-path bug investigation.

### What was removed
- Startup routing in `graphlink_app/graphlink_app.py` now launches `SplashScreen` and then the main `ChatWindow` directly.
- `SplashScreen` no longer accepts or displays a welcome screen target.
- Welcome screen persistence setting (`show_welcome_screen`) from `SettingsManager` load/create state and API.
- Settings UI controls for startup welcome toggle from `graphlink_ui_dialogs/graphlink_settings_dialogs.py`.
- Welcome references in Help dialog flow text (`graphlink_ui_dialogs/graphlink_system_dialogs.py`).
- Startup and navigation docs references in `GRAPHLINK_REPO_NAVIGATION.md`.
- Deleted module `graphlink_app/graphlink_welcome_screen.py`.

### Trace of remaining references
A final repository-wide grep for `WelcomeScreen`, `welcome screen`, `show_welcome_screen`, and `Show Welcome Screen` now returns only the historical removal notes in `doc/START_SCREEN_REMOVAL_AUDIT.md` and `GRAPHLINK_REPO_NAVIGATION.md`.

### Side effect notes
- Existing users with legacy `session.dat` keys containing `show_welcome_screen` will keep the key in their local state until next full write; it is intentionally no longer read or written.
- The startup entry point now has no alternate path for a separate welcome launcher.

### Files changed
- `graphlink_app/graphlink_app.py`
- `graphlink_app/graphlink_widgets/splash.py`
- `graphlink_app/graphlink_licensing.py`
- `graphlink_app/graphlink_ui_dialogs/graphlink_settings_dialogs.py`
- `graphlink_app/graphlink_ui_dialogs/graphlink_system_dialogs.py`
- `GRAPHLINK_REPO_NAVIGATION.md`
- `graphlink_app/graphlink_welcome_screen.py` (deleted)
