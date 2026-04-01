"""Pin-related flyout widgets and placeholders."""

import qtawesome as qta
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from graphite_config import get_current_palette

class NavigationPin:
    def __init__(self):
        self.title = "Dummy Pin"
        self.note = ""
        self.scene = lambda: None 


class PinOverlay(QFrame):
    closed = Signal()
    BASE_WIDTH = 360

    def __init__(self, canvas_view, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.window = canvas_view
        self.canvas_view = canvas_view
        self.pins = []
        self.setObjectName("pinFlyoutPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(self.BASE_WIDTH, 280)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 10)
        shadow.setColor(Qt.GlobalColor.black)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(12, 12, 12, 14)
        outer_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("pinFlyoutShell")
        self.container.setGraphicsEffect(shadow)
        outer_layout.addWidget(self.container)

        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        self.icon_badge = QLabel()
        self.icon_badge.setObjectName("pinFlyoutBadge")
        self.icon_badge.setFixedSize(28, 28)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.icon_badge, 0, Qt.AlignmentFlag.AlignTop)

        header_text_column = QVBoxLayout()
        header_text_column.setContentsMargins(0, 0, 0, 0)
        header_text_column.setSpacing(2)

        self.header_text = QLabel("Navigation Pins")
        self.header_text.setObjectName("pinFlyoutTitle")
        header_text_column.addWidget(self.header_text)

        self.header_body = QLabel("Quick-jump bookmarks for important spots on the canvas.")
        self.header_body.setObjectName("pinFlyoutMeta")
        self.header_body.setWordWrap(True)
        header_text_column.addWidget(self.header_body)
        header_layout.addLayout(header_text_column, 1)

        self.close_btn = QPushButton("Close")
        self.close_btn.setObjectName("pinFlyoutCloseButton")
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)

        main_layout.addWidget(header_widget)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("pinScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.pin_list = QWidget()
        self.pin_list.setObjectName("pinScrollContent")
        self.pin_layout = QVBoxLayout(self.pin_list)
        self.pin_layout.setSpacing(6)
        self.pin_layout.setContentsMargins(0, 0, 0, 0)
        self.pin_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll.setWidget(self.pin_list)
        main_layout.addWidget(self.scroll, 1)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(8)

        self.pin_count_label = QLabel("")
        self.pin_count_label.setObjectName("pinFlyoutCount")
        footer_row.addWidget(self.pin_count_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.add_btn = QPushButton("Drop New Pin")
        self.add_btn.setObjectName("pinAddButton")
        self.add_btn.setIcon(qta.icon('fa5s.map-pin', color='white'))
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self.create_pin)
        footer_row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignRight)

        main_layout.addLayout(footer_row)

        self.on_theme_changed()

    def on_theme_changed(self):
        palette = get_current_palette()
        accent = palette.SELECTION.name()
        accent_color = QColor(palette.SELECTION)
        brightness = (accent_color.red() * 299 + accent_color.green() * 587 + accent_color.blue() * 114) / 1000
        accent_text = "#161616" if brightness > 145 else "#f7f9fb"
        muted_text = "#8d8d8d"
        soft_text = "#d9e1ea"
        hover_gray = "rgba(255, 255, 255, 0.055)"
        badge_gray = "rgba(255, 255, 255, 0.025)"

        self.icon_badge.setPixmap(qta.icon('fa5s.map-marked-alt', color=accent).pixmap(14, 14))
        self.add_btn.setIcon(qta.icon('fa5s.map-pin', color=accent_text))
        self.setStyleSheet(f"""
            PinOverlay {{
                background-color: transparent;
            }}
            QFrame#pinFlyoutPanel {{
                background: transparent;
                border: none;
            }}
            QFrame#pinFlyoutShell {{
                background-color: rgba(42, 42, 42, 248);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }}
            QFrame#pinFlyoutShell QLabel,
            QFrame#pinFlyoutShell QWidget {{
                background: transparent;
            }}
            QLabel#pinFlyoutBadge {{
                background-color: {badge_gray};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QLabel#pinFlyoutTitle {{
                color: #f3f5f8;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#pinFlyoutMeta, QLabel#pinFlyoutCount {{
                color: {muted_text};
                font-size: 11px;
            }}
            QPushButton#pinFlyoutCloseButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: #f3f5f8;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton#pinFlyoutCloseButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QPushButton#pinAddButton {{
                background-color: {accent};
                color: {accent_text};
                border: none;
                border-radius: 8px;
                padding: 9px 14px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#pinAddButton:hover {{
                background-color: {accent_color.lighter(108).name()};
            }}
            QPushButton#pinAddButton:disabled {{
                background-color: #555555;
                color: #c9c9c9;
            }}
            QScrollArea#pinScrollArea, QWidget#pinScrollContent {{
                background: transparent;
                border: none;
            }}
            QFrame#pinEntryCard {{
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }}
            QPushButton#pinEntryButton {{
                background-color: transparent;
                border: none;
                border-radius: 8px;
                padding: 10px 10px 10px 4px;
                color: #f3f5f8;
                text-align: left;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#pinEntryButton:hover {{
                background-color: {hover_gray};
            }}
            QLabel#pinEntryNote {{
                color: {muted_text};
                font-size: 11px;
                padding-left: 6px;
            }}
            QPushButton#pinEntryDeleteButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 6px;
                min-width: 28px;
                min-height: 28px;
            }}
            QPushButton#pinEntryDeleteButton:hover {{
                background-color: {hover_gray};
                border-color: rgba(255, 255, 255, 0.06);
            }}
            QLabel#pinEmptyState {{
                color: {soft_text};
                font-size: 12px;
                padding: 18px 10px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 0.18);
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self.refresh_pins()

    def refresh_pins(self):
        self.pins = [pin for pin in self.pins if pin.scene() is not None]

        while self.pin_layout.count():
            item = self.pin_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self.pins:
            for pin in self.pins:
                if pin.scene():
                    self._create_pin_button(pin)
        else:
            empty_label = QLabel("No pins yet. Drop one from the current canvas view to make quick return points.")
            empty_label.setObjectName("pinEmptyState")
            empty_label.setWordWrap(True)
            self.pin_layout.addWidget(empty_label)

        self.add_btn.setEnabled(len(self.pins) < 10)
        self.pin_count_label.setText(f"{len(self.pins)} / 10 pins")

    def _create_pin_button(self, pin):
        palette = get_current_palette()
        pin_widget = QFrame()
        pin_widget.setObjectName("pinEntryCard")

        layout = QHBoxLayout(pin_widget)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)

        btn = QPushButton(pin.title)
        btn.setObjectName("pinEntryButton")
        btn.setIcon(qta.icon('fa5s.map-pin', color=palette.NAV_HIGHLIGHT.name()))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.navigate_to_pin(pin))
        text_column.addWidget(btn)

        if pin.note:
            note_label = QLabel(pin.note)
            note_label.setObjectName("pinEntryNote")
            note_label.setWordWrap(True)
            text_column.addWidget(note_label)

        layout.addLayout(text_column, 1)

        del_btn = QPushButton()
        del_btn.setObjectName("pinEntryDeleteButton")
        del_btn.setIcon(qta.icon('fa5s.times', color='#666666'))
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.remove_pin(pin))
        layout.addWidget(del_btn)

        self.pin_layout.addWidget(pin_widget)

    def create_pin(self):
        if len(self.pins) >= 10:
            return

        scene = self.canvas_view.scene()
        view = self.canvas_view
        center = view.mapToScene(view.viewport().rect().center())

        pin = scene.add_navigation_pin(center)
        self.pins.append(pin)
        self.refresh_pins()

    def remove_pin(self, pin):
        if pin in self.pins:
            scene = self.canvas_view.scene()

            if pin in scene.pins:
                scene.pins.remove(pin)

            if pin.scene() == scene:
                scene.removeItem(pin)

            self.pins.remove(pin)
            self.refresh_pins()

    def navigate_to_pin(self, pin):
        if pin.scene():
            view = self.canvas_view
            view.centerOn(pin)
            pin.setSelected(True)

    def clear_pins(self):
        self.pins.clear()
        self.refresh_pins()

    def update_pin(self, pin):
        if pin in self.pins and pin.scene():
            self.refresh_pins()
            
    def add_pin_button(self, pin):
        if len(self.pins) >= 10 or pin in self.pins:
            return

        if pin.scene():
            self.pins.append(pin)
            self.refresh_pins()

    def show_for_anchor(self, anchor_widget):
        self.on_theme_changed()

        row_count = max(1, min(len(self.pins), 6))
        target_height = 164 + (row_count * 64)
        self.resize(self.BASE_WIDTH, max(236, min(target_height, 452)))

        target_global = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 6))
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

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()


