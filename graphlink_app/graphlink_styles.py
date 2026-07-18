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
    "Purple": {"color": "#7c7c7c", "type": "full"}, "Orange": {"color": "#818181", "type": "full"},
    "Red": {"color": "#7c7c7c", "type": "full"}, "Yellow": {"color": "#8e8e8e", "type": "full"},
    "Mid Gray": {"color": "#595959", "type": "full"}, "Dark Gray": {"color": "#414141", "type": "full"},
    "Green Header": {"color": "#838383", "type": "header"}, "Blue Header": {"color": "#828282", "type": "header"},
    "Purple Header": {"color": "#7c7c7c", "type": "header"}, "Orange Header": {"color": "#818181", "type": "header"},
    "Red Header": {"color": "#7c7c7c", "type": "header"}, "Yellow Header": {"color": "#8e8e8e", "type": "header"}
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

MUTED_FRAME_COLORS = {
    "Green": {"color": "#717171", "type": "full"}, "Blue": {"color": "#6d6d6d", "type": "full"},
    "Purple": {"color": "#6b6b6b", "type": "full"}, "Orange": {"color": "#707070", "type": "full"},
    "Red": {"color": "#6c6c6c", "type": "full"}, "Yellow": {"color": "#7c7c7c", "type": "full"},
    "Mid Gray": {"color": "#4c4c4c", "type": "full"}, "Dark Gray": {"color": "#383838", "type": "full"},
    "Green Header": {"color": "#717171", "type": "header"}, "Blue Header": {"color": "#6d6d6d", "type": "header"},
    "Purple Header": {"color": "#6b6b6b", "type": "header"}, "Orange Header": {"color": "#707070", "type": "header"},
    "Red Header": {"color": "#6c6c6c", "type": "header"}, "Yellow Header": {"color": "#7c7c7c", "type": "header"}
}

# Per-theme source of truth for the colors this app hands out through
# ColorPalette / get_semantic_color / get_neutral_button_colors /
# get_graph_node_colors. Each theme's values here are the exact resolved
# output of those functions before this table existed (captured from the
# running app, not retyped from the old per-theme branching logic), so every
# consumer of those functions keeps seeing byte-identical colors while the
# functions themselves become table lookups instead of separate per-theme
# formulas.
#
# Not yet covered here: the three hand-written QSS stylesheet strings above
# (deliberately deferred - see doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md) and
# the frame-color presets (DARK_FRAME_COLORS etc. below - ColorPalette.FRAME_COLORS
# reads those dicts directly, not this table). This grouping (palette/semantic/
# neutral_button/graph_node) mirrors the four functions that existed before this
# table did, kept as-is here to keep this specific change byte-identical and
# reviewable - it is not the target shape a later Tailwind/schema-codegen pass
# will want (see the master plan's flatter token list) and should be expected
# to be reshaped, not just extended, when that work starts.
#
# "graph_node" intentionally holds only its 6 members that are independent
# theme literals (body_start/body_end/header_start/header_end/badge_fill/
# panel_fill). The other 7 keys get_graph_node_colors() returns
# (border/header/dot/hover_dot/hover_outline/selected_outline/panel_border)
# are not independent tokens - they are aliases of, or QColor.lighter()
# derivations from, get_neutral_button_colors()'s output (this is exactly
# what the pre-this-table branching logic computed). Storing those 7 as their
# own flat literals here would silently drift from neutral_button the next
# time someone edits a theme's button colors without also updating 7 more
# entries by hand; get_graph_node_colors() below derives them live instead.
THEME_TOKENS = {
    "dark": {
        "palette": {
            "user_node": "#838383",
            "ai_node": "#828282",
            "selection": "#858585",
            "nav_highlight": "#949494",
        },
        "semantic": {
            "search_highlight": "#949494",
            "status_info": "#828282",
            "status_success": "#838383",
            "status_error": "#848484",
            "status_warning": "#919191",
            "artifact": "#828282",
            "conversation_user_bubble": "#696969",
            "conversation_ai_bubble": "#323232",
            "default": "#858585",
        },
        "neutral_button": {
            "background": "#393939",
            "hover": "#484848",
            "pressed": "#343434",
            "border": "#585858",
            "icon": "#f0f0f0",
            "muted_icon": "#bdbdbd",
        },
        "graph_node": {
            "body_start": "#303030",
            "body_end": "#292929",
            "header_start": "#3c3c3c",
            "header_end": "#333333",
            "badge_fill": "#484848",
            "panel_fill": "#202020",
        },
    },
    "mono": {
        "palette": {
            "user_node": "#999999",
            "ai_node": "#bbbbbb",
            "selection": "#ffffff",
            "nav_highlight": "#dddddd",
        },
        "semantic": {
            "search_highlight": "#dddddd",
            "status_info": "#bbbbbb",
            "status_success": "#999999",
            "status_error": "#9a9a9a",
            "status_warning": "#b0b0b0",
            "artifact": "#8f8f8f",
            "conversation_user_bubble": "#595959",
            "conversation_ai_bubble": "#323232",
            "default": "#ffffff",
        },
        "neutral_button": {
            "background": "#555555",
            "hover": "#666666",
            "pressed": "#4a4a4a",
            "border": "#666666",
            "icon": "#ffffff",
            "muted_icon": "#d5d5d5",
        },
        "graph_node": {
            "body_start": "#303030",
            "body_end": "#292929",
            "header_start": "#3c3c3c",
            "header_end": "#333333",
            "badge_fill": "#484848",
            "panel_fill": "#202020",
        },
    },
    "muted": {
        "palette": {
            "user_node": "#757575",
            "ai_node": "#707070",
            "selection": "#848484",
            "nav_highlight": "#8c8c8c",
        },
        "semantic": {
            "search_highlight": "#8c8c8c",
            "status_info": "#707070",
            "status_success": "#757575",
            "status_error": "#8a8a8a",
            "status_warning": "#8d8d8d",
            "artifact": "#707070",
            "conversation_user_bubble": "#5e5e5e",
            "conversation_ai_bubble": "#323232",
            "default": "#848484",
        },
        "neutral_button": {
            "background": "#3a3a3a",
            "hover": "#484848",
            "pressed": "#363636",
            "border": "#5e5e5e",
            "icon": "#dbdbdb",
            "muted_icon": "#bababa",
        },
        "graph_node": {
            "body_start": "#303030",
            "body_end": "#282828",
            "header_start": "#3d3d3d",
            "header_end": "#333333",
            "badge_fill": "#4a4a4a",
            "panel_fill": "#1c1c1c",
        },
    },
}

_FRAME_COLORS_BY_THEME = {
    "dark": DARK_FRAME_COLORS,
    "mono": MONO_FRAME_COLORS,
    "muted": MUTED_FRAME_COLORS,
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


def _build_palette(theme_name: str) -> "ColorPalette":
    tokens = THEME_TOKENS[theme_name]["palette"]
    return ColorPalette(
        user_node=tokens["user_node"],
        ai_node=tokens["ai_node"],
        selection=tokens["selection"],
        nav_highlight=tokens["nav_highlight"],
        frame_colors=_FRAME_COLORS_BY_THEME[theme_name],
    )


# Concrete ColorPalette instance per theme, built from THEME_TOKENS above
# instead of separately-maintained literals.
DARK_PALETTE = _build_palette("dark")
MONO_PALETTE = _build_palette("mono")
MUTED_PALETTE = _build_palette("muted")

# The main dictionary mapping theme names to their respective stylesheet and palette objects.
# This is the central point for theme lookup in the application.
THEMES = {
    "dark": {
        "stylesheet": StyleSheet.DARK_THEME.replace("__ASSET_DOWN_ARROW__", asset_url("down_arrow.png")),
        "palette": DARK_PALETTE,
        "tokens": THEME_TOKENS["dark"],
    },
    "muted": {
        "stylesheet": StyleSheet.MUTED_THEME,
        "palette": MUTED_PALETTE,
        "tokens": THEME_TOKENS["muted"],
    },
    "mono": {
        "stylesheet": StyleSheet.MONOCHROMATIC_THEME,
        "palette": MONO_PALETTE,
        "tokens": THEME_TOKENS["mono"],
    }
}
