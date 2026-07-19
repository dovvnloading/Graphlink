import logging
import sys
from PySide6.QtWidgets import QApplication, QMessageBox

from graphlink_window import ChatWindow
from graphlink_widgets import SplashScreen
import graphlink_licensing
from graphlink_config import apply_theme, set_current_model, sync_ollama_task_models
from graphlink_logging import configure_logging
from graphlink_crash import (
    install_crash_handlers,
    mark_clean_exit,
    mark_running,
    previous_run_crashed,
    uninstall_crash_handlers,
)
from graphlink_frontend_bootstrap import FrontendBootstrapError, ensure_frontend_built
from graphlink_version import APP_VERSION

logger = logging.getLogger(__name__)


def _handle_frontend_bootstrap_error(exc: FrontendBootstrapError) -> None:
    """The failure path for ensure_frontend_built(), split out from main()
    so it's testable without constructing a real QApplication/ChatWindow or
    touching real crash/log files. Never returns - always exits the process."""
    # Caught here rather than left to propagate to the installed excepthook -
    # this is a handled, actionable configuration problem the user has just
    # been shown a dialog for, not an unhandled crash, so it must not be
    # recorded or reported as one. Still logged through the same durable,
    # inspectable file configure_logging() sets up (a windowed app has no
    # visible console for this to land in otherwise), and the running.lock
    # sentinel is cleared explicitly so this controlled exit isn't mistaken
    # for a crash on the next launch.
    logger.error("Frontend bootstrap failed: %s", exc)
    QMessageBox.critical(None, "Graphlink - frontend build failed", str(exc))
    mark_clean_exit()
    sys.exit(1)


def main():
    configure_logging()
    # Install crash capture before anything else can fail - faulthandler/excepthooks need
    # no QApplication, and must be in place before Qt/provider/model init runs.
    install_crash_handlers(version=APP_VERSION)
    crashed_last_time = previous_run_crashed()
    mark_running(version=APP_VERSION)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    # Remove Python/Qt crash callbacks before Qt destroys its Python wrappers.
    app.aboutToQuit.connect(uninstall_crash_handlers)

    # Build web_ui/'s frontend assets if missing or stale before anything below
    # constructs a widget that reads them synchronously (ComposerWebHost, at
    # ChatWindow construction). A no-op in a frozen build or under an explicit
    # GRAPHLINK_FRONTEND_DEV opt-out; loud and fatal on any real failure -
    # never a silent fall back to a stale or missing bundle.
    try:
        ensure_frontend_built()
    except FrontendBootstrapError as exc:
        _handle_frontend_bootstrap_error(exc)

    # Use the new SettingsManager (formerly LicenseManager)
    settings_manager = graphlink_licensing.SettingsManager()
    
    saved_theme = settings_manager.get_theme()
    apply_theme(app, saved_theme)

    saved_model = settings_manager.get_ollama_chat_model()
    set_current_model(saved_model)
    sync_ollama_task_models(settings_manager)

    # Initialize windows without license checks
    main_chat_window = ChatWindow(settings_manager)
    if crashed_last_time:
        main_chat_window.show_previous_crash_notice()
    splash = SplashScreen(main_chat_window)
    splash.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
