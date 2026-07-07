import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen


LOD_FULL_THRESHOLD = 0.72
LOD_SUMMARY_THRESHOLD = 0.3
LOD_PROXY_THRESHOLD = 0.62
LOD_PROXY_INTERACTIVE_THRESHOLD = 0.42
LOD_MODE_HYSTERESIS = 0.04


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _resolved_zoom(painter=None, fallback=1.0):
    if painter is not None:
        transform = painter.worldTransform()
        scale = max(abs(transform.m11()), abs(transform.m22()))
        if scale > 0:
            return max(0.01, scale)
    return max(0.01, float(fallback))


def _scaled_font(family, base_size, *, scale=1.0, weight=QFont.Weight.Normal, max_scale=3.0):
    font = QFont(family)
    font.setWeight(weight)
    font.setPointSizeF(base_size * _clamp(scale, 1.0, max_scale))
    return font


def _fit_font_to_height(font, max_height, *, min_point_size=7.0):
    fitted_font = QFont(font)
    point_size = fitted_font.pointSizeF()
    max_height = float(max_height)

    if max_height <= 0 or point_size <= 0:
        return fitted_font

    while point_size > min_point_size:
        if QFontMetrics(fitted_font).height() <= max_height:
            break
        point_size -= 0.5
        fitted_font.setPointSizeF(point_size)

    return fitted_font


def _screen_space_scene_height(target_pixels, zoom):
    return float(target_pixels) / max(0.01, float(zoom))


def _lod_font_height_limit(available_height, zoom, *, scene_fraction, target_pixels, minimum=0.0):
    # Size LoD text against the current zoom so the zoomed-out fallback stays
    # materially readable on screen instead of collapsing back to tiny scene-space caps.
    return max(
        float(minimum),
        min(
            float(available_height) * float(scene_fraction),
            _screen_space_scene_height(target_pixels, zoom),
        ),
    )


def _detail_text_scale(zoom, mode):
    if mode == "glyph":
        return _clamp(1.08 / zoom, 1.0, 3.3)
    if mode == "summary":
        return _clamp(0.92 / zoom, 1.0, 3.0)
    return 1.0


def current_view_zoom(item):
    scene = item.scene() if item else None
    if not scene or not scene.views():
        return 1.0

    transform = scene.views()[0].transform()
    return max(0.01, abs(transform.m11()))


def lod_mode_for_zoom(zoom, previous_mode=None):
    zoom = max(0.01, float(zoom))
    previous_mode = str(previous_mode or "").lower()

    if previous_mode == "full" and zoom >= (LOD_FULL_THRESHOLD - LOD_MODE_HYSTERESIS):
        return "full"
    if previous_mode == "glyph" and zoom < (LOD_SUMMARY_THRESHOLD + LOD_MODE_HYSTERESIS):
        return "glyph"
    if previous_mode == "summary":
        if zoom >= (LOD_FULL_THRESHOLD + LOD_MODE_HYSTERESIS):
            return "full"
        if zoom < (LOD_SUMMARY_THRESHOLD - LOD_MODE_HYSTERESIS):
            return "glyph"
        return "summary"

    if zoom >= LOD_FULL_THRESHOLD:
        return "full"
    if zoom >= LOD_SUMMARY_THRESHOLD:
        return "summary"
    return "glyph"


def lod_mode_for_item(item, zoom=None):
    resolved_zoom = current_view_zoom(item) if zoom is None else zoom
    previous_mode = getattr(item, "_lod_mode_cache", None)
    mode = lod_mode_for_zoom(resolved_zoom, previous_mode=previous_mode)
    if item is not None:
        item._lod_mode_cache = mode
        item._render_lod_zoom = resolved_zoom
    return mode


def current_view_scene_rect(item):
    scene = item.scene() if item else None
    if not scene or not scene.views():
        return None

    view = scene.views()[0]
    return view.mapToScene(view.viewport().rect()).boundingRect()


def preview_text(*parts, fallback="", limit=280):
    for part in parts:
        if part is None:
            continue
        normalized = re.sub(r"\s+", " ", str(part)).strip()
        if normalized:
            return normalized[:limit]
    return fallback


def _wrapped_elided_lines(text, font_metrics, max_width, max_lines):
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized or max_width <= 0 or max_lines <= 0:
        return []

    words = normalized.split(" ")
    lines = []
    width_limit = int(max(1.0, float(max_width)))
    index = 0

    while index < len(words) and len(lines) < max_lines:
        current = words[index]
        index += 1

        if font_metrics.horizontalAdvance(current) > width_limit:
            current = font_metrics.elidedText(current, Qt.TextElideMode.ElideRight, width_limit)

        while index < len(words):
            candidate = f"{current} {words[index]}"
            if font_metrics.horizontalAdvance(candidate) > width_limit:
                break
            current = candidate
            index += 1

        if len(lines) == max_lines - 1 and index < len(words):
            remainder = " ".join([current] + words[index:])
            lines.append(font_metrics.elidedText(remainder, Qt.TextElideMode.ElideRight, width_limit))
            return lines

        lines.append(font_metrics.elidedText(current, Qt.TextElideMode.ElideRight, width_limit))

    return lines


def _draw_elided_lines(
    painter,
    rect,
    text,
    *,
    font,
    color,
    max_lines,
    line_gap=0.0,
    alignment=Qt.AlignmentFlag.AlignLeft,
):
    if rect.width() <= 0 or rect.height() <= 0 or max_lines <= 0:
        return 0.0

    metrics = QFontMetrics(font)
    line_height = max(1.0, float(metrics.lineSpacing()))
    line_gap = max(0.0, float(line_gap))
    drawable_lines = max(1, int((rect.height() + line_gap) / max(1.0, line_height + line_gap)))
    line_limit = min(int(max_lines), drawable_lines)
    lines = _wrapped_elided_lines(text, metrics, rect.width(), line_limit)
    if not lines:
        return 0.0

    painter.setFont(font)
    painter.setPen(QColor(color))

    y = rect.top()
    for line in lines:
        line_rect = QRectF(rect.left(), y, rect.width(), line_height + 2.0)
        painter.drawText(line_rect, alignment | Qt.AlignmentFlag.AlignVCenter, line)
        y += line_height + line_gap

    return max(0.0, y - rect.top() - line_gap)


def initials_for_title(title, fallback="N"):
    words = re.findall(r"[A-Za-z0-9]+", str(title or ""))
    if not words:
        return fallback
    initials = "".join(word[0].upper() for word in words[:2]).strip()
    return initials or fallback


def sync_proxy_render_state(item, view_rect=None, zoom=None):
    zoom = current_view_zoom(item) if zoom is None else max(0.01, float(zoom))
    view_rect = current_view_scene_rect(item) if view_rect is None else view_rect

    mode = lod_mode_for_zoom(zoom, previous_mode=getattr(item, "_render_lod_mode", None))
    is_collapsed = bool(getattr(item, "is_collapsed", False))
    hovered = bool(getattr(item, "hovered", False))
    selected = bool(item.isSelected()) if hasattr(item, "isSelected") else False

    near_view = True
    if view_rect is not None:
        margin = max(180.0, 260.0 / zoom)
        expanded_rect = view_rect.adjusted(-margin, -margin, margin, margin)
        near_view = item.sceneBoundingRect().intersects(expanded_rect)

    proxy = getattr(item, "proxy", None)
    show_proxy = bool(
        proxy
        and not is_collapsed
        and near_view
        and (
            zoom >= LOD_PROXY_THRESHOLD
            or ((hovered or selected) and zoom >= LOD_PROXY_INTERACTIVE_THRESHOLD)
        )
    )

    if proxy is not None:
        if proxy.isVisible() != show_proxy:
            proxy.setVisible(show_proxy)
        if proxy.isEnabled() != show_proxy:
            proxy.setEnabled(show_proxy)

    widget = getattr(item, "widget", None)
    if widget is not None and hasattr(widget, "setUpdatesEnabled"):
        widget.setUpdatesEnabled(show_proxy)

    item._render_lod_zoom = zoom
    item._render_lod_mode = "full" if show_proxy and not is_collapsed else mode
    item._lod_mode_cache = item._render_lod_mode
    item._render_lod_near_view = near_view
    return item._render_lod_mode


def draw_lod_card(
    painter,
    rect,
    *,
    accent,
    selection_color,
    title,
    subtitle="",
    preview="",
    badge="",
    mode="summary",
    selected=False,
    hovered=False,
    search_match=False,
    navigation_highlight=False,
    connection_radius=0,
    border_radius=12,
    zoom=None,
):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    accent = QColor(accent)
    selection_color = QColor(selection_color)
    panel_rect = QRectF(rect)
    zoom = _resolved_zoom(painter, fallback=zoom if zoom is not None else 1.0)
    detail_scale = _detail_text_scale(zoom, mode)

    shadow_path = QPainterPath()
    shadow_path.addRoundedRect(panel_rect.adjusted(3, 4, 3, 4), border_radius, border_radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 42))
    painter.drawPath(shadow_path)

    panel_path = QPainterPath()
    panel_path.addRoundedRect(panel_rect, border_radius, border_radius)

    gradient = QLinearGradient(QPointF(panel_rect.left(), panel_rect.top()), QPointF(panel_rect.left(), panel_rect.bottom()))
    gradient.setColorAt(0, QColor("#272b31"))
    gradient.setColorAt(1, QColor("#171b20"))
    painter.setBrush(QBrush(gradient))

    border_color = accent.lighter(110)
    border_width = 1.35
    if hovered:
        border_color = QColor("#ffffff")
        border_width = 1.9
    if selected:
        border_color = selection_color
        border_width = 2.2

    painter.setPen(QPen(border_color, border_width))
    painter.drawPath(panel_path)

    painter.save()
    painter.setClipPath(panel_path)
    accent_fill = QColor(accent)
    accent_fill.setAlpha(170)
    painter.fillRect(QRectF(panel_rect.left(), panel_rect.top(), 5, panel_rect.height()), accent_fill)

    glow = QLinearGradient(QPointF(panel_rect.left(), panel_rect.top()), QPointF(panel_rect.right(), panel_rect.top()))
    top_glow = QColor(accent)
    top_glow.setAlpha(58)
    glow.setColorAt(0, top_glow)
    glow.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillRect(QRectF(panel_rect.left(), panel_rect.top(), panel_rect.width(), min(42.0, panel_rect.height() * 0.34)), glow)
    painter.restore()

    if connection_radius > 0:
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        mid_y = panel_rect.center().y() - connection_radius
        left_rect = QRectF(panel_rect.left() - connection_radius, mid_y, connection_radius * 2, connection_radius * 2)
        right_rect = QRectF(panel_rect.right() - connection_radius, mid_y, connection_radius * 2, connection_radius * 2)
        painter.drawPie(left_rect, 90 * 16, -180 * 16)
        painter.drawPie(right_rect, 90 * 16, 180 * 16)

    painter.save()
    content_clip_path = QPainterPath()
    clip_radius = max(1.0, border_radius - 1.0)
    content_clip_path.addRoundedRect(panel_rect.adjusted(1, 1, -1, -1), clip_radius, clip_radius)
    painter.setClipPath(content_clip_path)

    if mode == "glyph":
        content_left = panel_rect.left() + 14
        content_top = panel_rect.top() + 12
        content_width = max(32.0, panel_rect.width() - 28.0)
        content_height = max(40.0, panel_rect.height() - 24.0)

        chip_rect = QRectF()
        chip_block_height = 0.0
        if badge:
            badge_font = _fit_font_to_height(
                _scaled_font("Segoe UI", 7, scale=min(detail_scale, 1.42), weight=QFont.Weight.DemiBold, max_scale=1.42),
                _lod_font_height_limit(
                    panel_rect.height(),
                    zoom,
                    scene_fraction=0.14,
                    target_pixels=12.0,
                    minimum=14.0,
                ),
                min_point_size=7.0,
            )
            painter.setFont(badge_font)
            badge_metrics = QFontMetrics(badge_font)
            chip_width = min(content_width * 0.42, badge_metrics.horizontalAdvance(badge) + 18.0)
            chip_height = max(18.0, badge_metrics.height() + 8.0)
            chip_rect = QRectF(content_left, content_top, chip_width, chip_height)
            chip_block_height = chip_height + 10.0
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 64))
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 116), 1.0))
            painter.drawRoundedRect(chip_rect, chip_height / 2, chip_height / 2)
            painter.setPen(QColor("#f5fbff"))
            painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, badge)

        footer_font = _fit_font_to_height(
            _scaled_font("Segoe UI", 9, scale=min(detail_scale, 2.05), weight=QFont.Weight.DemiBold, max_scale=2.05),
            _lod_font_height_limit(
                panel_rect.height(),
                zoom,
                scene_fraction=0.18,
                target_pixels=14.0,
                minimum=14.0,
            ),
            min_point_size=7.0,
        )
        footer_metrics = QFontMetrics(footer_font)
        footer_height = max(20.0, footer_metrics.height() + 8.0)
        footer_rect = QRectF(content_left, panel_rect.bottom() - footer_height - 12.0, content_width, footer_height)

        orb_band_top = content_top + chip_block_height
        orb_band_bottom = footer_rect.top() - 10.0
        orb_band_height = max(34.0, orb_band_bottom - orb_band_top)
        orb_limit = max(38.0, min(content_width - 16.0, orb_band_height))
        orb_size = _clamp(
            orb_limit * _clamp(0.76 + ((detail_scale - 1.0) * 0.08), 0.76, 0.92),
            38.0,
            orb_limit,
        )
        orb_rect = QRectF(
            panel_rect.center().x() - (orb_size / 2),
            orb_band_top + max(0.0, (orb_band_height - orb_size) / 2),
            orb_size,
            orb_size,
        )
        orb_gradient = QLinearGradient(QPointF(orb_rect.left(), orb_rect.top()), QPointF(orb_rect.left(), orb_rect.bottom()))
        orb_gradient.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 110))
        orb_gradient.setColorAt(1, QColor(20, 24, 28, 220))
        painter.setBrush(QBrush(orb_gradient))
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 138), 1.4))
        painter.drawEllipse(orb_rect)

        painter.setBrush(QColor(255, 255, 255, 22))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            QRectF(
                orb_rect.left() + (orb_rect.width() * 0.16),
                orb_rect.top() + (orb_rect.height() * 0.14),
                orb_rect.width() * 0.68,
                orb_rect.height() * 0.32,
            )
        )

        glyph_font = _fit_font_to_height(
            _scaled_font("Segoe UI", 16, scale=detail_scale, weight=QFont.Weight.DemiBold, max_scale=2.8),
            _lod_font_height_limit(
                orb_rect.height(),
                zoom,
                scene_fraction=0.62,
                target_pixels=24.0,
                minimum=orb_rect.height() * 0.44,
            ),
            min_point_size=8.0,
        )
        painter.setFont(glyph_font)
        painter.setPen(QColor("#f4f7fb"))
        painter.drawText(orb_rect, Qt.AlignmentFlag.AlignCenter, initials_for_title(title))

        painter.setBrush(QColor(255, 255, 255, 18))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        painter.drawRoundedRect(footer_rect, footer_height / 2, footer_height / 2)
        _draw_elided_lines(
            painter,
            footer_rect.adjusted(10.0, 0.0, -10.0, 0.0),
            title,
            font=footer_font,
            color=QColor("#f3f7fb"),
            max_lines=1,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
    else:
        content_left = panel_rect.left() + 16
        content_right = panel_rect.right() - 16
        content_width = max(32.0, content_right - content_left)
        compact_summary = panel_rect.height() < 150 or panel_rect.width() < 240
        header_height = _clamp(panel_rect.height() * (0.22 if not compact_summary else 0.28), 30.0, 42.0)
        header_rect = QRectF(panel_rect.left(), panel_rect.top(), panel_rect.width(), min(header_height, panel_rect.height() - 12.0))

        header_gradient = QLinearGradient(QPointF(header_rect.left(), header_rect.top()), QPointF(header_rect.left(), header_rect.bottom()))
        header_gradient.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 64))
        header_gradient.setColorAt(1, QColor(255, 255, 255, 10))
        painter.fillRect(header_rect, QBrush(header_gradient))

        divider_y = header_rect.bottom()
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        painter.drawLine(
            QPointF(panel_rect.left() + 12.0, divider_y),
            QPointF(panel_rect.right() - 12.0, divider_y),
        )

        badge_rect = QRectF()
        meta_text_x = content_left
        if badge:
            badge_font = _fit_font_to_height(
                _scaled_font("Segoe UI", 7, scale=min(detail_scale, 1.5), weight=QFont.Weight.DemiBold, max_scale=1.5),
                _lod_font_height_limit(
                    panel_rect.height(),
                    zoom,
                    scene_fraction=0.14 if compact_summary else 0.12,
                    target_pixels=12.0,
                    minimum=15.0,
                ),
                min_point_size=7.0,
            )
            painter.setFont(badge_font)
            badge_metrics = QFontMetrics(badge_font)
            badge_width = min(content_width * 0.38, badge_metrics.horizontalAdvance(badge) + 18.0)
            badge_height = max(18.0, badge_metrics.height() + 8.0)
            badge_rect = QRectF(
                content_left,
                header_rect.top() + max(6.0, (header_rect.height() - badge_height) / 2),
                badge_width,
                badge_height,
            )
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 74))
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 116), 1.0))
            painter.drawRoundedRect(badge_rect, badge_height / 2, badge_height / 2)
            painter.setPen(QColor("#f4f8fc"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)
            meta_text_x = badge_rect.right() + 10.0

        if subtitle:
            subtitle_font = _fit_font_to_height(
                _scaled_font("Segoe UI", 8, scale=min(detail_scale, 1.52), weight=QFont.Weight.Medium, max_scale=1.52),
                _lod_font_height_limit(
                    panel_rect.height(),
                    zoom,
                    scene_fraction=0.12,
                    target_pixels=11.0,
                    minimum=12.0,
                ),
                min_point_size=7.0,
            )
            _draw_elided_lines(
                painter,
                QRectF(meta_text_x, header_rect.top(), max(18.0, content_right - meta_text_x), header_rect.height()),
                subtitle,
                font=subtitle_font,
                color=QColor("#adb7c2"),
                max_lines=1,
            )

        body_top = header_rect.bottom() + 12.0
        body_bottom = panel_rect.bottom() - 14.0
        body_height = max(20.0, body_bottom - body_top)

        title_font = _fit_font_to_height(
            _scaled_font(
                "Segoe UI",
                12 if compact_summary else 13,
                scale=min(detail_scale, 2.45 if compact_summary else 2.1),
                weight=QFont.Weight.DemiBold,
                max_scale=2.45 if compact_summary else 2.1,
            ),
            _lod_font_height_limit(
                panel_rect.height(),
                zoom,
                scene_fraction=0.18 if compact_summary else 0.16,
                target_pixels=18.0 if compact_summary else 20.0,
                minimum=16.0,
            ),
            min_point_size=8.0,
        )
        title_metrics = QFontMetrics(title_font)
        title_line_limit = 1 if compact_summary else 2
        title_rect_height = min(body_height, (title_metrics.lineSpacing() * title_line_limit) + ((title_line_limit - 1) * 2.0) + 2.0)
        title_rect = QRectF(content_left, body_top, content_width, title_rect_height)
        title_drawn_height = _draw_elided_lines(
            painter,
            title_rect,
            title,
            font=title_font,
            color=QColor("#f7fafc"),
            max_lines=title_line_limit,
            line_gap=2.0,
        )

        preview_panel_top = body_top + title_drawn_height + (10.0 if compact_summary else 12.0)
        preview_panel_height = max(18.0, body_bottom - preview_panel_top)
        preview_panel_rect = QRectF(content_left, preview_panel_top, content_width, preview_panel_height)

        painter.setBrush(QColor(255, 255, 255, 10))
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1.0))
        painter.drawRoundedRect(preview_panel_rect, 10, 10)

        preview_font = _fit_font_to_height(
            _scaled_font(
                "Segoe UI",
                9 if compact_summary else 10,
                scale=min(detail_scale, 1.9 if compact_summary else 1.7),
                max_scale=1.9 if compact_summary else 1.7,
            ),
            _lod_font_height_limit(
                panel_rect.height(),
                zoom,
                scene_fraction=0.16 if compact_summary else 0.14,
                target_pixels=12.0 if compact_summary else 13.0,
                minimum=12.0,
            ),
            min_point_size=7.0,
        )
        preview_metrics = QFontMetrics(preview_font)
        preview_inner_rect = preview_panel_rect.adjusted(12.0, 10.0, -12.0, -10.0)
        preview_line_capacity = max(
            1,
            int((preview_inner_rect.height() + 2.0) / max(1.0, preview_metrics.lineSpacing() + 2.0)),
        )
        preview_line_limit = min(6 if not compact_summary else 3, preview_line_capacity)
        preview_drawn_height = _draw_elided_lines(
            painter,
            preview_inner_rect,
            preview or " ",
            font=preview_font,
            color=QColor("#d5dde6"),
            max_lines=preview_line_limit,
            line_gap=2.0,
        )

        remaining_preview_height = preview_inner_rect.height() - preview_drawn_height
        if remaining_preview_height >= 24.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 18 if compact_summary else 15))
            line_y = preview_inner_rect.top() + preview_drawn_height + 8.0
            line_height = 4.0
            line_gap = 8.0
            line_patterns = (0.84, 0.66, 0.78, 0.58, 0.72)
            max_skeleton_lines = min(
                len(line_patterns),
                int((preview_inner_rect.bottom() - line_y) / (line_height + line_gap)),
            )
            for index in range(max(0, max_skeleton_lines)):
                line_width = max(42.0, preview_inner_rect.width() * line_patterns[index])
                painter.drawRoundedRect(
                    QRectF(preview_inner_rect.left(), line_y, line_width, line_height),
                    2.0,
                    2.0,
                )
                line_y += line_height + line_gap

    painter.restore()

    if navigation_highlight:
        nav_pen = QPen(QColor(selection_color), 2.2, Qt.PenStyle.DashLine)
        nav_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(nav_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(panel_path)

    if search_match:
        search_pen = QPen(QColor("#f5c542"), 2.2)
        search_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(search_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(panel_path)

    painter.restore()
