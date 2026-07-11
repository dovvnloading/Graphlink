# This file contains visual constants and stylesheets for the Graphite application.
# It centralizes all QSS (Qt StyleSheet) definitions, color palettes, and theme-related
# data to ensure a consistent look and feel and to make theming easier.

from PySide6.QtGui import QColor
from graphite_paths import asset_url

class StyleSheet:
    """A namespace class to hold large QSS string constants for different themes."""

    # Stylesheet for the default dark theme, tuned for muted contrast.
    DARK_THEME = """
        QMainWindow, QWidget {
            background-color: #1b1e22;
            color: #d7dde4;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #23282f;
            border-bottom: 1px solid #3b434d;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: #d7dde4;
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
            background-color: #3b434d;
        }
        
        #closeButton:hover {
            background-color: #5f666f !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: #23282f;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: #4a5560;
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #5d6875;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: #23282f;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: #4a5560;
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #5d6875;
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
            background-color: #23282f;
            border: 1px solid #3b434d;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #d7dde4;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #70889f;
            color: white;
        }
        QMenu::item:disabled {
            color: #6f7780;
        }
        QMenu::separator {
            height: 1px;
            background-color: #3b434d;
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: #23282f;
            border-bottom: 1px solid #3b434d;
            spacing: 8px;
            padding: 8px;
        }
        
        /* General styling for buttons placed directly in the toolbar */
        QToolBar > QPushButton {
            background-color: transparent;
            color: #d7dde4;
            border: 1px solid #3b434d;
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        
        QToolBar > QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.08);
            border-color: #56606b;
            color: #eef1f5;
        }
        
        QToolBar > QPushButton:pressed {
            background-color: rgba(0, 0, 0, 0.2);
        }
        
        /* Specific hover effects for different button types */
        QToolBar > QPushButton#actionButton:hover {
            border-color: #6f879f;
            color: #9db3ca;
        }
        
        QToolBar > QPushButton#helpButton:hover {
            border-color: #808b72;
            color: #a8b59d;
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: #323a44;
            color: #edf0f4;
            border: 1px solid #48515b;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: #3b4653;
            border-color: #53606f;
        }

        QPushButton:pressed {
            background-color: #2c343d;
            border-color: #404a58;
        }

        QPushButton:disabled {
            background-color: #2a3038;
            border-color: #3b4350;
            color: #6f7782;
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: #23282f;
            border: 1px solid #3b434d;
            color: #d7dde4;
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
            border-left-color: #3b434d;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }

        QComboBox::down-arrow {
            image: url(__ASSET_DOWN_ARROW__);
            width: 10px;
            height: 10px;
        }

        QComboBox QAbstractItemView {
            background-color: #23282f;
            border: 1px solid #3b434d;
            selection-background-color: #70889f;
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: #23282f;
            color: #d7dde4;
            border: 1px solid #3b434d;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #264f78;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: #70889f;
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: #23282f;
            color: #d7dde4;
            border: 1px solid #3b434d;
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
        #titleBarButtons QPushButton:hover { background-color: #4a4a4a; }
        #closeButton:hover { background-color: #6c6c6c !important; }
        
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
            background-color: #666666;
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

    # Stylesheet for a calm muted theme with reduced chroma and lower contrast jumps.
    MUTED_THEME = """
        QMainWindow, QWidget {
            background-color: #171a1e;
            color: #cad2db;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #1f242a;
            border-bottom: 1px solid #323943;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: #cad2db;
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
            background-color: #343d48;
        }
        
        #closeButton:hover {
            background-color: #585f68 !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: #1f242a;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: #3f4a58;
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #556271;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: #1f242a;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: #3f4a58;
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #556271;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
            background: none;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: none;
        }

        /* QMenu styling (Context Menus) */
        QMenu {
            background-color: #1f242a;
            border: 1px solid #323943;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #cad2db;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #5f7384;
            color: #ffffff;
        }
        QMenu::item:disabled {
            color: #69717a;
        }
        QMenu::separator {
            height: 1px;
            background-color: #323943;
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: #1f242a;
            border-bottom: 1px solid #323943;
            spacing: 8px;
            padding: 8px;
        }
        QToolBar > QPushButton {
            background-color: transparent;
            color: #cad2db;
            border: 1px solid #323943;
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        QToolBar > QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.08);
            border-color: #4a5664;
            color: #eef2f7;
        }
        QToolBar > QPushButton:pressed {
            background-color: rgba(0, 0, 0, 0.2);
        }
        QToolBar > QPushButton#actionButton:hover {
            border-color: #607184;
            color: #94a8bb;
        }
        QToolBar > QPushButton#helpButton:hover {
            border-color: #6d7566;
            color: #9ea590;
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: #2f353f;
            color: #dbe2eb;
            border: 1px solid #3f4a58;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: #3b4652;
            border-color: #4e5b68;
        }
        
        QPushButton:pressed {
            background-color: #2a3139;
            border-color: #3a4350;
        }

        QPushButton:disabled {
            background-color: #282e37;
            border-color: #3a434f;
            color: #6f7680;
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: #1f242a;
            border: 1px solid #323943;
            color: #cad2db;
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
            border-left-color: #323943;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }

        QComboBox QAbstractItemView {
            background-color: #1f242a;
            border: 1px solid #323943;
            selection-background-color: #5f7384;
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: #1f242a;
            color: #cad2db;
            border: 1px solid #323943;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #304257;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: #5f7384;
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: #1f242a;
            color: #cad2db;
            border: 1px solid #323943;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #304257;
        }
    """

# Defines the color presets available for Frames and Containers in the Dark theme.
DARK_FRAME_COLORS = {
    "Green": {"color": "#6f8798", "type": "full"}, "Blue": {"color": "#6f84a0", "type": "full"},
    "Purple": {"color": "#827892", "type": "full"}, "Orange": {"color": "#8e7f70", "type": "full"},
    "Red": {"color": "#8a7780", "type": "full"}, "Yellow": {"color": "#918f79", "type": "full"},
    "Mid Gray": {"color": "#525a64", "type": "full"}, "Dark Gray": {"color": "#3a424d", "type": "full"},
    "Green Header": {"color": "#6f8798", "type": "header"}, "Blue Header": {"color": "#6f84a0", "type": "header"},
    "Purple Header": {"color": "#827892", "type": "header"}, "Orange Header": {"color": "#8e7f70", "type": "header"},
    "Red Header": {"color": "#8a7780", "type": "header"}, "Yellow Header": {"color": "#918f79", "type": "header"}
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
    user_node="#6f8798",
    ai_node="#6f84a0",
    selection="#70889f",
    nav_highlight="#8f987e",
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

MUTED_FRAME_COLORS = {
    "Green": {"color": "#5f7584", "type": "full"}, "Blue": {"color": "#5f6f87", "type": "full"},
    "Purple": {"color": "#726879", "type": "full"}, "Orange": {"color": "#786f62", "type": "full"},
    "Red": {"color": "#7a6770", "type": "full"}, "Yellow": {"color": "#7e7d6a", "type": "full"},
    "Mid Gray": {"color": "#454d56", "type": "full"}, "Dark Gray": {"color": "#323943", "type": "full"},
    "Green Header": {"color": "#5f7584", "type": "header"}, "Blue Header": {"color": "#5f6f87", "type": "header"},
    "Purple Header": {"color": "#726879", "type": "header"}, "Orange Header": {"color": "#786f62", "type": "header"},
    "Red Header": {"color": "#7a6770", "type": "header"}, "Yellow Header": {"color": "#7e7d6a", "type": "header"}
}

# Concrete instance of the ColorPalette for the muted theme.
MUTED_PALETTE = ColorPalette(
    user_node="#63788a",
    ai_node="#67708d",
    selection="#768a6e",
    nav_highlight="#8a8f72",
    frame_colors=MUTED_FRAME_COLORS
)

# The main dictionary mapping theme names to their respective stylesheet and palette objects.
# This is the central point for theme lookup in the application.
THEMES = {
    "dark": {
        "stylesheet": StyleSheet.DARK_THEME.replace("__ASSET_DOWN_ARROW__", asset_url("down_arrow.png")),
        "palette": DARK_PALETTE
    },
    "muted": {
        "stylesheet": StyleSheet.MUTED_THEME,
        "palette": MUTED_PALETTE
    },
    "mono": {
        "stylesheet": StyleSheet.MONOCHROMATIC_THEME,
        "palette": MONO_PALETTE
    }
}
