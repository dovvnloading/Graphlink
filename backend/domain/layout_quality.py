"""Bounded, source-grounded visual connection diagnostics for layout plans.

These diagnostics use straight segments between node centres, *not* the
rendered orthogonal route or port geometry. They are comparable estimates
for a before/after preview, not a guarantee of zero routed-wire crossings.
They never influence or modify the proposed node coordinates.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


MAX_MEASURED_CROSSING_EDGES = 180
Rect = tuple[float, float, float, float]
Point = tuple[float, float]


def _centres(rects: Mapping[str, Rect]) -> dict[str, Point]:
    return {
        nid: (x + width / 2, y + height / 2)
        for nid, (x, y, width, height) in rects.items()
    }


def _orientation(a: Point, b: Point, c: Point) -> float:
    """Normalize enormous coordinates before the cross product."""
    m = max(abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]), abs(c[0]), abs(c[1]))
    if m == 0:
        return 0.0
    ax, ay = a[0] / m, a[1] / m
    bx, by = b[0] / m, b[1] / m
    cx, cy = c[0] / m, c[1] / m
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _proper_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    p, q = _orientation(a, b, c), _orientation(a, b, d)
    r, s = _orientation(c, d, a), _orientation(c, d, b)
    return p != 0 and q != 0 and r != 0 and s != 0 and (p > 0) != (q > 0) and (r > 0) != (s > 0)


def connection_diagnostics(
    rects: Mapping[str, Rect],
    edges: Sequence[tuple[str, str]],
) -> dict[str, int | float | None]:
    """Return exact bounded centreline crossing count and mean edge length.

    Shared endpoints are excluded. Beyond 180 edges the crossing count is
    null rather than a biased estimate; length still covers every edge.
    """
    centres = _centres(rects)
    visible = [
        (source, target) for source, target in edges
        if source != target and source in centres and target in centres
    ]
    lengths = [
        math.hypot(centres[target][0] - centres[source][0],
                   centres[target][1] - centres[source][1])
        for source, target in visible
    ]
    mean_length = (
        math.fsum(length / len(lengths) for length in lengths)
        if lengths and all(math.isfinite(length) for length in lengths)
        else 0.0
    )
    if len(visible) > MAX_MEASURED_CROSSING_EDGES:
        return {"crossings": None, "meanEdgeLength": mean_length, "measuredEdges": len(visible)}
    crossings = 0
    for i, (a, b) in enumerate(visible):
        for c, d in visible[i + 1:]:
            if len({a, b, c, d}) != 4:
                continue
            if _proper_cross(centres[a], centres[b], centres[c], centres[d]):
                crossings += 1
    return {"crossings": crossings, "meanEdgeLength": mean_length, "measuredEdges": len(visible)}
