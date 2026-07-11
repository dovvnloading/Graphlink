import sys
from PySide6.QtWidgets import QApplication

from graphlink_window import ChatWindow
from graphlink_widgets import SplashScreen
import graphlink_licensing
from graphlink_config import apply_theme, set_current_model, sync_ollama_task_models
from graphlink_logging import configure_logging

def main():
    configure_logging()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # Use the new SettingsManager (formerly LicenseManager)
    settings_manager = graphlink_licensing.SettingsManager()
    
    saved_theme = settings_manager.get_theme()
    apply_theme(app, saved_theme)

    saved_model = settings_manager.get_ollama_chat_model()
    set_current_model(saved_model)
    sync_ollama_task_models(settings_manager)

    # Initialize windows without license checks
    main_chat_window = ChatWindow(settings_manager)
    splash = SplashScreen(main_chat_window)
    splash.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
