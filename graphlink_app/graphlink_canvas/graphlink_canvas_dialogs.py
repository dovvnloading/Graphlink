import qtawesome as qta
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QWidget, QHBoxLayout, QLineEdit, QLabel, QPushButton,
    QGraphicsDropShadowEffect, QTextEdit, QGridLayout, QApplication
)
from graphlink_config import get_current_palette, get_semantic_color

class ColorPickerDialog(QDialog):
    """
    A small, frameless pop-up dialog for selecting a color from a predefined palette.
    It automatically closes when the user clicks outside of it.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)

        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(15, 15, 15, 15)

        self.container = QWidget(self)
        self.container.setObjectName("colorPickerContainer")
        dialog_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 190))
        shadow.setOffset(0, 2)
        self.container.setGraphicsEffect(shadow)
        
        main_layout = QVBoxLayout(self.container)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        title_label = QLabel("Style")
        title_label.setObjectName("colorPickerTitle")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        close_btn = QPushButton()
        close_btn.setObjectName("colorPickerCloseButton")
        close_btn.setFixedSize(24, 24)
        close_btn.setIcon(qta.icon('fa5s.times', color='white'))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)

        main_layout.addLayout(header_layout)

        default_btn = QPushButton("Reset to Default")
        default_btn.setIcon(qta.icon('fa5s.undo', color='white'))
        default_btn.clicked.connect(lambda: self.color_selected(None, "default"))
        main_layout.addWidget(default_btn)

        def create_section(title, color_type, names_list):
            label = QLabel(title)
            label.setStyleSheet("color: #CCCCCC; font-size: 10px; margin-top: 5px;")
            main_layout.addWidget(label)
            
            grid_layout = QGridLayout()
            grid_layout.setSpacing(8)
            col, row = 0, 0
            
            frame_colors = get_current_palette().FRAME_COLORS
            
            for name in names_list:
                color_data = frame_colors[name]
                btn = QPushButton()
                btn.setFixedSize(28, 28)
                btn.setToolTip(name)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                
                style = f"""
                    QPushButton {{ background-color: {color_data["color"]}; border: 2px solid #3F3F3F; border-radius: 14px; }}
                    QPushButton:hover {{ border: 2px solid #FFFFFF; }}
                """
                if color_type == "header":
                    style = f"""
                        QPushButton {{
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {color_data["color"]}, stop:0.4 {color_data["color"]},
                                stop:0.41 #3F3F3F, stop:1 #3F3F3F);
                            border: 2px solid #3F3F3F; border-radius: 14px;
                        }}
                        QPushButton:hover {{ border: 2px solid #FFFFFF; }}
                    """
                btn.setStyleSheet(style)
                btn.clicked.connect(lambda checked, c=color_data: self.color_selected(c["color"], c["type"]))
                
                grid_layout.addWidget(btn, row, col)
                col = (col + 1) % 5
                if col == 0: row += 1
            main_layout.addLayout(grid_layout)

        frame_colors = get_current_palette().FRAME_COLORS
        full_color_names = [k for k, v in frame_colors.items() if v['type'] == 'full' and 'Gray' not in k]
        header_color_names = [k for k, v in frame_colors.items() if v['type'] == 'header']
        mono_color_names = [k for k, v in frame_colors.items() if 'Gray' in k]
        
        create_section("Frame Colors", "full", full_color_names)
        create_section("Header Colors Only", "header", header_color_names)
        create_section("Monochrome", "full", mono_color_names)
        
        main_layout.addStretch()
        
        self.setStyleSheet("""
            QDialog { background: transparent; }
            QWidget#colorPickerContainer { background-color: #252525; border-radius: 8px; }
            QLabel#colorPickerTitle { color: #FFFFFF; font-size: 12px; font-weight: bold; }
            QPushButton { background-color: #3F3F3F; border-radius: 5px; padding: 8px; }
            QPushButton:hover { background-color: #555555; }
            QPushButton#colorPickerCloseButton {
                background-color: transparent;
                border-radius: 12px;
                padding: 0px;
            }
            QPushButton#colorPickerCloseButton:hover {
                background-color: #4A4A4A;
            }
        """)
        
        self.selected_color = None
        self.selected_type = None
        
    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if self.isVisible() and event.type() == QEvent.Type.MouseButtonPress:
            if not self.container.geometry().contains(self.mapFromGlobal(event.globalPos())):
                self.close()
                return False
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and self.isVisible() and not self.isActiveWindow():
            self.close()
        super().changeEvent(event)
        
    def color_selected(self, color, color_type):
        self.selected_color = color
        self.selected_type = color_type
        self.accept()
        
    def get_selected_color(self):
        return self.selected_color, self.selected_type

from graphlink_widgets.pins import NavigationPinEditor as PinEditDialog
