from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout
from PySide6.QtCore import Signal
import markdown
import qtawesome as qta
from graphlink_config import get_current_palette


class DocumentViewerPanel(QWidget):
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(500)
        self.setObjectName("documentViewerPanel")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        icon_label = QLabel()
        icon_label.setPixmap(qta.icon('fa5s.book-open', color='white').pixmap(16, 16))
        header_layout.addWidget(icon_label)

        title_label = QLabel("Document View")
        title_label.setStyleSheet("color: white; font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        close_button = QPushButton(qta.icon('fa5s.times', color='white'), "")
        close_button.setFixedSize(24, 24)
        close_button.setToolTip("Close Panel")
        close_button.clicked.connect(self.close_requested.emit)
        header_layout.addWidget(close_button)
        main_layout.addWidget(header_widget)

        self.content_viewer = QTextEdit()
        self.content_viewer.setReadOnly(True)
        main_layout.addWidget(self.content_viewer)

        self.on_theme_changed()

    def set_document_content(self, markdown_text):
        html = markdown.markdown(markdown_text, extensions=['fenced_code', 'tables'])
        self.content_viewer.setHtml(html)

    def on_theme_changed(self):
        palette = get_current_palette()
        self.setStyleSheet(f"""
            QWidget#documentViewerPanel {{
                background-color: #252525;
                border-right: 1px solid #3F3F3F;
            }}
            QTextEdit {{
                background-color: #2D2D2D;
                border: 1px solid #3F3F3F;
                color: #E0E0E0;
                font-size: 13px;
                padding: 8px;
            }}
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3F3F3F;
            }}
        """)
