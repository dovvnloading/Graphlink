"""UI-refactor P1: the shared native dialog shell (title + close + content).

Audit finding B5: Settings shipped with NO close affordance and no title.
The P1 policy is "mandatory close button + title on dialogs" - this frame is
that policy as a widget. Library already had its own hand-built shell;
Settings (and any future embedded dialog) wraps its content in this instead
of inventing another one-off.

Token-styled throughout (P0): every color/radius/size comes from
get_surface_color()/RADIUS_PX/TEXT_PX/SPACE_PX/ELEVATION_PARAMS - no
literals. Elevation level 3 matches the dialog tier of the shadow scale.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from graphlink_config import get_surface_color
from graphlink_styles import ELEVATION_PARAMS, RADIUS_PX, SPACE_PX, TEXT_PX


class DialogFrame(QFrame):
    close_requested = Signal()

    def __init__(self, title, content_widget, parent=None):
        super().__init__(parent)
        self.setObjectName("glDialogFrame")

        # NO QGraphicsDropShadowEffect here, deliberately: a QGraphicsEffect
        # on an ancestor forces the whole subtree through a software render
        # path that QWebEngineView (GPU-composited) cannot join - the web
        # content renders as a SOLID BLACK RECTANGLE. Found live in the P1
        # drive; the same effect on ChatLibraryDialog's shell was the entire
        # cause of the audit's "library body is a black void" finding (B7).
        # Elevation for webview-bearing dialogs comes from the scrim contrast
        # + border until a compositor-safe shadow technique lands (P9).

        layout = QVBoxLayout(self)
        margin = SPACE_PX[4]
        layout.setContentsMargins(margin, SPACE_PX[3], margin, margin)
        layout.setSpacing(SPACE_PX[3])

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(SPACE_PX[2])

        self.title_label = QLabel(title)
        self.title_label.setObjectName("glDialogTitle")
        header.addWidget(self.title_label)
        header.addStretch()

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("glDialogClose")
        self.close_button.setFixedSize(SPACE_PX[6] + SPACE_PX[1], SPACE_PX[6] + SPACE_PX[1])
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close_requested.emit)
        header.addWidget(self.close_button)

        layout.addLayout(header)
        layout.addWidget(content_widget, 1)

        self.setStyleSheet(f"""
            QFrame#glDialogFrame {{
                background-color: {get_surface_color("node_body")};
                border: 1px solid {get_surface_color("border")};
                border-radius: {RADIUS_PX["lg"]}px;
            }}
            QLabel#glDialogTitle {{
                color: {get_surface_color("text_strong")};
                font-size: {TEXT_PX["lg"]}px;
                font-weight: 600;
                background: transparent;
            }}
            QPushButton#glDialogClose {{
                background-color: transparent;
                color: {get_surface_color("text_label")};
                border: none;
                border-radius: {RADIUS_PX["sm"]}px;
                font-size: {TEXT_PX["base"]}px;
            }}
            QPushButton#glDialogClose:hover {{
                background-color: {get_surface_color("border")};
                color: {get_surface_color("text_strong")};
            }}
        """)

    def center_in_parent(self):
        """Center inside the parent widget and clamp so no pixel leaves it
        (audit B4: Settings used to overflow the window onto the desktop)."""
        parent = self.parentWidget()
        if parent is None:
            return
        margin = SPACE_PX[4]
        width = min(self.width(), parent.width() - 2 * margin)
        height = min(self.height(), parent.height() - 2 * margin)
        self.resize(max(width, 0), max(height, 0))
        x = (parent.width() - self.width()) // 2
        y = (parent.height() - self.height()) // 2
        self.move(max(margin, x), max(margin, y))
