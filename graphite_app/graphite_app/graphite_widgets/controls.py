"""Floating controls for font and grid configuration.""" 

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from graphite_config import get_current_palette

class FontControl(QWidget):
    fontFamilyChanged = Signal(str)
    fontSizeChanged = Signal(int)
    fontColorChanged = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.canvas = QWidget(self)
        self.canvas.setObjectName("fontControlPanel")
        main_layout = QVBoxLayout(self.canvas)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)
        
        self.canvas.setStyleSheet("""
            QWidget#fontControlPanel {
                background-color: rgba(24, 24, 24, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }
            QLabel, QSlider, QPushButton, QComboBox {
                background-color: transparent;
                border: none;
            }
            QComboBox {
                color: #d0d0d0; font-size: 11px;
                border: 1px solid #555;
                background-color: #4a4a4a;
                border-radius: 4px;
                padding: 4px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #3f3f3f; border: 1px solid #555; }
        """)

        font_label = QLabel("Font", self.canvas)
        font_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc;")
        main_layout.addWidget(font_label)

        self.font_family_combo = QComboBox(self.canvas)
        self.font_family_combo.addItems([
            "Segoe UI", "Arial", "Verdana", "Tahoma", "Consolas",
            "Calibri", "Cambria", "Lucida Grande", "Trebuchet MS",
            "Courier New", "Times New Roman", "Georgia", "System UI",
            "DejaVu Sans", "Segoe UI Variable", "Arial Rounded MT Bold"
        ])
        self.font_family_combo.currentTextChanged.connect(self.fontFamilyChanged.emit)
        main_layout.addWidget(self.font_family_combo)

        self.font_size_slider = QSlider(Qt.Orientation.Horizontal, self.canvas)
        self.font_size_slider.setFixedWidth(160)
        self.font_size_slider.setMinimum(8)
        self.font_size_slider.setMaximum(16)
        self.font_size_slider.setValue(10)
        self.font_size_slider.valueChanged.connect(self.fontSizeChanged.emit)
        self.font_size_slider.setToolTip(f"{self.font_size_slider.value()}pt")
        self.font_size_slider.valueChanged.connect(lambda v: self.font_size_slider.setToolTip(f"{v}pt"))
        self.font_size_slider.setStyleSheet("""
            QSlider::handle:horizontal { background-color: #555555; border-radius: 6px; width: 16px; margin: -6px 0; }
            QSlider::groove:horizontal { background-color: rgba(255, 255, 255, 0.16); height: 4px; border-radius: 2px; }
        """)
        main_layout.addWidget(self.font_size_slider, alignment=Qt.AlignmentFlag.AlignCenter)

        color_presets_layout = QHBoxLayout()
        color_presets_layout.setContentsMargins(0, 0, 0, 0)
        color_presets_layout.setSpacing(10)
        preset_colors = ["#f0f0f0", "#c7c7c7", "#949494", "#6d8599"]
        for color_hex in preset_colors:
            button = QPushButton("", self.canvas)
            button.setFixedSize(32, 20)
            button.setStyleSheet(f"background-color: {color_hex}; border: 2px solid #2d2d2d; border-radius: 5px;")
            button.clicked.connect(lambda checked, c=color_hex: self.fontColorChanged.emit(QColor(c)))
            color_presets_layout.addWidget(button)
        main_layout.addLayout(color_presets_layout)

        self.setFixedSize(200, 160)
        self.canvas.setFixedSize(200, 160)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 0)
        self.canvas.setGraphicsEffect(shadow)

class GridControl(QWidget):
    snapToGridChanged = Signal(bool)
    orthogonalConnectionsChanged = Signal(bool)
    smartGuidesChanged = Signal(bool)
    fadeConnectionsChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid_size = 10
        self.grid_opacity = 0.3
        self.grid_style = "Dots"
        self.grid_color = QColor("#555555")
        
        self.canvas = QWidget(self)
        self.canvas.setObjectName("gridControlPanel")
        main_layout = QVBoxLayout(self.canvas)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        self.on_theme_changed()

        grid_label = QLabel("Grid", self.canvas)
        grid_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc;")
        main_layout.addWidget(grid_label)
        
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal, self.canvas)
        self.opacity_slider.setFixedWidth(160)
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(int(self.grid_opacity * 100))
        self.opacity_slider.valueChanged.connect(self._update_opacity)
        self.opacity_slider.setToolTip(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(
            lambda: self.opacity_slider.setToolTip(f"{self.opacity_slider.value()}%")
        )
        self.opacity_slider.setStyleSheet("""
            QSlider::handle:horizontal { background-color: #555555; border-radius: 6px; width: 16px; margin: -6px 0; }
            QSlider::groove:horizontal { background-color: rgba(0, 0, 0, 0.2); height: 4px; border-radius: 2px; }
        """)
        main_layout.addWidget(self.opacity_slider, alignment=Qt.AlignmentFlag.AlignCenter)
        
        grid_presets_layout = QHBoxLayout()
        grid_presets_layout.setContentsMargins(0, 0, 0, 0)
        grid_presets_layout.setSpacing(12)
        preset_sizes = [(10, "10px"), (20, "20px"), (50, "50px"), (100, "100px")]
        for size, label_text in preset_sizes:
            button = QPushButton(label_text, self.canvas)
            button.setFixedSize(40, 25)
            button.setStyleSheet("""
                QPushButton {
                    color: white; background-color: rgba(63, 63, 63, 0.4);
                    border: none; border-radius: 5px; font-size: 10px; padding: 2px;
                }
                QPushButton:hover { background-color: rgba(85, 85, 85, 0.6); }
                QPushButton:pressed { background-color: rgba(46, 204, 113, 0.3); color: black; }
            """)
            button.clicked.connect(lambda checked, s=size: self._set_grid_size(s))
            grid_presets_layout.addWidget(button)
        main_layout.addLayout(grid_presets_layout)

        style_presets_layout = QHBoxLayout()
        style_presets = [("Dots", "fa5s.ellipsis-h"), ("Lines", "fa5s.grip-lines"), ("Cross", "fa5s.plus")]
        for style, icon_name in style_presets:
            button = QPushButton(qta.icon(icon_name, color='white'), "", self.canvas)
            button.setFixedSize(40, 25)
            button.setStyleSheet("background-color: rgba(63, 63, 63, 0.4); border: none; border-radius: 5px;")
            button.setToolTip(style)
            button.clicked.connect(lambda checked, s=style: self._set_grid_style(s))
            style_presets_layout.addWidget(button)
        main_layout.addLayout(style_presets_layout)

        color_presets_layout = QHBoxLayout()
        color_presets_layout.setContentsMargins(0, 0, 0, 0)
        color_presets_layout.setSpacing(12)
        preset_colors = ["#404040", "#555555", "#2ecc71", "#3498db"]
        for color_hex in preset_colors:
            button = QPushButton("", self.canvas)
            button.setFixedSize(40, 25)
            button.setStyleSheet(f"background-color: {color_hex}; border: 2px solid #2d2d2d; border-radius: 5px;")
            button.clicked.connect(lambda checked, c=color_hex: self._set_grid_color(c))
            color_presets_layout.addWidget(button)
        main_layout.addLayout(color_presets_layout)

        align_label = QLabel("Alignment & Routing", self.canvas)
        align_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        align_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc; margin-top: 10px;")
        main_layout.addWidget(align_label)

        self.snap_grid_checkbox = QCheckBox("Snap to Grid")
        self.snap_grid_checkbox.toggled.connect(self.snapToGridChanged.emit)
        main_layout.addWidget(self.snap_grid_checkbox)

        self.ortho_conn_checkbox = QCheckBox("Orthogonal Connections")
        self.ortho_conn_checkbox.toggled.connect(self.orthogonalConnectionsChanged.emit)
        main_layout.addWidget(self.ortho_conn_checkbox)

        self.smart_guides_checkbox = QCheckBox("Smart Guides")
        self.smart_guides_checkbox.toggled.connect(self.smartGuidesChanged.emit)
        main_layout.addWidget(self.smart_guides_checkbox)

        signals_label = QLabel("Connection Rendering", self.canvas)
        signals_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        signals_label.setStyleSheet("background-color: transparent; border: none; font-size: 10px; font-weight: bold; color: #cccccc; margin-top: 10px;")
        main_layout.addWidget(signals_label)

        self.fade_connections_checkbox = QCheckBox("Faded Connections")
        self.fade_connections_checkbox.setToolTip("Keep connections quiet until you hover them.")
        self.fade_connections_checkbox.toggled.connect(self.fadeConnectionsChanged.emit)
        main_layout.addWidget(self.fade_connections_checkbox)
        
        self.setFixedSize(200, 360)
        self.canvas.setFixedSize(200, 360)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 0)
        self.canvas.setGraphicsEffect(shadow)

    def on_theme_changed(self):
        palette = get_current_palette()
        selection_color = palette.SELECTION.name()
        selection_border = palette.SELECTION.darker(110).name()

        self.canvas.setStyleSheet(f"""
            QWidget#gridControlPanel {{
                background-color: rgba(24, 24, 24, 0.9);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }}
            QLabel, QSlider, QPushButton, QCheckBox {{
                background-color: transparent;
                border: none;
            }}
            QCheckBox {{
                color: #cccccc;
                font-size: 11px;
            }}
            QCheckBox::indicator {{ width: 16px; height: 16px; }}
            QCheckBox::indicator:unchecked {{
                border: 1px solid #555; background-color: #3f3f3f; border-radius: 4px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {selection_color}; border: 1px solid {selection_border};
                image: url(C:/Users/Admin/source/repos/graphite_app/assets/check.png);
                border-radius: 4px;
            }}
        """)
        
    def _update_opacity(self, value):
        self.grid_opacity = value / 100.0
        if self.parent():
            self.parent().update()
            
    def _set_grid_size(self, size):
        self.grid_size = size
        if self.parent():
            self.parent().update()

    def _set_grid_style(self, style):
        self.grid_style = style
        if self.parent():
            self.parent().update()

    def _set_grid_color(self, color_hex):
        self.grid_color = QColor(color_hex)
        if self.parent():
            self.parent().update()


