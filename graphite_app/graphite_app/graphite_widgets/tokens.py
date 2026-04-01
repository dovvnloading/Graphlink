"""Token estimation and display widgets."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGridLayout, QLabel, QVBoxLayout, QWidget

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

class TokenEstimator:
    _instance = None
    _encoding = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TokenEstimator, cls).__new__(cls)
            if TIKTOKEN_AVAILABLE:
                try:
                    cls._encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    cls._encoding = None
                    print("Warning: tiktoken installed, but failed to get encoding. Falling back to character count.")
        return cls._instance

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoding:
            return len(self._encoding.encode(text))
        else:
            return len(text) // 4

class TokenCounterWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tokenCounterWidget")
        self.setFixedWidth(150)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)

        self.container = QWidget(self)
        self.container.setObjectName("tokenCounterContainer")
        main_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 1)
        self.container.setGraphicsEffect(shadow)

        grid_layout = QGridLayout(self.container)
        grid_layout.setContentsMargins(8, 6, 8, 6)
        grid_layout.setSpacing(4)

        grid_layout.addWidget(QLabel("Input:"), 0, 0)
        grid_layout.addWidget(QLabel("Output:"), 1, 0)
        grid_layout.addWidget(QLabel("Context:"), 2, 0)
        grid_layout.addWidget(QLabel("Total:"), 3, 0)

        self.input_label = QLabel("0")
        self.output_label = QLabel("0")
        self.context_label = QLabel("0")
        self.total_label = QLabel("0")

        for label in [self.input_label, self.output_label, self.context_label, self.total_label]:
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        grid_layout.addWidget(self.input_label, 0, 1)
        grid_layout.addWidget(self.output_label, 1, 1)
        grid_layout.addWidget(self.context_label, 2, 1)
        grid_layout.addWidget(self.total_label, 3, 1)

        self.setStyleSheet("""
            QWidget#tokenCounterWidget {
                background-color: transparent;
            }
            QWidget#tokenCounterContainer {
                background-color: rgba(45, 45, 45, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
            }
            QLabel {
                background-color: transparent;
                color: #cccccc;
                font-size: 10px;
            }
        """)

    def update_counts(self, input_tokens=None, output_tokens=None, context_tokens=None, total_tokens=None):
        if input_tokens is not None:
            self.input_label.setText(f"{input_tokens:,}")
        if output_tokens is not None:
            self.output_label.setText(f"{output_tokens:,}")
        if context_tokens is not None:
            self.context_label.setText(f"{context_tokens:,}")
        if total_tokens is not None:
            self.total_label.setText(f"{total_tokens:,}")
    
    def reset(self):
        self.update_counts(0, 0, 0, 0)


