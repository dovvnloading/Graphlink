import qtawesome as qta
from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from graphite_config import get_current_palette


class PluginCategoryButton(QPushButton):
    def __init__(self, category_name, category_icon, parent=None):
        super().__init__(parent)
        self.category_name = category_name
        self.setObjectName("pluginCategoryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setText(category_name)
        self.setIcon(qta.icon(category_icon, color="#d9e1ea"))
        self.setIconSize(QSize(14, 14))


class PluginEntryCard(QFrame):
    clicked = Signal(str)

    def __init__(self, plugin, accent_color, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self._accent_color = accent_color
        self._hovered = False
        self.setObjectName("pluginEntryCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        icon_badge = QLabel()
        icon_badge.setObjectName("pluginEntryBadge")
        icon_badge.setFixedSize(28, 28)
        icon_badge.setPixmap(qta.icon(plugin.get("icon", "fa5s.puzzle-piece"), color=accent_color).pixmap(14, 14))
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_badge, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(4)

        title_label = QLabel(plugin["name"])
        title_label.setObjectName("pluginEntryTitle")
        text_column.addWidget(title_label)

        description_label = QLabel(plugin["description"])
        description_label.setObjectName("pluginEntryDescription")
        description_label.setWordWrap(True)
        description_label.setMaximumHeight(30)
        text_column.addWidget(description_label)

        layout.addLayout(text_column, 1)

        chevron_label = QLabel()
        chevron_label.setObjectName("pluginEntryChevron")
        chevron_label.setPixmap(qta.icon("fa5s.chevron-right", color="#7b8694").pixmap(12, 12))
        chevron_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chevron_label.setFixedWidth(18)
        layout.addWidget(chevron_label, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.plugin["name"])
            event.accept()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_state()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_state()
        super().leaveEvent(event)

    def _refresh_state(self):
        self.setProperty("hovered", self._hovered)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class PluginFlyoutPanel(QFrame):
    pluginSelected = Signal(str)
    BASE_WIDTH = 526
    BASE_CATEGORY_RAIL_WIDTH = 214

    def __init__(self, plugin_portal, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.plugin_portal = plugin_portal
        self.category_buttons = {}
        self.current_category_name = None
        self.setObjectName("pluginFlyoutPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(self.BASE_WIDTH, 236)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 8)
        shadow.setColor(Qt.GlobalColor.black)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 12)
        outer_layout.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("pluginFlyoutShell")
        self.shell.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.shell)

        root_layout = QHBoxLayout(self.shell)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(0)

        self.category_panel = QWidget()
        self.category_panel.setObjectName("pluginCategoryRail")
        self.category_panel.setFixedWidth(self.BASE_CATEGORY_RAIL_WIDTH)
        category_layout = QVBoxLayout(self.category_panel)
        category_layout.setContentsMargins(10, 10, 10, 10)
        category_layout.setSpacing(6)

        rail_eyebrow = QLabel("Categories")
        rail_eyebrow.setObjectName("pluginSectionLabel")
        category_layout.addWidget(rail_eyebrow)

        self.category_button_column = QVBoxLayout()
        self.category_button_column.setContentsMargins(0, 4, 0, 0)
        self.category_button_column.setSpacing(6)
        category_layout.addLayout(self.category_button_column)
        category_layout.addStretch(1)

        divider = QFrame()
        divider.setObjectName("pluginFlyoutDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        divider.setLineWidth(1)

        content_panel = QWidget()
        content_panel.setObjectName("pluginMenuPane")
        content_layout = QVBoxLayout(content_panel)
        content_layout.setContentsMargins(12, 10, 12, 10)
        content_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        self.category_icon_label = QLabel()
        self.category_icon_label.setObjectName("pluginCategoryIcon")
        self.category_icon_label.setFixedSize(24, 24)
        self.category_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(self.category_icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        header_text_column = QVBoxLayout()
        header_text_column.setContentsMargins(0, 0, 0, 0)
        header_text_column.setSpacing(1)

        self.header_title = QLabel("Plugins")
        self.header_title.setObjectName("pluginMenuTitle")
        header_text_column.addWidget(self.header_title)

        self.header_body = QLabel("")
        self.header_body.setObjectName("pluginMenuMeta")
        header_text_column.addWidget(self.header_body)

        header_row.addLayout(header_text_column, 1)
        content_layout.addLayout(header_row)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("pluginScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_content = QWidget()
        scroll_content.setObjectName("pluginScrollContent")
        self.plugin_card_column = QVBoxLayout(scroll_content)
        self.plugin_card_column.setContentsMargins(0, 0, 0, 0)
        self.plugin_card_column.setSpacing(4)
        self.plugin_card_column.addStretch(1)
        scroll_area.setWidget(scroll_content)

        content_layout.addWidget(scroll_area, 1)

        root_layout.addWidget(self.category_panel)
        root_layout.addWidget(divider)
        root_layout.addWidget(content_panel, 1)

        self._apply_styles()
        self._build_category_buttons()

    def _apply_styles(self):
        palette = get_current_palette()
        accent = palette.SELECTION.name()
        panel_gray = "rgba(42, 42, 42, 248)"
        line_gray = "rgba(255, 255, 255, 0.08)"
        muted_text = "#8d8d8d"
        soft_text = "#bfc4ca"
        hover_gray = "rgba(255, 255, 255, 0.055)"
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.setStyleSheet(f"""
            QFrame#pluginFlyoutPanel {{
                background: transparent;
                border: none;
            }}
            QFrame#pluginFlyoutShell {{
                background-color: {panel_gray};
                border: 1px solid {line_gray};
                border-radius: 10px;
            }}
            QWidget#pluginCategoryRail, QWidget#pluginMenuPane {{
                background: transparent;
            }}
            QFrame#pluginFlyoutDivider {{
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                margin-top: 8px;
                margin-bottom: 8px;
            }}
            QLabel#pluginSectionLabel {{
                color: {muted_text};
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.12em;
                padding-left: 4px;
                background: transparent;
            }}
            QLabel#pluginCategoryIcon {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
            }}
            QLabel#pluginMenuTitle {{
                color: #f3f5f8;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#pluginMenuMeta {{
                color: {muted_text};
                font-size: 11px;
                background: transparent;
            }}
            QPushButton#pluginCategoryButton {{
                background-color: transparent;
                color: {soft_text};
                border: 1px solid transparent;
                border-radius: 7px;
                padding: 8px 10px;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#pluginCategoryButton {{
                min-height: 34px;
            }}
            QPushButton#pluginCategoryButton:hover {{
                background-color: {hover_gray};
                border-color: rgba(255, 255, 255, 0.05);
                color: #ffffff;
            }}
            QPushButton#pluginCategoryButton:checked {{
                background-color: rgba(255, 255, 255, 0.05);
                border-color: rgba(255, 255, 255, 0.06);
                color: #ffffff;
            }}
            QScrollArea#pluginScrollArea {{
                background: transparent;
            }}
            QWidget#pluginScrollContent {{
                background: transparent;
            }}
            QFrame#pluginEntryCard {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
            }}
            QFrame#pluginEntryCard[hovered="true"] {{
                background-color: {hover_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
            }}
            QLabel#pluginEntryBadge {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#pluginEntryTitle {{
                color: #f3f5f8;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            QLabel#pluginEntryDescription {{
                color: {muted_text};
                font-size: 11px;
                background: transparent;
            }}
            QLabel#pluginEntryChevron {{
                background: transparent;
            }}
        """)

        self._accent_color = accent
    def _build_category_buttons(self):
        previous_category = self.current_category_name
        while self.category_button_column.count():
            item = self.category_button_column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.category_buttons.clear()
        categories = self.plugin_portal.get_plugin_categories()
        for category in categories:
            button = PluginCategoryButton(category["name"], category["icon"], self)
            button.clicked.connect(lambda checked=False, category_name=category["name"]: self.set_current_category(category_name))
            self.category_button_column.addWidget(button)
            self.category_buttons[category["name"]] = button

        self.category_button_column.addStretch(1)
        if categories:
            preferred_category = previous_category if previous_category in self.category_buttons else categories[0]["name"]
            self.set_current_category(preferred_category)

    def refresh(self):
        self._apply_styles()
        self._build_category_buttons()

    def set_current_category(self, category_name):
        categories = self.plugin_portal.get_plugin_categories()
        category = next((item for item in categories if item["name"] == category_name), None)
        if category is None:
            return

        self.current_category_name = category_name
        for name, button in self.category_buttons.items():
            button.setChecked(name == category_name)

        self.header_title.setText(category["name"])
        self.header_body.setText(f"{len(category['plugins'])} plugin{'s' if len(category['plugins']) != 1 else ''}")
        self.category_icon_label.setPixmap(qta.icon(category["icon"], color=self._accent_color).pixmap(12, 12))

        while self.plugin_card_column.count():
            item = self.plugin_card_column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for plugin in category["plugins"]:
            card = PluginEntryCard(plugin, self._accent_color, self)
            card.clicked.connect(self._handle_plugin_clicked)
            self.plugin_card_column.addWidget(card)

        self.plugin_card_column.addStretch(1)

    def _handle_plugin_clicked(self, plugin_name):
        self.pluginSelected.emit(plugin_name)
        self.close()

    def show_for_anchor(self, anchor_widget):
        self.refresh()
        categories = self.plugin_portal.get_plugin_categories()
        active_category = next((item for item in categories if item["name"] == self.current_category_name), categories[0] if categories else None)

        category_rows = max(1, len(categories))
        plugin_rows = max(1, len(active_category["plugins"]) if active_category else 1)
        visible_rows = max(min(category_rows, 6), min(plugin_rows, 5))
        target_height = 52 + (visible_rows * 42) + 20
        self.resize(self.BASE_WIDTH, max(188, min(target_height, 312)))

        target_global = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 4))
        screen = QGuiApplication.screenAt(target_global) or QGuiApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None

        x = target_global.x()
        y = target_global.y()

        if available_geometry is not None:
            max_x = available_geometry.right() - self.width() - 12
            max_y = available_geometry.bottom() - self.height() - 12
            x = max(available_geometry.left() + 12, min(x, max_x))
            y = max(available_geometry.top() + 12, min(y, max_y))

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
