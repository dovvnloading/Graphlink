"""Repeatable organization benchmarks, separate from publish/serialization.

Run: python -m backend.tests.perf.measure_organization

The documented 25/100/500/2,000-node workloads are generated through
the real domain API. This measures read-only preview time and memory, as
well as objective geometric quality: diagram area, movement and crossings.
Hard safety invariants are enforced by make_organization_plan itself.
No thresholds are baked into this measurement script: inspect recorded
baselines across runner/OS variants before establishing CI budgets (#476).
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
import tracemalloc

from backend.domain.containment import hidden_group_descendants
from backend.domain.organization import make_organization_plan
from backend.tests.perf.graph_factory import ALL_WORKLOADS, Workload


def _crosses(
    a: tuple[float, float], b: tuple[float, float],
    c: tuple[float, float], d: tuple[float, float],
) -> bool:
    """Proper intersection of two centreline segments, not an endpoint touch."""
    def orient(p: tuple[float, float], q: tuple[float, float],
               r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    a1, a2 = orient(a, b, c), orient(a, b, d)
    b1, b2 = orient(c, d, a), orient(c, d, b)
    return a1 * a2 < 0 and b1 * b2 < 0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil((len(ordered) - 1) * fraction)]


def layout_quality(document, plan) -> dict[str, float | int]:
    """Compare before/after positions and edge-centre routing, deterministically."""
    hidden = hidden_group_descendants(plan.nodes)
    rects = [
        (n.x, n.y, *document.node_footprint(n))
        for nid, n in plan.nodes.items()
        if nid not in hidden and not n.is_docked
    ]
    if rects:
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[0] + r[2] for r in rects)
        bottom = max(r[1] + r[3] for r in rects)
        area = max(0.0, right - left) * max(0.0, bottom - top)
    else:
        area = 0.0

    movement = [
        math.hypot(after.x - document.nodes[nid].x, after.y - document.nodes[nid].y)
        for nid, after in plan.nodes.items()
        if nid not in hidden and not after.is_docked
    ]
    centre = {
        nid: (node.x + document.node_footprint(node)[0] / 2,
              node.y + document.node_footprint(node)[1] / 2)
        for nid, node in plan.nodes.items()
        if nid not in hidden and not node.is_docked
    }
    paths = [
        (edge.source, edge.target, centre[edge.source], centre[edge.target])
        for edge in document.edges.values()
        if edge.source in centre and edge.target in centre
    ]
    lengths = [math.dist(start, end) for _, _, start, end in paths]
    crossings = 0
    for index, (source, target, a, b) in enumerate(paths):
        for c_source, c_target, c, d in paths[index + 1:]:
            if source in (c_source, c_target) or target in (c_source, c_target):
                continue
            if (
                max(a[0], b[0]) < min(c[0], d[0])
                or max(c[0], d[0]) < min(a[0], b[0])
                or max(a[1], b[1]) < min(c[1], d[1])
                or max(c[1], d[1]) < min(a[1], b[1])
            ):
                continue
            crossings += int(_crosses(a, b, c, d))
    return {
        "diagram_area": round(area, 2),
        "edge_crossings": crossings,
        "mean_edge_length": round(statistics.mean(lengths) if lengths else 0.0, 2),
        "p95_edge_length": round(_percentile(lengths, 0.95), 2),
        "mean_displacement": round(statistics.mean(movement) if movement else 0.0, 2),
        "p95_displacement": round(_percentile(movement, 0.95), 2),
        "moved_node_count": len(plan.changed_ids),
    }


def measure(workload: Workload, repeats: int = 5) -> dict[str, object]:
    document = workload.build()
    durations: list[float] = []
    peaks: list[int] = []
    fingerprints: set[str] = set()
    plan = None
    for _ in range(repeats):
        tracemalloc.start()
        start = time.perf_counter()
        plan = make_organization_plan(document, "all")
        durations.append((time.perf_counter() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        fingerprints.add(plan.plan_id)

    assert plan is not None
    assert len(fingerprints) == 1, "preview layout is nondeterministic"
    return {
        "name": workload.name,
        "nodes": len(document.nodes),
        "edges": len(document.edges),
        "preview_p50_ms": round(statistics.median(durations), 2),
        "preview_p95_ms": round(_percentile(durations, 0.95), 2),
        "preview_peak_mib": round(max(peaks) / (1024 * 1024), 2),
        **layout_quality(document, plan),
    }


def main() -> None:
    results = [measure(workload) for workload in ALL_WORKLOADS]
    sys.stdout.write(json.dumps(results, indent=2) + "\\n")


if __name__ == "__main__":
    main()
