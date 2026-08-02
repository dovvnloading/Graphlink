"""ADR-019 stage 19.1: fills in the "today" column of ADR-019's budget table
with real, reproducible measurements against the graph_factory fixtures -
replacing the audit's one-off subagent measurement with a committed,
rerunnable artifact. Run directly: `python -m backend.tests.perf.measure_baselines`.

Scope, honestly stated: this measures everything that is backend-only and
needs no live app instance (wire bytes, publish build+serialize cost, a
single node's own payload size as a proxy for ADR-003's future patch cost).
It deliberately does NOT attempt cold-start, chat-load-from-db, memory
steady-state, or canvas frame-time - those need a real running instance
(graphlink_desktop.py + a browser) and are correctly a separate, larger
piece of work, not a Phase 0 quick-fix. See ADR-019's table for which cells
remain "not measured" pending that follow-up.
"""

from __future__ import annotations

import json
import time

from backend.tests.perf.graph_factory import ALL_WORKLOADS, Workload


def _measure(workload: Workload) -> dict:
    t0 = time.perf_counter()
    doc = workload.build()
    build_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    payload = doc.scene_payload()
    payload_build_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    serialized = json.dumps(payload)
    serialize_s = time.perf_counter() - t0

    total_bytes = len(serialized.encode("utf-8"))
    one_node_bytes = len(json.dumps(payload["nodes"][-1]).encode("utf-8")) if payload["nodes"] else 0

    return {
        "name": workload.name,
        "node_count": len(doc.nodes),
        "edge_count": len(doc.edges),
        "fixture_build_ms": round(build_s * 1000, 2),
        "payload_build_ms": round(payload_build_s * 1000, 2),
        "serialize_ms": round(serialize_s * 1000, 2),
        "publish_total_ms": round((payload_build_s + serialize_s) * 1000, 2),
        "full_snapshot_kib": round(total_bytes / 1024, 1),
        "single_node_payload_kib": round(one_node_bytes / 1024, 3),
    }


def main() -> None:
    results = [_measure(w) for w in ALL_WORKLOADS]
    print(f"{'workload':<10} {'nodes':>6} {'edges':>6} {'build_ms':>9} {'payload_ms':>11} "
          f"{'serialize_ms':>13} {'total_ms':>9} {'snapshot_KiB':>13} {'one_node_KiB':>13}")
    for r in results:
        print(
            f"{r['name']:<10} {r['node_count']:>6} {r['edge_count']:>6} "
            f"{r['fixture_build_ms']:>9} {r['payload_build_ms']:>11} "
            f"{r['serialize_ms']:>13} {r['publish_total_ms']:>9} "
            f"{r['full_snapshot_kib']:>13} {r['single_node_payload_kib']:>13}"
        )
    print()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
