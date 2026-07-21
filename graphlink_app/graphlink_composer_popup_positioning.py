"""Shared screen-space positioning for the composer's picker/context-review
overlay hosts (Phase 5 increment 3).

Both ComposerPickerHost and ComposerContextHost anchor themselves relative to
the composer and the graph viewport identically. Relocated here, unchanged,
from graphlink_composer_popups.py (deleted this increment) so the anchoring
math survives the native-popup-to-island migration - it was never
Qt.WindowType.Tool-specific to begin with, and still applies to an embedded
QFrame with real screen geometry via mapToGlobal().
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize


def composer_picker_position(
    viewport_rect: QRect,
    composer_rect: QRect,
    popup_size: QSize,
    margin: int = 8,
) -> QPoint:
    """Return a global popup position that stays inside the graph viewport.

    All rectangles are expected to be in global screen coordinates. The picker
    prefers the space above the composer, then below it, and finally clamps to
    the viewport when neither side has enough room.
    """
    margin = max(0, int(margin))
    popup_width = max(0, int(popup_size.width()))
    popup_height = max(0, int(popup_size.height()))

    min_x = viewport_rect.left() + margin
    max_x = viewport_rect.right() - popup_width + 1 - margin
    anchor_x = composer_rect.right() - popup_width + 1 - 10
    x = min(max(min_x, anchor_x), max_x) if max_x >= min_x else min_x

    min_y = viewport_rect.top() + margin
    max_y = viewport_rect.bottom() - popup_height + 1 - margin
    above_y = composer_rect.top() - popup_height - margin
    below_y = composer_rect.bottom() + 1 + margin
    if above_y >= min_y:
        y = above_y
    elif below_y <= max_y:
        y = below_y
    else:
        y = min(max(min_y, above_y), max_y) if max_y >= min_y else min_y

    return QPoint(int(x), int(y))
