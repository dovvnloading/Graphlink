# This file contains visual constants and stylesheets for the Graphlink application.
# It centralizes all QSS (Qt StyleSheet) definitions, color palettes, and theme-related
# data to ensure a consistent look and feel and to make theming easier.

from PySide6.QtGui import QColor
from graphlink_paths import asset_url

class StyleSheet:
    """A namespace class to hold large QSS string constants for different themes."""

    # Stylesheet for the default dark theme, tuned for muted contrast.
    DARK_THEME_TEMPLATE = """
        QMainWindow, QWidget {
            background-color: {{qmainwindow_qwidget__background_color}};
            color: {{qmainwindow_qwidget__color}};
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: {{titlebar__background_color}};
            border-bottom: 1px solid {{titlebar__border_bottom}};
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: {{titlebar_qlabel__color}};
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
            background-color: {{titlebarbuttons_qpushbutton_hover__background_color}};
        }
        
        #closeButton:hover {
            background-color: {{closebutton_hover__background_color}} !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: {{qscrollbar_vertical__background}};
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: {{qscrollbar_handle_vertical__background_color}};
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: {{qscrollbar_handle_vertical_hover__background_color}};
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: {{qscrollbar_horizontal__background}};
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: {{qscrollbar_handle_horizontal__background_color}};
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: {{qscrollbar_handle_horizontal_hover__background_color}};
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
            background-color: {{qmenu__background_color}};
            border: 1px solid {{qmenu__border}};
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: {{qmenu_item__color}};
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: {{qmenu_item_selected__background_color}};
            color: white;
        }
        QMenu::item:disabled {
            color: {{qmenu_item_disabled__color}};
        }
        QMenu::separator {
            height: 1px;
            background-color: {{qmenu_separator__background_color}};
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: {{qtoolbar__background_color}};
            border-bottom: 1px solid {{qtoolbar__border_bottom}};
            spacing: 8px;
            padding: 8px;
        }
        
        /* General styling for buttons placed directly in the toolbar */
        QToolBar > QPushButton {
            background-color: transparent;
            color: {{qtoolbar_qpushbutton__color}};
            border: 1px solid {{qtoolbar_qpushbutton__border}};
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        
        QToolBar > QPushButton:hover {
            background-color: {{qtoolbar_qpushbutton_hover__background_color}};
            border-color: {{qtoolbar_qpushbutton_hover__border_color}};
            color: {{qtoolbar_qpushbutton_hover__color}};
        }
        
        QToolBar > QPushButton:pressed {
            background-color: {{qtoolbar_qpushbutton_pressed__background_color}};
        }
        
        /* Specific hover effects for different button types */
        QToolBar > QPushButton#actionButton:hover {
            border-color: {{qtoolbar_qpushbutton_actionbutton_hover__border_color}};
            color: {{qtoolbar_qpushbutton_actionbutton_hover__color}};
        }
        
        QToolBar > QPushButton#helpButton:hover {
            border-color: {{qtoolbar_qpushbutton_helpbutton_hover__border_color}};
            color: {{qtoolbar_qpushbutton_helpbutton_hover__color}};
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: {{qpushbutton__background_color}};
            color: {{qpushbutton__color}};
            border: 1px solid {{qpushbutton__border}};
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: {{qpushbutton_hover__background_color}};
            border-color: {{qpushbutton_hover__border_color}};
        }

        QPushButton:pressed {
            background-color: {{qpushbutton_pressed__background_color}};
            border-color: {{qpushbutton_pressed__border_color}};
        }

        QPushButton:disabled {
            background-color: {{qpushbutton_disabled__background_color}};
            border-color: {{qpushbutton_disabled__border_color}};
            color: {{qpushbutton_disabled__color}};
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: {{qcombobox__background_color}};
            border: 1px solid {{qcombobox__border}};
            color: {{qcombobox__color}};
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
            border-left-color: {{qcombobox_drop_down__border_left_color}};
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
            background-color: {{qcombobox_qabstractitemview__background_color}};
            border: 1px solid {{qcombobox_qabstractitemview__border}};
            selection-background-color: {{qcombobox_qabstractitemview__selection_background_color}};
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: {{qlineedit__background_color}};
            color: {{qlineedit__color}};
            border: 1px solid {{qlineedit__border}};
            border-radius: 4px;
            padding: 8px;
            selection-background-color: {{qlineedit__selection_background_color}};
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: {{qlineedit_focus__border_color}};
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: {{qtextedit_qplaintextedit__background_color}};
            color: {{qtextedit_qplaintextedit__color}};
            border: 1px solid {{qtextedit_qplaintextedit__border}};
            border-radius: 4px;
            padding: 8px;
            selection-background-color: {{qtextedit_qplaintextedit__selection_background_color}};
        }
    """

    # Stylesheet for a monochromatic (grayscale) theme for a minimalist look.
    MONOCHROMATIC_THEME_TEMPLATE = """
        QMainWindow, QWidget {
            background-color: {{qmainwindow_qwidget__background_color}};
            color: {{qmainwindow_qwidget__color}};
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: {{titlebar__background_color}};
            border-bottom: 1px solid {{titlebar__border_bottom}};
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel { color: {{titlebar_qlabel__color}}; font-size: 12px; font-weight: bold; }
        #titleBarButtons QPushButton { background-color: transparent; border: none; width: 34px; height: 26px; padding: 4px; border-radius: 4px; }
        #titleBarButtons QPushButton:hover { background-color: {{titlebarbuttons_qpushbutton_hover__background_color}}; }
        #closeButton:hover { background-color: {{closebutton_hover__background_color}} !important; }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical { background: {{qscrollbar_vertical__background}}; width: 10px; margin: 0px; border-radius: 5px; }
        QScrollBar::handle:vertical { background-color: {{qscrollbar_handle_vertical__background_color}}; min-height: 25px; border-radius: 5px; }
        QScrollBar::handle:vertical:hover { background-color: {{qscrollbar_handle_vertical_hover__background_color}}; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: none; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

        QScrollBar:horizontal { background: {{qscrollbar_horizontal__background}}; height: 10px; margin: 0px; border-radius: 5px; }
        QScrollBar::handle:horizontal { background-color: {{qscrollbar_handle_horizontal__background_color}}; min-width: 25px; border-radius: 5px; }
        QScrollBar::handle:horizontal:hover { background-color: {{qscrollbar_handle_horizontal_hover__background_color}}; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: none; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

        /* QMenu styling (Context Menus) */
        QMenu {
            background-color: {{qmenu__background_color}};
            border: 1px solid {{qmenu__border}};
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: {{qmenu_item__color}};
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: {{qmenu_item_selected__background_color}};
            color: {{qmenu_item_selected__color}};
        }
        QMenu::item:disabled {
            color: {{qmenu_item_disabled__color}};
        }
        QMenu::separator {
            height: 1px;
            background-color: {{qmenu_separator__background_color}};
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar { background-color: {{qtoolbar__background_color}}; border-bottom: 1px solid {{qtoolbar__border_bottom}}; spacing: 8px; padding: 8px; }
        QToolBar > QPushButton { background-color: transparent; color: {{qtoolbar_qpushbutton__color}}; border: 1px solid {{qtoolbar_qpushbutton__border}}; padding: 6px 16px; border-radius: 6px; font-size: 12px; min-width: 80px; min-height: 28px; }
        QToolBar > QPushButton:hover { background-color: {{qtoolbar_qpushbutton_hover__background_color}}; border-color: {{qtoolbar_qpushbutton_hover__border_color}}; color: {{qtoolbar_qpushbutton_hover__color}}; }
        QToolBar > QPushButton:pressed { background-color: {{qtoolbar_qpushbutton_pressed__background_color}}; }
        QToolBar > QPushButton#actionButton:hover { border-color: {{qtoolbar_qpushbutton_actionbutton_hover__border_color}}; color: {{qtoolbar_qpushbutton_actionbutton_hover__color}}; }
        QToolBar > QPushButton#helpButton:hover { border-color: {{qtoolbar_qpushbutton_helpbutton_hover__border_color}}; color: {{qtoolbar_qpushbutton_helpbutton_hover__color}}; }
        
        /* Regular button styling */
        QPushButton { background-color: {{qpushbutton__background_color}}; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; font-size: 12px; }
        QPushButton:hover { background-color: {{qpushbutton_hover__background_color}}; }
        
        /* ComboBox styling */
        QComboBox { background-color: {{qcombobox__background_color}}; border: 1px solid {{qcombobox__border}}; color: white; padding: 5px; border-radius: 4px; }
        QComboBox::drop-down { border-left-color: {{qcombobox_drop_down__border_left_color}}; }
        QComboBox QAbstractItemView { background-color: {{qcombobox_qabstractitemview__background_color}}; border: 1px solid {{qcombobox_qabstractitemview__border}}; selection-background-color: {{qcombobox_qabstractitemview__selection_background_color}}; }
        
        /* LineEdit and TextEdit styling */
        QLineEdit { background-color: {{qlineedit__background_color}}; color: {{qlineedit__color}}; border: 1px solid {{qlineedit__border}}; border-radius: 4px; padding: 8px; selection-background-color: {{qlineedit__selection_background_color}}; }
        QLineEdit:focus { border-color: {{qlineedit_focus__border_color}}; }
        QTextEdit, QPlainTextEdit { background-color: {{qtextedit_qplaintextedit__background_color}}; color: {{qtextedit_qplaintextedit__color}}; border: 1px solid {{qtextedit_qplaintextedit__border}}; border-radius: 4px; padding: 8px; selection-background-color: {{qtextedit_qplaintextedit__selection_background_color}}; }
    """

    # Stylesheet for a calm muted theme with reduced chroma and lower contrast jumps.
    MUTED_THEME_TEMPLATE = """
        QMainWindow, QWidget {
            background-color: {{qmainwindow_qwidget__background_color}};
            color: {{qmainwindow_qwidget__color}};
        }
        
        /* Custom Title Bar Styling */
        #titleBar {
            background-color: {{titlebar__background_color}};
            border-bottom: 1px solid {{titlebar__border_bottom}};
            padding: 4px;
            min-height: 32px;
        }
        
        #titleBar QLabel {
            color: {{titlebar_qlabel__color}};
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
            background-color: {{titlebarbuttons_qpushbutton_hover__background_color}};
        }
        
        #closeButton:hover {
            background-color: {{closebutton_hover__background_color}} !important;
        }
        
        /* Custom Scrollbar Styling */
        QScrollBar:vertical {
            background: {{qscrollbar_vertical__background}};
            width: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background-color: {{qscrollbar_handle_vertical__background_color}};
            min-height: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: {{qscrollbar_handle_vertical_hover__background_color}};
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }

        QScrollBar:horizontal {
            background: {{qscrollbar_horizontal__background}};
            height: 10px;
            margin: 0px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal {
            background-color: {{qscrollbar_handle_horizontal__background_color}};
            min-width: 25px;
            border-radius: 5px;
        }
        QScrollBar::handle:horizontal:hover {
            background-color: {{qscrollbar_handle_horizontal_hover__background_color}};
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
            background-color: {{qmenu__background_color}};
            border: 1px solid {{qmenu__border}};
            border-radius: 4px;
            padding: 4px;
        }
        QMenu::item {
            background-color: transparent;
            padding: 8px 24px 8px 24px;
            border-radius: 4px;
            color: {{qmenu_item__color}};
            font-family: 'Segoe UI', sans-serif;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: {{qmenu_item_selected__background_color}};
            color: {{qmenu_item_selected__color}};
        }
        QMenu::item:disabled {
            color: {{qmenu_item_disabled__color}};
        }
        QMenu::separator {
            height: 1px;
            background-color: {{qmenu_separator__background_color}};
            margin: 4px 0px;
        }

        /* Toolbar styling */
        QToolBar {
            background-color: {{qtoolbar__background_color}};
            border-bottom: 1px solid {{qtoolbar__border_bottom}};
            spacing: 8px;
            padding: 8px;
        }
        QToolBar > QPushButton {
            background-color: transparent;
            color: {{qtoolbar_qpushbutton__color}};
            border: 1px solid {{qtoolbar_qpushbutton__border}};
            padding: 6px 16px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
            min-width: 80px;
            min-height: 28px;
        }
        QToolBar > QPushButton:hover {
            background-color: {{qtoolbar_qpushbutton_hover__background_color}};
            border-color: {{qtoolbar_qpushbutton_hover__border_color}};
            color: {{qtoolbar_qpushbutton_hover__color}};
        }
        QToolBar > QPushButton:pressed {
            background-color: {{qtoolbar_qpushbutton_pressed__background_color}};
        }
        QToolBar > QPushButton#actionButton:hover {
            border-color: {{qtoolbar_qpushbutton_actionbutton_hover__border_color}};
            color: {{qtoolbar_qpushbutton_actionbutton_hover__color}};
        }
        QToolBar > QPushButton#helpButton:hover {
            border-color: {{qtoolbar_qpushbutton_helpbutton_hover__border_color}};
            color: {{qtoolbar_qpushbutton_helpbutton_hover__color}};
        }
        
        /* Regular button styling (e.g., Send button) */
        QPushButton {
            background-color: {{qpushbutton__background_color}};
            color: {{qpushbutton__color}};
            border: 1px solid {{qpushbutton__border}};
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        }
        
        QPushButton:hover {
            background-color: {{qpushbutton_hover__background_color}};
            border-color: {{qpushbutton_hover__border_color}};
        }
        
        QPushButton:pressed {
            background-color: {{qpushbutton_pressed__background_color}};
            border-color: {{qpushbutton_pressed__border_color}};
        }

        QPushButton:disabled {
            background-color: {{qpushbutton_disabled__background_color}};
            border-color: {{qpushbutton_disabled__border_color}};
            color: {{qpushbutton_disabled__color}};
        }
        
        /* Styling for ComboBoxes (dropdown menus) */
        QComboBox {
            background-color: {{qcombobox__background_color}};
            border: 1px solid {{qcombobox__border}};
            color: {{qcombobox__color}};
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
            border-left-color: {{qcombobox_drop_down__border_left_color}};
            border-left-style: solid;
            border-top-right-radius: 3px;
            border-bottom-right-radius: 3px;
        }

        QComboBox QAbstractItemView {
            background-color: {{qcombobox_qabstractitemview__background_color}};
            border: 1px solid {{qcombobox_qabstractitemview__border}};
            selection-background-color: {{qcombobox_qabstractitemview__selection_background_color}};
        }
        
        /* Styling for LineEdits (single-line text inputs) */
        QLineEdit {
            background-color: {{qlineedit__background_color}};
            color: {{qlineedit__color}};
            border: 1px solid {{qlineedit__border}};
            border-radius: 4px;
            padding: 8px;
            selection-background-color: {{qlineedit__selection_background_color}};
            font-family: 'Segoe UI', sans-serif;
        }
        
        QLineEdit:focus {
            border-color: {{qlineedit_focus__border_color}};
        }

        /* Styling for TextEdits (multi-line text inputs) */
        QTextEdit, QPlainTextEdit {
            background-color: {{qtextedit_qplaintextedit__background_color}};
            color: {{qtextedit_qplaintextedit__color}};
            border: 1px solid {{qtextedit_qplaintextedit__border}};
            border-radius: 4px;
            padding: 8px;
            selection-background-color: {{qtextedit_qplaintextedit__selection_background_color}};
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
# "qss" and "qss_alpha" (added in the QSS-generation increment) cover the
# three StyleSheet.*_THEME strings: every literal color the hand-written QSS
# used that isn't already produced by one of the four functions above, split
# by value shape (qss = flat hex, qss_alpha = rgba(...) literals, since the
# rest of this table has no representation for partial-alpha colors). Not
# deduplicated against palette/semantic/neutral_button/graph_node even where a
# value happens to coincide - same convention this table already uses
# elsewhere (palette.selection == semantic.default in every theme): group by
# original consumer, not by value, since a numeric coincidence today isn't a
# design relationship the two should be forced to keep in sync tomorrow.
#
# That coincidence is NOT rare, and is worth naming precisely rather than by
# one example: measured directly against the values below, 7 "qss" entries in
# dark, 7 in muted, and 19 in mono also equal some palette/semantic/
# neutral_button/graph_node value. Mono's cluster is the one to look at
# skeptically before trusting "coincidence" going forward - e.g. mono's
# qpushbutton_hover__background_color, qmenu_item_selected__background_color,
# and neutral_button.hover/.border all sit on #666666, and three unrelated
# selection-background properties (QLineEdit/QTextEdit/titlebar-button hover)
# all equal neutral_button.pressed's #4A4A4A - consistent with mono having
# originally been hand-authored from a small shared gray ramp rather than
# fully independent per-rule choices. Unlike the graph_node/neutral_button
# case above, nothing in the pre-refactor source computed one from the other
# for any of these pairs (verified: the original hand-written QSS and
# get_neutral_button_colors() were always two independent literals, even in
# mono), so leaving them as independent "qss" entries is still the correct
# call today - not a shortcut. It's flagged this explicitly so a later
# Tailwind/schema-codegen pass evaluates promoting some of mono's overlaps to
# real derivations on purpose, instead of rediscovering the pattern cold.
#
# The frame-color presets (DARK_FRAME_COLORS etc. below - ColorPalette.FRAME_COLORS
# reads those dicts directly, not this table) remain out of scope. This grouping
# (palette/semantic/neutral_button/graph_node/qss/qss_alpha) mirrors the
# functions/consumers that existed before this table did, kept as-is here to
# keep changes byte-identical and reviewable - it is not the target shape a
# later Tailwind/schema-codegen pass will want (see the master plan's flatter
# token list) and should be expected to be reshaped, not just extended, when
# that work starts.
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
# Composer island chrome colors, captured verbatim from
# web_ui/src/islands/composer/styles.css, where they were hand-authored as
# literal hex/rgba with their own eyeballed dark palette - never derived from
# any theme this app actually ships (section 2's own survey already flagged
# this: "styles.css hardcodes its own dark hex values").
#
# Split flat/alpha on exactly the qss/qss_alpha precedent: the rest of this
# table has no representation for partial-alpha colors, and composer uses 18
# rgba() literals for borders, overlays, focus rings and drop shadows.
#
# These two dicts are the SINGLE source of the values; the per-theme groups
# below are built from them with dict(), so the same 43 values are not
# triplicated by hand across three themes. That construction is deliberate and
# load-bearing in both directions: the per-theme SHAPE is real (a future
# increment authoring a genuine mono or muted composer palette edits one line
# per theme, with no restructuring), while the shared VALUES record the honest
# fact that composer has only ever had one palette. Storing identical values
# across themes is not a new shape here - graph_node is already byte-identical
# between "dark" and "mono" across all six of its keys (verified, not assumed).
#
# Naming is per-OCCURRENCE, not per-value: 43 tokens for 34 distinct colors.
# That follows this table's own "group by original consumer, not by value"
# rule (see the comment above), and needs no interpretation - e.g.
# status_text/status_failed_text hold the same #b0b0b0 at two different CSS
# sites, so the fact that .request-status.failed's override is currently a
# no-op stays visible in the data instead of being silently collapsed away.
# The one genuinely interpretive act, flagged rather than buried: composer's
# two box-shadow declarations each carry two colors (a drop shadow plus an
# inset highlight / focus ring), a case qss_alpha never hit, so those four got
# explicit part suffixes (*_shadow_drop / *_shadow_inset / *_focus_ring).
#
# Keys use SINGLE underscores, unlike qss's "__" property-separator
# convention - a double underscore would emit "--gl-composer-shell--background",
# which fails the CSS custom-property name shape the tests already enforce.
#
# DERIVED RELATIONSHIPS THAT PER-OCCURRENCE NAMING DOES NOT ENCODE. Read this
# before authoring a real per-theme palette - flat capture necessarily discards
# these, and nothing below will fail if they are broken:
#
#   attachment_count_border MUST TRACK shell_background. The badge is
#   position:absolute at top:-5px/right:-7px on a 30x30 control, so it overhangs
#   its button and sits on the shell. Its 1px border is exactly the shell color
#   because it is a cut-out/halo punching the badge visually out of the shell -
#   not a border color chosen for its own sake. Change shell_background alone
#   and the badge gains a visible dark ring.
#
#   attach_button_background (rgba white 0.018) and control_active_background
#   (rgba white 0.045) are overlays composited against shell_background, so
#   their effective color moves with it too - less sharply than the halo above,
#   but they are not independent choices either.
#
# Two probable authoring slips, frozen here as if deliberate, flagged rather
# than silently normalized (fixing them would be a real visual change, which
# this capture-only increment is not allowed to make):
#
#   shell_focus_border rgba(160,160,160,0.82) vs button_focus_outline
#   rgba(156,156,156,0.82) - two focus indicators differing by 4/255.
#
#   The four drop shadows use two alphas (0.20 and 0.22) that are almost
#   certainly one conceptual shadow.
_COMPOSER_CAPTURED = {
    "root_text": "#e7e7e7",
    "shell_background": "#1f1f1f",
    "input_text": "#eeeeee",
    "input_scrollbar_thumb": "#555555",
    "input_placeholder": "#888888",
    "attach_button_icon": "#c0c0c0",
    "attach_button_hover_icon": "#f4f4f4",
    "attachment_count_border": "#1f1f1f",
    "attachment_count_text": "#ededed",
    "attachment_count_background": "#555555",
    "attachment_count_hover_text": "#ffffff",
    "attachment_count_hover_background": "#666666",
    "restored_pill_text": "#c7c7c7",
    "control_text": "#c0c0c0",
    "control_active_text": "#f0f0f0",
    "control_icon": "#909090",
    "control_kicker_text": "#8a8a8a",
    "control_value_text": "#dddddd",
    "send_button_icon": "#ffffff",
    "send_button_background": "#414141",
    "send_button_hover_background": "#555555",
    "send_button_cancel_background": "#4a4a4a",
    "status_text": "#b0b0b0",
    "status_failed_text": "#b0b0b0",
    "status_action_text": "#bdbdbd",
}

_COMPOSER_ALPHA_CAPTURED = {
    "shell_border": "rgba(150, 150, 150, 0.34)",
    "shell_shadow_drop": "rgba(0, 0, 0, 0.20)",
    "shell_shadow_inset": "rgba(255, 255, 255, 0.035)",
    "shell_focus_border": "rgba(160, 160, 160, 0.82)",
    "shell_focus_shadow_drop": "rgba(0, 0, 0, 0.22)",
    "shell_focus_ring": "rgba(160, 160, 160, 0.12)",
    "attach_button_border": "rgba(150, 150, 150, 0.28)",
    "attach_button_background": "rgba(255, 255, 255, 0.018)",
    "attach_button_hover_border": "rgba(160, 160, 160, 0.72)",
    "attach_button_hover_background": "rgba(160, 160, 160, 0.10)",
    "button_focus_outline": "rgba(156, 156, 156, 0.82)",
    "restored_pill_border": "rgba(150, 150, 150, 0.28)",
    "restored_pill_background": "rgba(160, 160, 160, 0.08)",
    "control_active_border": "rgba(160, 160, 160, 0.34)",
    "control_active_background": "rgba(255, 255, 255, 0.045)",
    "send_button_border": "rgba(255, 255, 255, 0.15)",
    "send_button_shadow": "rgba(0, 0, 0, 0.22)",
    "send_button_cancel_shadow": "rgba(0, 0, 0, 0.20)",
}

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
        "qss": {
            "qmainwindow_qwidget__background_color": "#1E1E1E",
            "qmainwindow_qwidget__color": "#DCDCDC",
            "titlebar__background_color": "#272727",
            "titlebar__border_bottom": "#424242",
            "titlebar_qlabel__color": "#DCDCDC",
            "titlebarbuttons_qpushbutton_hover__background_color": "#424242",
            "closebutton_hover__background_color": "#656565",
            "qscrollbar_vertical__background": "#272727",
            "qscrollbar_handle_vertical__background_color": "#535353",
            "qscrollbar_handle_vertical_hover__background_color": "#676767",
            "qscrollbar_horizontal__background": "#272727",
            "qscrollbar_handle_horizontal__background_color": "#535353",
            "qscrollbar_handle_horizontal_hover__background_color": "#676767",
            "qmenu__background_color": "#272727",
            "qmenu__border": "#424242",
            "qmenu_item__color": "#DCDCDC",
            "qmenu_item_selected__background_color": "#858585",
            "qmenu_item_disabled__color": "#767676",
            "qmenu_separator__background_color": "#424242",
            "qtoolbar__background_color": "#272727",
            "qtoolbar__border_bottom": "#424242",
            "qtoolbar_qpushbutton__color": "#DCDCDC",
            "qtoolbar_qpushbutton__border": "#424242",
            "qtoolbar_qpushbutton_hover__border_color": "#5F5F5F",
            "qtoolbar_qpushbutton_hover__color": "#F1F1F1",
            "qtoolbar_qpushbutton_actionbutton_hover__border_color": "#848484",
            "qtoolbar_qpushbutton_actionbutton_hover__color": "#B0B0B0",
            "qtoolbar_qpushbutton_helpbutton_hover__border_color": "#878787",
            "qtoolbar_qpushbutton_helpbutton_hover__color": "#B1B1B1",
            "qpushbutton__background_color": "#393939",
            "qpushbutton__color": "#F0F0F0",
            "qpushbutton__border": "#505050",
            "qpushbutton_hover__background_color": "#454545",
            "qpushbutton_hover__border_color": "#5E5E5E",
            "qpushbutton_pressed__background_color": "#333333",
            "qpushbutton_pressed__border_color": "#494949",
            "qpushbutton_disabled__background_color": "#2F2F2F",
            "qpushbutton_disabled__border_color": "#424242",
            "qpushbutton_disabled__color": "#767676",
            "qcombobox__background_color": "#272727",
            "qcombobox__border": "#424242",
            "qcombobox__color": "#DCDCDC",
            "qcombobox_drop_down__border_left_color": "#424242",
            "qcombobox_qabstractitemview__background_color": "#272727",
            "qcombobox_qabstractitemview__border": "#424242",
            "qcombobox_qabstractitemview__selection_background_color": "#858585",
            "qlineedit__background_color": "#272727",
            "qlineedit__color": "#DCDCDC",
            "qlineedit__border": "#424242",
            "qlineedit__selection_background_color": "#494949",
            "qlineedit_focus__border_color": "#858585",
            "qtextedit_qplaintextedit__background_color": "#272727",
            "qtextedit_qplaintextedit__color": "#DCDCDC",
            "qtextedit_qplaintextedit__border": "#424242",
            "qtextedit_qplaintextedit__selection_background_color": "#494949",
        },
        "qss_alpha": {
            "qtoolbar_qpushbutton_hover__background_color": "rgba(255, 255, 255, 0.08)",
            "qtoolbar_qpushbutton_pressed__background_color": "rgba(0, 0, 0, 0.2)",
        },
        "composer": dict(_COMPOSER_CAPTURED),
        "composer_alpha": dict(_COMPOSER_ALPHA_CAPTURED),
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
        "qss": {
            "qmainwindow_qwidget__background_color": "#222222",
            "qmainwindow_qwidget__color": "#DDDDDD",
            "titlebar__background_color": "#2A2A2A",
            "titlebar__border_bottom": "#333333",
            "titlebar_qlabel__color": "#DDDDDD",
            "titlebarbuttons_qpushbutton_hover__background_color": "#4A4A4A",
            "closebutton_hover__background_color": "#6C6C6C",
            "qscrollbar_vertical__background": "#2A2A2A",
            "qscrollbar_handle_vertical__background_color": "#555555",
            "qscrollbar_handle_vertical_hover__background_color": "#6A6A6A",
            "qscrollbar_horizontal__background": "#2A2A2A",
            "qscrollbar_handle_horizontal__background_color": "#555555",
            "qscrollbar_handle_horizontal_hover__background_color": "#6A6A6A",
            "qmenu__background_color": "#2A2A2A",
            "qmenu__border": "#444444",
            "qmenu_item__color": "#DDDDDD",
            "qmenu_item_selected__background_color": "#666666",
            "qmenu_item_selected__color": "#FFFFFF",
            "qmenu_item_disabled__color": "#777777",
            "qmenu_separator__background_color": "#444444",
            "qtoolbar__background_color": "#2A2A2A",
            "qtoolbar__border_bottom": "#333333",
            "qtoolbar_qpushbutton__color": "#DDDDDD",
            "qtoolbar_qpushbutton__border": "#444444",
            "qtoolbar_qpushbutton_hover__border_color": "#888888",
            "qtoolbar_qpushbutton_hover__color": "#FFFFFF",
            "qtoolbar_qpushbutton_actionbutton_hover__border_color": "#888888",
            "qtoolbar_qpushbutton_actionbutton_hover__color": "#FFFFFF",
            "qtoolbar_qpushbutton_helpbutton_hover__border_color": "#888888",
            "qtoolbar_qpushbutton_helpbutton_hover__color": "#FFFFFF",
            "qpushbutton__background_color": "#555555",
            "qpushbutton_hover__background_color": "#666666",
            "qcombobox__background_color": "#2D2D2D",
            "qcombobox__border": "#444444",
            "qcombobox_drop_down__border_left_color": "#444444",
            "qcombobox_qabstractitemview__background_color": "#2D2D2D",
            "qcombobox_qabstractitemview__border": "#444444",
            "qcombobox_qabstractitemview__selection_background_color": "#555555",
            "qlineedit__background_color": "#2A2A2A",
            "qlineedit__color": "#D4D4D4",
            "qlineedit__border": "#444444",
            "qlineedit__selection_background_color": "#4A4A4A",
            "qlineedit_focus__border_color": "#888888",
            "qtextedit_qplaintextedit__background_color": "#2A2A2A",
            "qtextedit_qplaintextedit__color": "#D4D4D4",
            "qtextedit_qplaintextedit__border": "#444444",
            "qtextedit_qplaintextedit__selection_background_color": "#4A4A4A",
        },
        "qss_alpha": {
            "qtoolbar_qpushbutton_hover__background_color": "rgba(255, 255, 255, 0.1)",
            "qtoolbar_qpushbutton_pressed__background_color": "rgba(0, 0, 0, 0.2)",
        },
        "composer": dict(_COMPOSER_CAPTURED),
        "composer_alpha": dict(_COMPOSER_ALPHA_CAPTURED),
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
        "qss": {
            "qmainwindow_qwidget__background_color": "#1A1A1A",
            "qmainwindow_qwidget__color": "#D1D1D1",
            "titlebar__background_color": "#232323",
            "titlebar__border_bottom": "#383838",
            "titlebar_qlabel__color": "#D1D1D1",
            "titlebarbuttons_qpushbutton_hover__background_color": "#3C3C3C",
            "closebutton_hover__background_color": "#5E5E5E",
            "qscrollbar_vertical__background": "#232323",
            "qscrollbar_handle_vertical__background_color": "#494949",
            "qscrollbar_handle_vertical_hover__background_color": "#606060",
            "qscrollbar_horizontal__background": "#232323",
            "qscrollbar_handle_horizontal__background_color": "#494949",
            "qscrollbar_handle_horizontal_hover__background_color": "#606060",
            "qmenu__background_color": "#232323",
            "qmenu__border": "#383838",
            "qmenu_item__color": "#D1D1D1",
            "qmenu_item_selected__background_color": "#707070",
            "qmenu_item_selected__color": "#FFFFFF",
            "qmenu_item_disabled__color": "#707070",
            "qmenu_separator__background_color": "#383838",
            "qtoolbar__background_color": "#232323",
            "qtoolbar__border_bottom": "#383838",
            "qtoolbar_qpushbutton__color": "#D1D1D1",
            "qtoolbar_qpushbutton__border": "#383838",
            "qtoolbar_qpushbutton_hover__border_color": "#545454",
            "qtoolbar_qpushbutton_hover__color": "#F2F2F2",
            "qtoolbar_qpushbutton_actionbutton_hover__border_color": "#6F6F6F",
            "qtoolbar_qpushbutton_actionbutton_hover__color": "#A5A5A5",
            "qtoolbar_qpushbutton_helpbutton_hover__border_color": "#727272",
            "qtoolbar_qpushbutton_helpbutton_hover__color": "#A2A2A2",
            "qpushbutton__background_color": "#343434",
            "qpushbutton__color": "#E1E1E1",
            "qpushbutton__border": "#494949",
            "qpushbutton_hover__background_color": "#454545",
            "qpushbutton_hover__border_color": "#595959",
            "qpushbutton_pressed__background_color": "#303030",
            "qpushbutton_pressed__border_color": "#424242",
            "qpushbutton_disabled__background_color": "#2D2D2D",
            "qpushbutton_disabled__border_color": "#424242",
            "qpushbutton_disabled__color": "#757575",
            "qcombobox__background_color": "#232323",
            "qcombobox__border": "#383838",
            "qcombobox__color": "#D1D1D1",
            "qcombobox_drop_down__border_left_color": "#383838",
            "qcombobox_qabstractitemview__background_color": "#232323",
            "qcombobox_qabstractitemview__border": "#383838",
            "qcombobox_qabstractitemview__selection_background_color": "#707070",
            "qlineedit__background_color": "#232323",
            "qlineedit__color": "#D1D1D1",
            "qlineedit__border": "#383838",
            "qlineedit__selection_background_color": "#404040",
            "qlineedit_focus__border_color": "#707070",
            "qtextedit_qplaintextedit__background_color": "#232323",
            "qtextedit_qplaintextedit__color": "#D1D1D1",
            "qtextedit_qplaintextedit__border": "#383838",
            "qtextedit_qplaintextedit__selection_background_color": "#404040",
        },
        "qss_alpha": {
            "qtoolbar_qpushbutton_hover__background_color": "rgba(255, 255, 255, 0.08)",
            "qtoolbar_qpushbutton_pressed__background_color": "rgba(0, 0, 0, 0.2)",
        },
        "composer": dict(_COMPOSER_CAPTURED),
        "composer_alpha": dict(_COMPOSER_ALPHA_CAPTURED),
    },
}


def _generate_qss(theme_name: str) -> str:
    """Fill a StyleSheet *_THEME_TEMPLATE with THEME_TOKENS[theme_name]'s
    "qss" (flat hex) and "qss_alpha" (rgba literal) groups. Templates use
    double-brace {{token}} placeholders rather than str.format()'s single-
    brace fields, since the QSS text itself contains literal single braces
    (CSS rule delimiters) that str.format() would otherwise misparse.
    """
    tokens = THEME_TOKENS[theme_name]
    merged = {**tokens["qss"], **tokens["qss_alpha"]}
    result = _QSS_TEMPLATES[theme_name]
    for name, value in merged.items():
        result = result.replace("{{" + name + "}}", value)
    return result


_QSS_TEMPLATES = {
    "dark": StyleSheet.DARK_THEME_TEMPLATE,
    "mono": StyleSheet.MONOCHROMATIC_THEME_TEMPLATE,
    "muted": StyleSheet.MUTED_THEME_TEMPLATE,
}

# Resolved QSS strings, generated from THEME_TOKENS - StyleSheet.DARK_THEME
# etc. stay plain string class attributes (same external shape as before
# this refactor), just no longer hand-maintained literals.
StyleSheet.DARK_THEME = _generate_qss("dark")
StyleSheet.MONOCHROMATIC_THEME = _generate_qss("mono")
StyleSheet.MUTED_THEME = _generate_qss("muted")


_FRAME_COLORS_BY_THEME = {
    "dark": DARK_FRAME_COLORS,
    "mono": MONO_FRAME_COLORS,
    "muted": MUTED_FRAME_COLORS,
}

# The one font-family value used everywhere it's explicit in the three QSS
# templates above (confirmed via `grep -o "font-family:[^;]*;"` - a single
# distinct declaration, byte-identical to what's already shipping; no
# per-theme variation exists to preserve). Not every QSS rule sets
# font-family explicitly (several inherit Qt's platform default instead),
# so this is "the app's one explicit font choice," not "every font in use."
FONT_FAMILY = "'Segoe UI', sans-serif"

# THEME_TOKENS groups that belong to ONE island's own chrome rather than to the
# app-wide color vocabulary, mapped to the CSS custom-property prefix they
# flatten under. These are exported by css_custom_properties() (island CSS has
# to be able to resolve them) but deliberately kept OUT of tailwind_theme_css()'s
# @theme block: registering them as Tailwind design tokens would mint utilities
# like bg-gl-composer-shell-background, i.e. publish one island's private
# palette as a workspace-wide surface every other island could reach for. Same
# reasoning that keeps qss/qss_alpha out of the --gl-* export entirely, applied
# one tier down.
#
# NOTE ON SCOPE, stated precisely because the obvious reading overstates it:
# this carve-out removes island tokens from Tailwind's *paved path*, not from
# reach. _inline_bundle() injects css_root_block(CURRENT_THEME) - the FULL
# property set, all 43 composer tokens included - into every island's <head>,
# so any island's CSS can already resolve var(--gl-composer-*) directly, and
# Tailwind's arbitrary-value syntax (bg-[var(--gl-composer-shell-background)])
# still works. What this prevents is the ergonomic default: minting
# bg-gl-composer-* utilities that make reaching for another island's private
# chrome the path of least resistance. css_custom_properties()'s docstring is
# already honest about the same limitation for the mechanical names.
#
# When a SECOND island lands here, that is the first time there are two real
# examples to generalize a shared semantic vocabulary from - the release
# condition css_custom_properties()'s docstring names for the deferred
# bg-0/1/2-style naming. That check lives in tests/test_theme_tokens.py rather
# than as a module-level assert here, deliberately: an import-time assertion
# would make graphlink_styles unimportable and take the whole app down mid-
# feature, which punishes the developer who followed the convention and
# registered their island here, while the developer who instead added it to
# css_custom_properties()'s included_groups would never trip it at all. A test
# fails loudly for both without bricking anything.
_ISLAND_GROUPS = {
    "composer": "--gl-composer-",
    "composer_alpha": "--gl-composer-",
}


def css_custom_properties(theme_name: str) -> dict[str, str]:
    """Flatten THEME_TOKENS + the frame-color presets + FONT_FAMILY into
    --gl-*-named CSS custom properties, for web/Tailwind consumption
    (section 3.4: "Tailwind preset maps every utility to var(--gl-*))".

    Key names mirror THEME_TOKENS's own existing group/key names, mostly
    mechanically (kebab-cased), not a redesigned semantic vocabulary (section
    3.4's prose names one - surfaces bg-0/1/2, text tiers, accent, focus ring
    - but inventing that naming now, with zero real web consumer to validate
    it against, risks the exact "one example isn't enough to generalize
    correctly from" mistake this migration has already avoided elsewhere:
    see IslandBridge's id-not-path firewall, and the lib/ui/ deferral). Tried
    the assignment exercise directly before deciding this: there is no clean
    3-way "bg-0/1/2" split anywhere in the exported groups, and even counting
    the excluded qss group there are only two distinct chrome-background
    values per theme, not three - naming that vocabulary today would mean
    inventing a value, not renaming one. ("Mostly" mechanical, not entirely:
    the frame-color export does real, small interpretive work - deduping
    "X"/"X Header" pairs into one token on the verified assumption their
    colors match, and slugifying human-readable preset names - reasonable,
    but worth being precise about rather than claiming zero judgment.)

    Reshaping into a real semantic vocabulary is deferred to whichever
    increment actually retrofits a real island onto it, with real usage to
    design against. Until then, these mechanical --gl-* names should NOT
    become a public surface island CSS/TSX consumes directly (nothing
    currently stops that, e.g. via Tailwind's arbitrary-value syntax) - once
    real usage depends on the mechanical names, the eventual semantic rename
    becomes a breaking, repo-wide find-and-replace instead of an additive
    layer on top of this function's output.

    The "qss"/"qss_alpha" groups are deliberately excluded - those are
    QSS-only literals for the hand-written Qt stylesheets (window chrome,
    scrollbars, native widget states), not colors any island's own UI is
    expected to reuse. Known real gap in that boundary, confirmed by
    adversarial review, not fixed here: qlineedit_focus__border_color (in
    the excluded qss group) is the only "focus ring" color anywhere in this
    file, and qss also holds the only "surface bg"/"primary text" values -
    two of section 3.4's named concepts have no source data outside the
    group this function excludes. Whoever builds the semantic layer needs
    new curated source data for those, not just a rename of what this
    function already exports.
    """
    tokens = THEME_TOKENS[theme_name]
    included_groups = ("palette", "semantic", "neutral_button", "graph_node")
    excluded_groups = ("qss", "qss_alpha")
    # Self-verifying rather than relying solely on a separate test file to
    # catch drift: if a future edit adds a new top-level THEME_TOKENS group
    # without deciding whether it belongs in this export, this raises
    # immediately instead of silently omitting it forever.
    assert set(tokens) == set(included_groups) | set(excluded_groups) | set(
        _ISLAND_GROUPS
    ), (
        f"THEME_TOKENS[{theme_name!r}] has group(s) "
        f"{set(tokens) - set(included_groups) - set(excluded_groups) - set(_ISLAND_GROUPS)} "
        "that css_custom_properties() doesn't know to include or deliberately "
        "exclude - decide which before extending THEME_TOKENS further."
    )

    properties: dict[str, str] = {}

    for group in included_groups:
        group_slug = group.replace("_", "-")
        for key, value in tokens[group].items():
            properties[f"--gl-{group_slug}-{key.replace('_', '-')}"] = value

    # Island groups flatten under one prefix per island, deliberately dropping
    # the flat/alpha distinction the storage split needs - a consumer writing
    # var(--gl-composer-shell-border) should not have to know whether that
    # particular color happens to carry alpha. That makes the two groups share
    # one namespace, so their key sets must not collide.
    for group, prefix in _ISLAND_GROUPS.items():
        collisions = set(tokens[group]) & {
            key for other, other_prefix in _ISLAND_GROUPS.items()
            if other != group and other_prefix == prefix
            for key in tokens[other]
        }
        assert not collisions, (
            f"{theme_name}: island group {group!r} shares key(s) {collisions} with "
            f"another group flattening under the same '{prefix}' prefix - one would "
            "silently overwrite the other; rename before proceeding."
        )
        for key, value in tokens[group].items():
            properties[f"{prefix}{key.replace('_', '-')}"] = value

    # Resolved explicitly from each base ("full"-type) entry, never from
    # whichever of a "X"/"X Header" pair happens to be encountered first in
    # dict iteration order - if the two ever diverge, this raises rather
    # than silently exporting whichever one iteration order favored today.
    frame_colors = _FRAME_COLORS_BY_THEME[theme_name]
    frame_base_names = {name.removesuffix(" Header") for name in frame_colors}
    for base_name in frame_base_names:
        base_color = frame_colors[base_name]["color"]
        header_name = f"{base_name} Header"
        if header_name in frame_colors:
            header_color = frame_colors[header_name]["color"]
            assert header_color == base_color, (
                f"{theme_name}: {header_name!r} color {header_color!r} differs from "
                f"{base_name!r} color {base_color!r} - css_custom_properties()'s "
                "dedup assumes these always match; decide which one should "
                "actually be exported before extending this function to cover "
                "the divergent case."
            )
        slug = base_name.lower().replace(" ", "-")
        properties[f"--gl-frame-{slug}"] = base_color

    properties["--gl-font-family"] = FONT_FAMILY

    for name, value in properties.items():
        _assert_safe_css_declaration_value(name, value)
    return properties


_UNSAFE_CSS_VALUE_CHARS = (";", "{", "}", "\n", "\r", "<", ">")


def _assert_safe_css_declaration_value(property_name: str, value: str) -> None:
    """css_root_block() interpolates values directly into a CSS text block
    with no escaping - internal, self-authored data today (never user
    input), but a typo introducing one of these characters would silently
    produce syntactically broken or CSS-injected output otherwise. Loud
    failure here beats a broken :root block discovered later at paint time.

    `<`/`>` are included alongside the original CSS-breaking set because
    css_root_block()'s output is no longer only ever consumed as CSS syntax -
    graphlink_web_island_host.py's _inline_bundle() now embeds it directly
    into an HTML <style> raw-text element. A value containing the literal
    sequence `</style` there would prematurely close the style element in
    HTML parsing, and this page's CSP (script-src 'unsafe-inline') would let
    a broken-out inline <script> actually execute rather than render as
    inert text. Blocking `<`/`>` entirely is simpler and safer than only
    blocking the exact `</style` substring, and costs nothing today - no
    THEME_TOKENS value has ever contained either character."""
    if any(char in value for char in _UNSAFE_CSS_VALUE_CHARS):
        raise ValueError(
            f"{property_name} = {value!r} contains a character that would break out "
            f"of a CSS declaration ({_UNSAFE_CSS_VALUE_CHARS!r}) - fix the source value."
        )


def css_root_block(theme_name: str) -> str:
    """Render css_custom_properties(theme_name) as a valid CSS `:root { ... }`
    block, one declaration per line, sorted for a stable/reviewable diff."""
    properties = css_custom_properties(theme_name)
    lines = [f"  {name}: {value};" for name, value in sorted(properties.items())]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def tailwind_theme_css() -> str:
    """Generate a Tailwind v4 `@theme` block registering every --gl-* custom
    property NAME (not its per-theme value) as a Tailwind design token, so
    Tailwind's compiler generates real utility classes (bg-gl-palette-
    selection, text-gl-semantic-status-error, font-gl, etc.) that resolve
    via var(--gl-*) at runtime, whatever theme is active. This is the
    generated Tailwind preset section 3.1's target tree names directly:
    "lib/tokens/ - generated CSS-variable sheet + Tailwind preset
    (var(--gl-*))" - Tailwind v4 has no separate JS preset file the way v3
    did; its config is CSS-native, an @theme block is the whole preset.

    Only names matter here, not values - css_custom_properties() supplies
    values for a specific theme at runtime (via the still-deferred bridge-
    to-documentElement wiring) or at build time (via the still-deferred
    :root embedding); this function only needs to know every token NAME
    exists, once, so Tailwind's build-time utility generation has something
    to point var() at regardless of which theme resolves it later. "dark"
    is used below purely as a representative source of names - every theme
    has an identical key set (guarded by css_custom_properties()'s own
    cross-theme key-set test), so which theme is asked is not a decision
    with color-value consequences here, only a source-data convenience.

    Island-scoped groups (see _ISLAND_GROUPS) are excluded: they are exported
    as resolvable custom properties but must not become Tailwind utilities,
    since that would publish one island's private chrome palette as a
    workspace-wide surface. The exclusion is computed from those groups' real
    key sets rather than by matching the "--gl-composer-" prefix as a string,
    so a future app-wide token that merely happens to start with an island's
    prefix cannot be silently swallowed by the carve-out.
    """
    excluded = island_property_names("dark")
    names = [name for name in sorted(css_custom_properties("dark")) if name not in excluded]
    lines = []
    for name in names:
        if name == "--gl-font-family":
            lines.append(f"  --font-gl: var({name});")
        else:
            lines.append(f"  --color-{name[len('--'):]}: var({name});")
    return "@theme {\n" + "\n".join(lines) + "\n}\n"


def island_property_names(theme_name: str) -> set[str]:
    """The exact --gl-* names contributed by island-scoped groups.

    Computed from _ISLAND_GROUPS's real key sets, never by prefix-matching the
    emitted names, so this stays a precise carve-out rather than a namespace
    land-grab over everything that happens to start with the same characters.
    """
    return {
        f"{prefix}{key.replace('_', '-')}"
        for group, prefix in _ISLAND_GROUPS.items()
        for key in THEME_TOKENS[theme_name][group]
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
