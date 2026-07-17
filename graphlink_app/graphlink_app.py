import sys
from PySide6.QtWidgets import QApplication

from graphlink_window import ChatWindow
from graphlink_widgets import SplashScreen
import graphlink_licensing
from graphlink_config import apply_theme, set_current_model, sync_ollama_task_models
from graphlink_logging import configure_logging
from graphlink_crash import (
    install_crash_handlers,
    mark_running,
    previous_run_crashed,
    uninstall_crash_handlers,
)
from graphlink_version import APP_VERSION

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
