"""Search and loading overlay widgets."""

import qtawesome as qta
from PySide6.QtCore import QPointF, Property, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QFontMetrics, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import QGraphicsObject, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget
from graphite_config import get_current_palette, is_monochrome_theme
from .loading_visuals import paint_orbital_loading_spinner

class LoadingAnimation(QGraphicsObject):
    def __init__(self, diameter=56.0, parent=None):
        super().__init__(parent)
        self.setZValue(100) 
        self.diameter = max(36.0, float(diameter))
        self._angle1 = 0.0
        self._angle2 = 0.0
        self._angle3 = 0.0

        self.anim1 = QPropertyAnimation(self, b'angle1')
        self.anim1.setStartValue(0)
        self.anim1.setEndValue(360)
        self.anim1.setDuration(1200)
        self.anim1.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.anim2 = QPropertyAnimation(self, b'angle2')
        self.anim2.setStartValue(70)
        self.anim2.setEndValue(430)
        self.anim2.setDuration(1000)
        self.anim2.setEasingCurve(QEasingCurve.Type.InOutSine)

        self.anim3 = QPropertyAnimation(self, b'angle3')
        self.anim3.setStartValue(140)
        self.anim3.setEndValue(500)
        self.anim3.setDuration(1500)
        self.anim3.setEasingCurve(QEasingCurve.Type.InOutQuad)
        
        self.anim_group = QParallelAnimationGroup()
        self.anim_group.addAnimation(self.anim1)
        self.anim_group.addAnimation(self.anim2)
        self.anim_group.addAnimation(self.anim3)
        self.anim_group.setLoopCount(-1)

    @property
    def radius(self):
        return self.diameter * 0.5

    def boundingRect(self):
        return QRectF(-self.radius, -self.radius, self.diameter, self.diameter)

    def paint(self, painter, option, widget):
        paint_orbital_loading_spinner(
            painter,
            self.boundingRect(),
            self._angle1,
            self._angle2,
            self._angle3,
        )

    def start(self):
        self.anim_group.start()

    def stop(self):
        self.anim_group.stop()

    @Property(float)
    def angle1(self):
        return self._angle1

    @angle1.setter
    def angle1(self, value):
        self._angle1 = value
        self.update()

    @Property(float)
    def angle2(self):
        return self._angle2

    @angle2.setter
    def angle2(self, value):
        self._angle2 = value
        self.update()

    @Property(float)
    def angle3(self):
        return self._angle3

    @angle3.setter
    def angle3(self, value):
        self._angle3 = value
        self.update()


class GhostNodePreview(QGraphicsObject):
    """A transient placeholder that reserves and visualizes a pending node spawn."""

    BORDER_RADIUS = 12
    HEADER_HEIGHT = 34
    PADDING = 15
    CONTROL_GUTTER = 34

    def __init__(self, width=420, height=128, title="Generating response", subtitle=None, parent=None):
        super().__init__(parent)
        self.width = max(220.0, float(width))
        self.height = max(88.0, float(height))
        self.title = title
        self.subtitle = subtitle
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(90)
        spinner_diameter = min(92.0, max(64.0, self.height * 0.52))
        self._spinner = LoadingAnimation(diameter=spinner_diameter, parent=self)
        self._spinner.setZValue(1)
        self._spinner.start()
        self._layout_loading_visual()

    def boundingRect(self):
        return QRectF(-8, -8, self.width + 16, self.height + 16)

    def _layout_loading_visual(self):
        panel_rect = self._content_panel_rect()
        spinner_center = QPointF(panel_rect.left() + 34.0, panel_rect.center().y())
        self._spinner.setPos(spinner_center)

    def _mix_color(self, base, accent, ratio):
        base_color = QColor(base)
        accent_color = QColor(accent)
        mix = max(0.0, min(1.0, float(ratio)))
        return QColor(
            round(base_color.red() + (accent_color.red() - base_color.red()) * mix),
            round(base_color.green() + (accent_color.green() - base_color.green()) * mix),
            round(base_color.blue() + (accent_color.blue() - base_color.blue()) * mix),
        )

    def _surface_colors(self):
        palette = get_current_palette()
        accent = QColor(palette.AI_NODE)
        monochrome = is_monochrome_theme()

        body_start = self._mix_color(QColor("#25282d"), accent, 0.04 if not monochrome else 0.02)
        body_start.setAlpha(210)
        body_end = self._mix_color(QColor("#171a1f"), accent, 0.02 if not monochrome else 0.01)
        body_end.setAlpha(192)
        header_start = self._mix_color(QColor("#2c333b"), accent, 0.30 if not monochrome else 0.08)
        header_start.setAlpha(188)
        header_end = self._mix_color(QColor("#181c22"), accent, 0.18 if not monochrome else 0.04)
        header_end.setAlpha(170)
        badge_fill = self._mix_color(QColor("#262c33"), accent, 0.58 if not monochrome else 0.12)
        badge_fill.setAlpha(138)
        descriptor_text = self._mix_color(QColor("#9ca6b0"), accent, 0.14 if not monochrome else 0.04)
        descriptor_text.setAlpha(175)
        content_panel_border = self._mix_color(QColor("#343a42"), accent, 0.08 if not monochrome else 0.03)
        content_panel_border.setAlpha(135)

        return {
            "accent": accent,
            "body_start": body_start,
            "body_end": body_end,
            "header_start": header_start,
            "header_end": header_end,
            "badge_fill": badge_fill,
            "badge_text": QColor("#eef7ff"),
            "descriptor_text": descriptor_text,
            "content_panel_fill": QColor(17, 20, 23, 182),
            "content_panel_border": content_panel_border,
        }

    def _content_panel_rect(self):
        content_area_width = self.width - (self.PADDING * 2) - self.CONTROL_GUTTER
        return QRectF(
            self.PADDING - 2,
            self.HEADER_HEIGHT + 6,
            content_area_width + 4,
            max(20, self.height - self.HEADER_HEIGHT - 12),
        )

    def stop_animation(self):
        self._spinner.stop()

    def paint(self, painter, option, widget):
        colors = self._surface_colors()
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

            body_rect = QRectF(0, 0, self.width, self.height)

            shadow_path = QPainterPath()
            shadow_path.addRoundedRect(3, 4, self.width, self.height, self.BORDER_RADIUS, self.BORDER_RADIUS)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 34))
            painter.drawPath(shadow_path)

            node_path = QPainterPath()
            node_path.addRoundedRect(body_rect, self.BORDER_RADIUS, self.BORDER_RADIUS)

            gradient = QLinearGradient(QPointF(0, 0), QPointF(0, self.height))
            gradient.setColorAt(0, colors["body_start"])
            gradient.setColorAt(1, colors["body_end"])
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(node_path)

            painter.save()
            painter.setClipPath(node_path)
            accent_fill = QColor(colors["accent"])
            accent_fill.setAlpha(116)
            accent_top = self.HEADER_HEIGHT + 1
            accent_height = max(0.0, self.height - accent_top - 1)
            if accent_height > 0:
                painter.setBrush(accent_fill)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRect(QRectF(0, accent_top, 5, accent_height))
            painter.restore()

            header_rect = QRectF(0, 0, self.width, self.HEADER_HEIGHT)
            header_path = QPainterPath()
            corner_radius = min(self.BORDER_RADIUS, self.HEADER_HEIGHT, self.width / 2)
            header_path.moveTo(header_rect.left(), header_rect.bottom())
            header_path.lineTo(header_rect.left(), header_rect.top() + corner_radius)
            header_path.quadTo(header_rect.left(), header_rect.top(), header_rect.left() + corner_radius, header_rect.top())
            header_path.lineTo(header_rect.right() - corner_radius, header_rect.top())
            header_path.quadTo(header_rect.right(), header_rect.top(), header_rect.right(), header_rect.top() + corner_radius)
            header_path.lineTo(header_rect.right(), header_rect.bottom())
            header_path.closeSubpath()

            header_gradient = QLinearGradient(QPointF(0, 0), QPointF(0, self.HEADER_HEIGHT))
            header_gradient.setColorAt(0, colors["header_start"])
            header_gradient.setColorAt(1, colors["header_end"])
            painter.setBrush(QBrush(header_gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(header_path)

            painter.setPen(QPen(colors["content_panel_border"], 1))
            painter.drawLine(
                QPointF(10, self.HEADER_HEIGHT),
                QPointF(self.width - 10, self.HEADER_HEIGHT),
            )

            badge_font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            badge_text = "Assistant"
            badge_metrics = QFontMetrics(badge_font)
            badge_width = badge_metrics.horizontalAdvance(badge_text) + 18
            badge_rect = QRectF(12, 8, badge_width, 18)
            badge_path = QPainterPath()
            badge_path.addRoundedRect(badge_rect, 9, 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors["badge_fill"])
            painter.drawPath(badge_path)

            badge_text_color = QColor(colors["badge_text"])
            badge_text_color.setAlpha(214)
            painter.setPen(badge_text_color)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

            descriptor_font = QFont("Segoe UI", 8)
            painter.setFont(descriptor_font)
            painter.setPen(colors["descriptor_text"])
            painter.drawText(
                QRectF(badge_rect.right() + 10, 0, self.width - badge_rect.right() - 60, self.HEADER_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "Reply pending",
            )

            content_rect = self._content_panel_rect()
            painter.setBrush(colors["content_panel_fill"])
            content_border = QColor(colors["content_panel_border"])
            content_border.setAlpha(125)
            painter.setPen(QPen(content_border, 1))
            painter.drawRoundedRect(content_rect, 10, 10)

            text_left = content_rect.left() + 68
            text_width = max(80.0, content_rect.width() - 84.0)

            title_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
            painter.setFont(title_font)
            title_color = QColor("#eef6ff")
            title_color.setAlpha(220)
            painter.setPen(title_color)
            painter.drawText(
                QRectF(text_left, content_rect.top() + 13, text_width, 20),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.title,
            )

            if self.subtitle:
                subtitle_font = QFont("Segoe UI", 8)
                painter.setFont(subtitle_font)
                subtitle_color = QColor("#aab8c8")
                subtitle_color.setAlpha(165)
                painter.setPen(subtitle_color)
                painter.drawText(
                    QRectF(text_left, content_rect.top() + 34, text_width, 18),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    self.subtitle,
                )

            outline_color = QColor(colors["accent"].lighter(105))
            outline_color.setAlpha(135)
            painter.setPen(QPen(outline_color, 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(node_path)
        finally:
            painter.restore()

class SearchOverlay(QWidget):
    textChanged = Signal(str)
    findNext = Signal()
    findPrevious = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(300)
        self.setObjectName("searchOverlay")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find...")
        self.search_input.textChanged.connect(self.textChanged.emit)
        self.search_input.returnPressed.connect(self.findNext.emit)
        layout.addWidget(self.search_input)

        self.results_label = QLabel("0 / 0")
        layout.addWidget(self.results_label)

        prev_btn = QPushButton(qta.icon('fa5s.chevron-up', color='white'), "")
        prev_btn.setFixedSize(24, 24)
        prev_btn.setToolTip("Previous match (Shift+Enter)")
        prev_btn.clicked.connect(self.findPrevious.emit)
        layout.addWidget(prev_btn)
        
        QShortcut(QKeySequence("Shift+Return"), self.search_input, self.findPrevious.emit)
        QShortcut(QKeySequence("Shift+Enter"), self.search_input, self.findPrevious.emit)

        next_btn = QPushButton(qta.icon('fa5s.chevron-down', color='white'), "")
        next_btn.setFixedSize(24, 24)
        next_btn.setToolTip("Next match (Enter)")
        next_btn.clicked.connect(self.findNext.emit)
        layout.addWidget(next_btn)
        
        close_btn = QPushButton(qta.icon('fa5s.times', color='white'), "")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.closed.emit)
        layout.addWidget(close_btn)
        
        QShortcut(QKeySequence("Esc"), self, self.closed.emit)

        self.setStyleSheet("""
            QWidget#searchOverlay {
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 5px;
            }
            QLabel { color: #ccc; background-color: transparent; }
            QLineEdit {
                border: 1px solid #555;
                background-color: #3f3f3f;
                border-radius: 3px;
                padding: 4px;
            }
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)

    def update_results_label(self, current, total):
        self.results_label.setText(f"{current} / {total}")
        if total > 0 and current > 0:
            self.results_label.setStyleSheet("color: #fff;")
        elif total > 0 and current == 0:
             self.results_label.setStyleSheet("color: #ccc;")
        else:
            self.results_label.setStyleSheet("color: #e74c3c;")

    def focus_input(self):
        self.search_input.setFocus()
        self.search_input.selectAll()


