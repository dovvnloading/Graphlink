"""Finite, bounded geometry at mutation and organization boundaries.

Never let a malformed reported size, chart resize or node move inject NaN/
infinity into the scene. Layout calculations must use bounded real values:
an overflowed subtree span can otherwise turn a centred x into inf - inf.
The finite bound is generously above realistic canvas scales; it prevents
hostile/inadvertent floating-point extrema, not ordinary legacy layouts.
"""

from __future__ import annotations

import math
from backend.domain._composed import SceneDocumentParts
from backend.domain.model import SceneError
from backend.domain.node_states import ChartState, FrameState, GroupSizedState

MAX_COORDINATE = 1_000_000_000_000.0
MAX_DIMENSION = 1_000_000_000_000.0
MAX_MEASURED_DIMENSION = 1_000_000.0


def finite_float(value: object, field: str, *, limit: float = MAX_COORDINATE,
                 positive: bool = False) -> float:
    """Coerce a geometry input to a finite, bounded float or fail closed."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise SceneError(f"{field} must be a finite number") from exc
    if not math.isfinite(result) or abs(result) > limit or (positive and result <= 0):
        raise SceneError(f"{field} must be a finite, bounded {'positive ' if positive else ''}number")
    return result


def valid_measured_dimension(value: object) -> bool:
    """Strictly positive dimension; size reports are advisory and skipped."""
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(numeric) and 0 < numeric <= MAX_MEASURED_DIMENSION


def validate_layout_geometry(document: SceneDocumentParts) -> None:
    """Validate a whole scene before planning and *after* calculating a plan.

    Includes measured sizes even for currently hidden members so a later
    expansion cannot reveal geometry polluted by an untrusted size report.
    This function is read-only and never repairs a broken graph silently.
    """
    for node in document.nodes.values():
        finite_float(node.x, f"node {node.id} x")
        finite_float(node.y, f"node {node.id} y")
        if isinstance(node.state, ChartState):
            finite_float(node.state.chart_width, f"chart {node.id} width",
                         limit=MAX_DIMENSION, positive=True)
            finite_float(node.state.chart_height, f"chart {node.id} height",
                         limit=MAX_DIMENSION, positive=True)
        if isinstance(node.state, GroupSizedState):
            for name in ("group_width", "group_height"):
                value = getattr(node.state, name)
                if value is not None:
                    finite_float(value, f"group {node.id} {name}",
                                 limit=MAX_DIMENSION, positive=True)
            if isinstance(node.state, FrameState):
                for name in ("group_manual_x", "group_manual_y"):
                    value = getattr(node.state, name)
                    if value is not None:
                        finite_float(value, f"frame {node.id} {name}")
                for name in ("group_manual_width", "group_manual_height"):
                    value = getattr(node.state, name)
                    if value is not None:
                        finite_float(value, f"frame {node.id} {name}",
                                     limit=MAX_DIMENSION, positive=True)
        w, h = document.node_footprint(node)
        finite_float(w, f"node {node.id} footprint width",
                     limit=MAX_DIMENSION, positive=True)
        finite_float(h, f"node {node.id} footprint height",
                     limit=MAX_DIMENSION, positive=True)
        finite_float(node.x + w, f"node {node.id} right edge")
        finite_float(node.y + h, f"node {node.id} bottom edge")
    for node_id, (w, h) in document.measured_sizes.items():
        if not valid_measured_dimension(w) or not valid_measured_dimension(h):
            raise SceneError(f"invalid measured geometry for node {node_id}")
