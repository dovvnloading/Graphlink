"""Tooltip-related widget helpers."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

class CustomTooltip(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("""
            QLabel {
                background-color: rgba(30, 30, 30, 0.9);
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px;
                font-size: 11px;
            }
        """)


