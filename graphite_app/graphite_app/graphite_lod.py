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


def preview_text(*parts, fallback="", limit=120):
    for part in parts:
        if part is None:
            continue
        normalized = re.sub(r"\s+", " ", str(part)).strip()
        if normalized:
            return normalized[:limit]
    return fallback


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
        orb_size = _clamp(
            48.0 * _clamp(0.96 + ((detail_scale - 1.0) * 0.26), 1.0, 1.56),
            48.0,
            max(48.0, min(panel_rect.width() - 24.0, panel_rect.height() * 0.68)),
        )
        orb_rect = QRectF(
            panel_rect.center().x() - (orb_size / 2),
            panel_rect.top() + max(14.0, panel_rect.height() * 0.15),
            orb_size,
            orb_size,
        )
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 48))
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 112), 1.2))
        painter.drawEllipse(orb_rect)

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

        label_font = _fit_font_to_height(
            _scaled_font("Segoe UI", 10, scale=min(detail_scale, 2.35), weight=QFont.Weight.DemiBold, max_scale=2.35),
            _lod_font_height_limit(
                panel_rect.height(),
                zoom,
                scene_fraction=0.22,
                target_pixels=15.0,
                minimum=14.0,
            ),
            min_point_size=7.0,
        )
        painter.setFont(label_font)
        label_metrics = QFontMetrics(label_font)
        label_text = label_metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(panel_rect.width() - 26))
        label_top = min(
            orb_rect.bottom() + max(8.0, 7.0 * min(detail_scale, 1.5)),
            panel_rect.bottom() - label_metrics.height() - 12,
        )
        label_rect = QRectF(
            panel_rect.left() + 13,
            label_top,
            panel_rect.width() - 26,
            label_metrics.height() + 6,
        )
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label_text)
    else:
        content_left = panel_rect.left() + 16
        content_right = panel_rect.right() - 16
        content_width = max(32.0, content_right - content_left)
        header_y = panel_rect.top() + 14
        footer_y = panel_rect.bottom() - 16
        compact_summary = zoom < 0.52 or panel_rect.height() < 135

        if badge:
            badge_font = _fit_font_to_height(
                _scaled_font("Segoe UI", 7, scale=min(detail_scale, 1.55), weight=QFont.Weight.DemiBold, max_scale=1.55),
                _lod_font_height_limit(
                    panel_rect.height(),
                    zoom,
                    scene_fraction=0.18 if compact_summary else 0.15,
                    target_pixels=13.0,
                    minimum=16.0,
                ),
                min_point_size=7.0,
            )
            painter.setFont(badge_font)
            badge_metrics = QFontMetrics(badge_font)
            badge_width = badge_metrics.horizontalAdvance(badge) + 18
            badge_height = max(18.0, badge_metrics.height() + 8.0)
            badge_rect = QRectF(content_left, header_y, badge_width, badge_height)
            painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 58))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(badge_rect, 9, 9)
            painter.setPen(QColor("#eff6ff"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)
            text_x = badge_rect.right() + 10
        else:
            text_x = content_left
            badge_rect = QRectF()

        title_font = _fit_font_to_height(
            _scaled_font(
                "Segoe UI",
                13 if compact_summary else 11,
                scale=min(detail_scale, 3.2 if compact_summary else 2.5),
                weight=QFont.Weight.DemiBold,
                max_scale=3.2 if compact_summary else 2.5,
            ),
            _lod_font_height_limit(
                panel_rect.height(),
                zoom,
                scene_fraction=0.4 if compact_summary else 0.3,
                target_pixels=20.0 if compact_summary else 17.0,
                minimum=18.0,
            ),
            min_point_size=8.0,
        )
        painter.setFont(title_font)
        title_metrics = QFontMetrics(title_font)
        title_row_height = max(badge_rect.height(), title_metrics.height() + 2.0)
        title_text = title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(max(24.0, content_right - text_x)))
        painter.setPen(QColor("#f7fafc"))
        title_rect = QRectF(
            text_x,
            header_y,
            max(24.0, content_right - text_x),
            title_row_height,
        )
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)
        header_bottom = title_rect.bottom()

        preview_font = _fit_font_to_height(
            _scaled_font(
                "Segoe UI",
                10 if compact_summary else 9,
                scale=min(detail_scale, 2.2 if compact_summary else 1.9),
                max_scale=2.2 if compact_summary else 1.9,
            ),
            _lod_font_height_limit(
                panel_rect.height(),
                zoom,
                scene_fraction=0.24 if compact_summary else 0.19,
                target_pixels=14.0 if compact_summary else 12.0,
                minimum=14.0,
            ),
            min_point_size=7.0,
        )
        preview_metrics = QFontMetrics(preview_font)
        preview_height = preview_metrics.height() + 4
        preview_rect = QRectF(
            content_left,
            footer_y - preview_height,
            content_width,
            preview_height,
        )

        if subtitle:
            subtitle_font = _fit_font_to_height(
                _scaled_font("Segoe UI", 8, scale=min(detail_scale, 1.55), max_scale=1.55),
                _lod_font_height_limit(
                    panel_rect.height(),
                    zoom,
                    scene_fraction=0.16,
                    target_pixels=11.0,
                    minimum=13.0,
                ),
                min_point_size=7.0,
            )
            painter.setFont(subtitle_font)
            subtitle_metrics = QFontMetrics(subtitle_font)
            subtitle_text = subtitle_metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, int(content_width))
            subtitle_top = header_bottom + max(2.0, 2.5 * min(detail_scale, 1.5))
            subtitle_rect = QRectF(content_left, subtitle_top, content_width, subtitle_metrics.height() + 2)
            subtitle_gap = preview_rect.top() - subtitle_rect.bottom()
            show_subtitle = (not compact_summary) and subtitle_gap >= (subtitle_metrics.height() + 4)
            if show_subtitle:
                painter.setPen(QColor("#9da6b1"))
                painter.drawText(subtitle_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, subtitle_text)

        painter.setPen(QColor("#d6dde6"))
        painter.setFont(preview_font)
        preview_text_value = preview_metrics.elidedText(preview or " ", Qt.TextElideMode.ElideRight, int(preview_rect.width()))
        painter.drawText(preview_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, preview_text_value)

        skeleton_top = preview_rect.top() - max(18.0, 14.0 * min(detail_scale, 1.45))
        show_skeleton = (not compact_summary) and (skeleton_top - header_bottom) >= 14.0
        if show_skeleton:
            painter.setPen(Qt.PenStyle.NoPen)
            line_color = QColor(255, 255, 255, 22)
            painter.setBrush(line_color)
            line_width = max(40.0, panel_rect.width() * 0.26)
            painter.drawRoundedRect(QRectF(content_left, skeleton_top, line_width, 4), 2, 2)
            painter.drawRoundedRect(QRectF(content_left, skeleton_top + 8, max(28.0, line_width * 0.68), 4), 2, 2)

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
