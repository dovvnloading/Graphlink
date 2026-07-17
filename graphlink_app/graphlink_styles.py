# This file contains visual constants and stylesheets for the Graphlink application.
# It centralizes all QSS (Qt StyleSheet) definitions, color palettes, and theme-related
# data to ensure a consistent look and feel and to make theming easier.

from PySide6.QtGui import QColor
from graphlink_paths import asset_url

class StyleSheet:
    """A namespace class to hold large QSS string constants for different themes."""

    # Stylesheet for the default dark theme, tuned for muted contrast.
    DARK_THEME = """
        QMainWindow, QWidget {
            background-color: #1E1E1E;
            color: #DCDCDC;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #272727;
            border-bottom: 1px solid #424242;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: #DCDCDC;
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
            background-color: #424242;
        }
        
        #closeButton:hover {
            background-color: #656565 !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: #272727;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: #535353;
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #676767;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: #272727;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: #535353;
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #676767;
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
            background-color: #272727;
            border: 1px solid #424242;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #DCDCDC;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #858585;
            color: white;
        }
        QMenu::item:disabled {
            color: #767676;
        }
        QMenu::separator {
            height: 1px;
            background-color: #424242;
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: #272727;
            border-bottom: 1px solid #424242;
            spacing: 8px;
            padding: 8px;
        }
        
        /* General styling for buttons placed directly in the toolbar */
        QToolBar > QPushButton {
            background-color: transparent;
            color: #DCDCDC;
            border: 1px solid #424242;
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        
        QToolBar > QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.08);
            border-color: #5F5F5F;
            color: #F1F1F1;
        }
        
        QToolBar > QPushButton:pressed {
            background-color: rgba(0, 0, 0, 0.2);
        }
        
        /* Specific hover effects for different button types */
        QToolBar > QPushButton#actionButton:hover {
            border-color: #848484;
            color: #B0B0B0;
        }
        
        QToolBar > QPushButton#helpButton:hover {
            border-color: #878787;
            color: #B1B1B1;
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: #393939;
            color: #F0F0F0;
            border: 1px solid #505050;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: #454545;
            border-color: #5E5E5E;
        }

        QPushButton:pressed {
            background-color: #333333;
            border-color: #494949;
        }

        QPushButton:disabled {
            background-color: #2F2F2F;
            border-color: #424242;
            color: #767676;
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: #272727;
            border: 1px solid #424242;
            color: #DCDCDC;
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
            border-left-color: #424242;
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
            background-color: #272727;
            border: 1px solid #424242;
            selection-background-color: #858585;
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: #272727;
            color: #DCDCDC;
            border: 1px solid #424242;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #494949;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: #858585;
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: #272727;
            color: #DCDCDC;
            border: 1px solid #424242;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #494949;
        }
    """

    # Stylesheet for a monochromatic (grayscale) theme for a minimalist look.
    MONOCHROMATIC_THEME = """
        QMainWindow, QWidget {
            background-color: #222222;
            color: #DDDDDD;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #2A2A2A;
            border-bottom: 1px solid #333333;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel { color: #DDDDDD; font-size: 12px; font-weight: bold; }
        #titleBarButtons QPushButton { background-color: transparent; border: none; width: 34px; height: 26px; padding: 4px; border-radius: 4px; }
        #titleBarButtons QPushButton:hover { background-color: #4A4A4A; }
        #closeButton:hover { background-color: #6C6C6C !important; }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical { background: #2A2A2A; width: 10px; margin: 0px; border-radius: 5px; }
        QScrollBar::handle:vertical { background-color: #555555; min-height: 25px; border-radius: 5px; }
        QScrollBar::handle:vertical:hover { background-color: #6A6A6A; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: none; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

        QScrollBar:horizontal { background: #2A2A2A; height: 10px; margin: 0px; border-radius: 5px; }
        QScrollBar::handle:horizontal { background-color: #555555; min-width: 25px; border-radius: 5px; }
        QScrollBar::handle:horizontal:hover { background-color: #6A6A6A; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: none; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

        /* QMenu styling (Context Menus) */
        QMenu {
            background-color: #2A2A2A;
            border: 1px solid #444444;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #DDDDDD;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #666666;
            color: #FFFFFF;
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
        QToolBar { background-color: #2A2A2A; border-bottom: 1px solid #333333; spacing: 8px; padding: 8px; }
        QToolBar > QPushButton { background-color: transparent; color: #DDDDDD; border: 1px solid #444444; padding: 6px 16px; border-radius: 6px; font-size: 12px; min-width: 80px; min-height: 28px; }
        QToolBar > QPushButton:hover { background-color: rgba(255, 255, 255, 0.1); border-color: #888888; color: #FFFFFF; }
        QToolBar > QPushButton:pressed { background-color: rgba(0, 0, 0, 0.2); }
        QToolBar > QPushButton#actionButton:hover { border-color: #888888; color: #FFFFFF; }
        QToolBar > QPushButton#helpButton:hover { border-color: #888888; color: #FFFFFF; }
        
        /* Regular button styling */
        QPushButton { background-color: #555555; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; font-size: 12px; }
        QPushButton:hover { background-color: #666666; }
        
        /* ComboBox styling */
        QComboBox { background-color: #2D2D2D; border: 1px solid #444444; color: white; padding: 5px; border-radius: 4px; }
        QComboBox::drop-down { border-left-color: #444444; }
        QComboBox QAbstractItemView { background-color: #2D2D2D; border: 1px solid #444444; selection-background-color: #555555; }
        
        /* LineEdit and TextEdit styling */
        QLineEdit { background-color: #2A2A2A; color: #D4D4D4; border: 1px solid #444444; border-radius: 4px; padding: 8px; selection-background-color: #4A4A4A; }
        QLineEdit:focus { border-color: #888888; }
        QTextEdit, QPlainTextEdit { background-color: #2A2A2A; color: #D4D4D4; border: 1px solid #444444; border-radius: 4px; padding: 8px; selection-background-color: #4A4A4A; }
    """

    # Stylesheet for a calm muted theme with reduced chroma and lower contrast jumps.
    MUTED_THEME = """
        QMainWindow, QWidget {
            background-color: #1A1A1A;
            color: #D1D1D1;
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: #232323;
            border-bottom: 1px solid #383838;
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: #D1D1D1;
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
            background-color: #3C3C3C;
        }
        
        #closeButton:hover {
            background-color: #5E5E5E !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: #232323;
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: #494949;
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #606060;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: #232323;
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: #494949;
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: #606060;
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
            background-color: #232323;
            border: 1px solid #383838;
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: #D1D1D1;
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #707070;
            color: #FFFFFF;
        }
        QMenu::item:disabled {
            color: #707070;
        }
        QMenu::separator {
            height: 1px;
            background-color: #383838;
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: #232323;
            border-bottom: 1px solid #383838;
            spacing: 8px;
            padding: 8px;
        }
        QToolBar > QPushButton {
            background-color: transparent;
            color: #D1D1D1;
            border: 1px solid #383838;
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        QToolBar > QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.08);
            border-color: #545454;
            color: #F2F2F2;
        }
        QToolBar > QPushButton:pressed {
            background-color: rgba(0, 0, 0, 0.2);
        }
        QToolBar > QPushButton#actionButton:hover {
            border-color: #6F6F6F;
            color: #A5A5A5;
        }
        QToolBar > QPushButton#helpButton:hover {
            border-color: #727272;
            color: #A2A2A2;
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: #343434;
            color: #E1E1E1;
            border: 1px solid #494949;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: #454545;
            border-color: #595959;
        }
        
        QPushButton:pressed {
            background-color: #303030;
            border-color: #424242;
        }

        QPushButton:disabled {
            background-color: #2D2D2D;
            border-color: #424242;
            color: #757575;
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: #232323;
            border: 1px solid #383838;
            color: #D1D1D1;
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
            border-left-color: #383838;
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }

        QComboBox QAbstractItemView {
            background-color: #232323;
            border: 1px solid #383838;
            selection-background-color: #707070;
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: #232323;
            color: #D1D1D1;
            border: 1px solid #383838;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #404040;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: #707070;
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: #232323;
            color: #D1D1D1;
            border: 1px solid #383838;
            border-radius: 4px;
            padding: 8px;
            selection-background-color: #404040;
        }
    """

# Defines the color presets available for Frames and Containers in the Dark theme.
DARK_FRAME_COLORS = {
    "Green": {"color": "#838383", "type": "full"}, "Blue": {"color": "#828282", "type": "full"},
    "Purple": {"color": "#7C7C7C", "type": "full"}, "Orange": {"color": "#818181", "type": "full"},
    "Red": {"color": "#7C7C7C", "type": "full"}, "Yellow": {"color": "#8E8E8E", "type": "full"},
    "Mid Gray": {"color": "#595959", "type": "full"}, "Dark Gray": {"color": "#414141", "type": "full"},
    "Green Header": {"color": "#838383", "type": "header"}, "Blue Header": {"color": "#828282", "type": "header"},
    "Purple Header": {"color": "#7C7C7C", "type": "header"}, "Orange Header": {"color": "#818181", "type": "header"},
    "Red Header": {"color": "#7C7C7C", "type": "header"}, "Yellow Header": {"color": "#8E8E8E", "type": "header"}
}

# Defines the color presets available for Frames and Containers in the Monochromatic theme.
MONO_FRAME_COLORS = {
    "Green": {"color": "#666666", "type": "full"}, "Blue": {"color": "#777777", "type": "full"},
    "Purple": {"color": "#6A6A6A", "type": "full"}, "Orange": {"color": "#7A7A7A", "type": "full"},
    "Red": {"color": "#707070", "type": "full"}, "Yellow": {"color": "#808080", "type": "full"},
    "Mid Gray": {"color": "#555555", "type": "full"}, "Dark Gray": {"color": "#3A3A3A", "type": "full"},
    "Green Header": {"color": "#666666", "type": "header"}, "Blue Header": {"color": "#777777", "type": "header"},
    "Purple Header": {"color": "#6A6A6A", "type": "header"}, "Orange Header": {"color": "#7A7A7A", "type": "header"},
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
    user_node="#838383",
    ai_node="#828282",
    selection="#858585",
    nav_highlight="#949494",
    frame_colors=DARK_FRAME_COLORS
)

# Concrete instance of the ColorPalette for the monochromatic theme.
MONO_PALETTE = ColorPalette(
    user_node="#999999",
    ai_node="#BBBBBB",
    selection="#FFFFFF",
    nav_highlight="#DDDDDD",
    frame_colors=MONO_FRAME_COLORS
)

MUTED_FRAME_COLORS = {
    "Green": {"color": "#717171", "type": "full"}, "Blue": {"color": "#6D6D6D", "type": "full"},
    "Purple": {"color": "#6B6B6B", "type": "full"}, "Orange": {"color": "#707070", "type": "full"},
    "Red": {"color": "#6C6C6C", "type": "full"}, "Yellow": {"color": "#7C7C7C", "type": "full"},
    "Mid Gray": {"color": "#4C4C4C", "type": "full"}, "Dark Gray": {"color": "#383838", "type": "full"},
    "Green Header": {"color": "#717171", "type": "header"}, "Blue Header": {"color": "#6D6D6D", "type": "header"},
    "Purple Header": {"color": "#6B6B6B", "type": "header"}, "Orange Header": {"color": "#707070", "type": "header"},
    "Red Header": {"color": "#6C6C6C", "type": "header"}, "Yellow Header": {"color": "#7C7C7C", "type": "header"}
}

# Concrete instance of the ColorPalette for the muted theme.
MUTED_PALETTE = ColorPalette(
    user_node="#757575",
    ai_node="#707070",
    selection="#848484",
    nav_highlight="#8C8C8C",
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
