# This file contains visual constants and stylesheets for the Graphite application.
# It centralizes all QSS (Qt StyleSheet) definitions, color palettes, and theme-related
# data to ensure a consistent look and feel and to make theming easier.

from PySide6.QtGui import QColor

class StyleSheet:
    """A namespace class to hold large QSS string constants for different themes."""

    # Stylesheet for the default dark theme, featuring dark grays and vibrant accent colors.
    DARK_THEME = """
        QMainWindow, QWidget {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #2d2d2d;
            border-bottom: 1px solid #3f3f3f;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: #ffffff;
            font-size: 12px;
            font-weight: bold;
            font-family: 'Segoe UI', sans-serif;
        }
        
        #titleBarButtons QPushButton {
            background-color: transparent;
            border: none;
            width: 34px;
            height: 26px;
            padding: 4px;
            border-radius: 4px;
        }
        
        #titleBarButtons QPushButton:hover {
            background-color: #3f3f3f;
        }
        
        #closeButton:hover {
            background-color: #c42b1c !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: #252526;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: #555555;
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #6a6a6a;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: #252526;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: #555555;
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #6a6a6a;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
            background: none;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: none;
        }

        /* QMenu styling (Context Menus) - Force opaque background */
        QMenu {
            background-color: #2d2d2d;
            border: 1px solid #3f3f3f;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #ffffff;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #2ecc71;
            color: white;
        }
        QMenu::item:disabled {
            color: #777777;
        }
        QMenu::separator {
            height: 1px;
            background-color: #3f3f3f;
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: #252526;
            border-bottom: 1px solid #3f3f3f;
            spacing: 8px;
            padding: 8px;
        }
        
        /* General styling for buttons placed directly in the toolbar */
        QToolBar > QPushButton {
            background-color: transparent;
            color: #ffffff;
            border: 1px solid #3f3f3f;
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        
        QToolBar > QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.08);
            border-color: #5a5a5a;
            color: #ffffff;
        }
        
        QToolBar > QPushButton:pressed {
            background-color: rgba(0, 0, 0, 0.2);
        }
        
        /* Specific hover effects for different button types */
        QToolBar > QPushButton#actionButton:hover {
            border-color: #3498db;
            color: #3498db;
        }
        
        QToolBar > QPushButton#helpButton:hover {
            border-color: #9b59b6;
            color: #9b59b6;
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: #3a3a3a;
            color: #f3f3f3;
            border: 1px solid #4d4d4d;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: #474747;
            border-color: #5a5a5a;
        }

        QPushButton:pressed {
            background-color: #303030;
            border-color: #424242;
        }

        QPushButton:disabled {
            background-color: #2b2b2b;
            border-color: #353535;
            color: #7b7b7b;
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: #2d2d2d;
            border: 1px solid #3f3f3f;
            color: white;
            padding: 5px;
            border-radius: 4px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }

        QComboBox::drop-down {
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 20px;
            border-left-width: 1px;
            border-left-color: #3f3f3f;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }

        QComboBox::down-arrow {
            image: url(C:/Users/Admin/source/repos/graphite_app/assets/down_arrow.png);
            width: 10px;
            height: 10px;
        }

        QComboBox QAbstractItemView {
            background-color: #2d2d2d;
            border: 1px solid #3f3f3f;
            selection-background-color: #2ecc71;
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: #252526;
            color: #d4d4d4;
            border: 1px solid #3f3f3f;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #264f78;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: #2ecc71;
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: #252526;
            color: #d4d4d4;
            border: 1px solid #3f3f3f;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #264f78;
        }
    """

    # Stylesheet for a monochromatic (grayscale) theme for a minimalist look.
    MONOCHROMATIC_THEME = """
        QMainWindow, QWidget {
            background-color: #222222;
            color: #dddddd;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #2a2a2a;
            border-bottom: 1px solid #333333;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel { color: #dddddd; font-size: 12px; font-weight: bold; }
        #titleBarButtons QPushButton { background-color: transparent; border: none; width: 34px; height: 26px; padding: 4px; border-radius: 4px; }
        #titleBarButtons QPushButton:hover { background-color: #444444; }
        #closeButton:hover { background-color: #993333 !important; }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical { background: #2a2a2a; width: 10px; margin: 0px; border-radius: 5px; }
        QScrollBar::handle:vertical { background-color: #555555; min-height: 25px; border-radius: 5px; }
        QScrollBar::handle:vertical:hover { background-color: #6a6a6a; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: none; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

        QScrollBar:horizontal { background: #2a2a2a; height: 10px; margin: 0px; border-radius: 5px; }
        QScrollBar::handle:horizontal { background-color: #555555; min-width: 25px; border-radius: 5px; }
        QScrollBar::handle:horizontal:hover { background-color: #6a6a6a; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: none; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

        /* QMenu styling (Context Menus) */
        QMenu {
            background-color: #2a2a2a;
            border: 1px solid #444444;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #dddddd;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #555555;
            color: #ffffff;
        }
        QMenu::item:disabled {
            color: #777777;
        }
        QMenu::separator {
            height: 1px;
            background-color: #444444;
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar { background-color: #2a2a2a; border-bottom: 1px solid #333333; spacing: 8px; padding: 8px; }
        QToolBar > QPushButton { background-color: transparent; color: #dddddd; border: 1px solid #444444; padding: 6px 16px; border-radius: 6px; font-size: 12px; min-width: 80px; min-height: 28px; }
        QToolBar > QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); border-color: #888888; color: #ffffff; }
        QToolBar > QPushButton:pressed { background-color: rgba(0, 0, 0, 0.2); }
        QToolBar > QPushButton#actionButton:hover { border-color: #888888; color: #ffffff; }
        QToolBar > QPushButton#helpButton:hover { border-color: #888888; color: #ffffff; }
        
        /* Regular button styling */
        QPushButton { background-color: #555555; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; font-size: 12px; }
        QPushButton:hover { background-color: #666666; }
        
        /* ComboBox styling */
        QComboBox { background-color: #2d2d2d; border: 1px solid #444444; color: white; padding: 5px; border-radius: 4px; }
        QComboBox::drop-down { border-left-color: #444444; }
        QComboBox QAbstractItemView { background-color: #2d2d2d; border: 1px solid #444444; selection-background-color: #555555; }
        
        /* LineEdit and TextEdit styling */
        QLineEdit { background-color: #2a2a2a; color: #d4d4d4; border: 1px solid #444444; border-radius: 4px; padding: 8px; selection-background-color: #4a4a4a; }
        QLineEdit:focus { border-color: #888888; }
        QTextEdit, QPlainTextEdit { background-color: #2a2a2a; color: #d4d4d4; border: 1px solid #444444; border-radius: 4px; padding: 8px; selection-background-color: #4a4a4a; }
    """

# Defines the color presets available for Frames and Containers in the Dark theme.
DARK_FRAME_COLORS = {
    "Green": {"color": "#2ecc71", "type": "full"}, "Blue": {"color": "#3498db", "type": "full"},
    "Purple": {"color": "#9b59b6", "type": "full"}, "Orange": {"color": "#e67e22", "type": "full"},
    "Red": {"color": "#e74c3c", "type": "full"}, "Yellow": {"color": "#f1c40f", "type": "full"},
    "Mid Gray": {"color": "#555555", "type": "full"}, "Dark Gray": {"color": "#3a3a3a", "type": "full"},
    "Green Header": {"color": "#2ecc71", "type": "header"}, "Blue Header": {"color": "#3498db", "type": "header"},
    "Purple Header": {"color": "#9b59b6", "type": "header"}, "Orange Header": {"color": "#e67e22", "type": "header"},
    "Red Header": {"color": "#e74c3c", "type": "header"}, "Yellow Header": {"color": "#f1c40f", "type": "header"}
}

# Defines the color presets available for Frames and Containers in the Monochromatic theme.
MONO_FRAME_COLORS = {
    "Green": {"color": "#666666", "type": "full"}, "Blue": {"color": "#777777", "type": "full"},
    "Purple": {"color": "#6a6a6a", "type": "full"}, "Orange": {"color": "#7a7a7a", "type": "full"},
    "Red": {"color": "#707070", "type": "full"}, "Yellow": {"color": "#808080", "type": "full"},
    "Mid Gray": {"color": "#555555", "type": "full"}, "Dark Gray": {"color": "#3a3a3a", "type": "full"},
    "Green Header": {"color": "#666666", "type": "header"}, "Blue Header": {"color": "#777777", "type": "header"},
    "Purple Header": {"color": "#6a6a6a", "type": "header"}, "Orange Header": {"color": "#7a7a7a", "type": "header"},
    "Red Header": {"color": "#707070", "type": "header"}, "Yellow Header": {"color": "#808080", "type": "header"}
}

class ColorPalette:
    """
    A data class to hold QColor objects for a specific theme palette.
    This provides a structured way to access theme colors throughout the
    application's drawing code.
    """
    def __init__(self, user_node, ai_node, selection, nav_highlight, frame_colors):
        """
        Initializes the ColorPalette.

        Args:
            user_node (str): Hex color for user nodes.
            ai_node (str): Hex color for AI nodes.
            selection (str): Hex color for selected items.
            nav_highlight (str): Hex color for navigation highlights.
            frame_colors (dict): Dictionary of frame color presets.
        """
        self.USER_NODE = QColor(user_node)
        self.AI_NODE = QColor(ai_node)
        self.SELECTION = QColor(selection)
        self.NAV_HIGHLIGHT = QColor(nav_highlight)
        self.FRAME_COLORS = frame_colors

# Concrete instance of the ColorPalette for the default dark theme.
DARK_PALETTE = ColorPalette(
    user_node="#2ecc71",
    ai_node="#3498db",
    selection="#2ecc71",
    nav_highlight="#f1c40f",
    frame_colors=DARK_FRAME_COLORS
)

# Concrete instance of the ColorPalette for the monochromatic theme.
MONO_PALETTE = ColorPalette(
    user_node="#999999",
    ai_node="#bbbbbb",
    selection="#ffffff",
    nav_highlight="#dddddd",
    frame_colors=MONO_FRAME_COLORS
)

# The main dictionary mapping theme names to their respective stylesheet and palette objects.
# This is the central point for theme lookup in the application.
THEMES = {
    "dark": {
        "stylesheet": StyleSheet.DARK_THEME,
        "palette": DARK_PALETTE
    },
    "mono": {
        "stylesheet": StyleSheet.MONOCHROMATIC_THEME,
        "palette": MONO_PALETTE
    }
}
