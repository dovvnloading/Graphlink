import sys
from PySide6.QtWidgets import QApplication

from graphite_window import ChatWindow
from graphite_widgets import SplashScreen
from graphite_welcome_screen import WelcomeScreen
import graphite_licensing
from graphite_config import apply_theme, set_current_model

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    # Use the new SettingsManager (formerly LicenseManager)
    settings_manager = graphite_licensing.SettingsManager()
    
    saved_theme = settings_manager.get_theme()
    apply_theme(app, saved_theme)

    saved_model = settings_manager.get_ollama_chat_model()
    set_current_model(saved_model)

    # Initialize windows without license checks
    main_chat_window = ChatWindow(settings_manager)
    welcome_screen = WelcomeScreen(settings_manager, main_chat_window)
    
    show_welcome = settings_manager.get_show_welcome_screen()

    splash = SplashScreen(main_chat_window, welcome_screen, show_welcome)
    splash.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()