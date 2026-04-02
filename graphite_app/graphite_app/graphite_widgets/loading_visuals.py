"""Shared loading animation painting helpers."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from graphite_config import get_current_palette


def paint_orbital_loading_spinner(painter, rect, angle1, angle2, angle3):
    """Paints the shared Graphite splash/loading spinner."""
    palette = get_current_palette()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    bounds = QRectF(rect)
    cx = bounds.center().x()
    cy = bounds.center().y()
    spinner_radius = min(bounds.width(), bounds.height()) * 0.5

    # Inner orbital dots.
    painter.setPen(Qt.PenStyle.NoPen)
    inner_radius = spinner_radius * 0.225
    for i in range(3):
        offset_deg = i * 120
        rad = math.radians(-angle3 * 1.5 + offset_deg)
        x = cx + inner_radius * math.cos(rad)
        y = cy + inner_radius * math.sin(rad)

        color = QColor(palette.NAV_HIGHLIGHT)
        color.setAlpha(200)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(x, y), spinner_radius * 0.0625, spinner_radius * 0.0625)

    # Middle morphing ring.
    path = QPainterPath()
    base_radius = spinner_radius * 0.425
    amplitude = spinner_radius * 0.0625
    frequency = 4

    morph_phase = math.radians(angle1 * 1.5)
    rotation_phase = math.radians(angle1)

    for i in range(361):
        rad = math.radians(i)
        radius = base_radius + amplitude * math.sin(frequency * rad + morph_phase)
        draw_rad = rad + rotation_phase

        x = cx + radius * math.cos(draw_rad)
        y = cy + radius * math.sin(draw_rad)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)

    painter.setPen(
        QPen(
            palette.USER_NODE,
            max(1.8, spinner_radius * 0.0625),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)

    # Outer comet ring.
    painter.setPen(Qt.PenStyle.NoPen)
    num_dots = 18
    outer_radius = spinner_radius * 0.675
    tail_length = 240

    for i in range(num_dots):
        dot_angle_deg = i * (360 / num_dots)
        diff = (angle2 - dot_angle_deg) % 360

        if diff >= tail_length:
            continue

        progress = 1.0 - (diff / tail_length)
        eased = progress ** 1.8

        opacity = int(eased * 255)
        size = spinner_radius * (0.025 + (eased * 0.0875))

        rad = math.radians(dot_angle_deg)
        x = cx + outer_radius * math.cos(rad)
        y = cy + outer_radius * math.sin(rad)

        color = QColor(palette.SELECTION)
        color.setAlpha(opacity)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(x, y), size, size)

    painter.restore()
