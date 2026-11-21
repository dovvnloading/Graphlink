"""Profile ADR-019's 500-node full publish, including the staleness path.

Run without coverage from the repository root:
    python -m backend.tests.perf.profile_scene_publish

This deliberately uses LARGE (rather than a hand-built synthetic node list)
and leaves baseline.json unchanged. Timings are local diagnostics, not a
replacement for the Windows nightly median-of-seven baseline measurement.
"""

from __future__ import annotations

import cProfile
import io
import json
import pstats
import statistics
import sys
import time

from backend.domain.staleness import fingerprint_for_reply, scene_staleness
from backend.domain.table_workflows import TableReadCache
from backend.tests.perf.graph_factory import LARGE

REPEATS = 21
PROFILE_REPEATS = 80


def _median_ms(call) -> float:
    times = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        call()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def main() -> None:
    doc = LARGE.build()
    reply = next(
        node for node in doc.nodes.values()
        if node.kind == "chat" and not node.state.is_user and doc._branch_parent_edge(node.id)
    )
    parent_id = doc._branch_parent_edge(reply.id).source
    reply.state.provider = "profile"
    reply.state.model = "profile"
    reply.state.reply_history_fingerprint = fingerprint_for_reply(doc, parent_id, "profile", "profile", False)
    doc.update_chat_node_content(parent_id, "modified input: staleness is now active")
    doc.scene_payload()  # Warm lookup/digest caches; matches measure_baselines' median semantics.

    def rows_only():
        read_cache = TableReadCache()
        source_digests: dict[str, str] = {}
        statuses = scene_staleness(doc)
        return [
            doc._node_wire(node, source_digests, read_cache, staleness=statuses.get(node.id, "current"))
            for node in doc.nodes.values()
        ]

    payload = doc.scene_payload()
    staleness_ms = _median_ms(lambda: scene_staleness(doc))
    rows_ms = _median_ms(rows_only) - staleness_ms
    build_ms = _median_ms(doc.scene_payload)
    encode_ms = _median_ms(lambda: json.dumps(payload))
    combined_ms = _median_ms(lambda: json.dumps(doc.scene_payload()))
    sys.stdout.write(
        f"LARGE: {len(doc.nodes)} nodes / {len(doc.edges)} edges (changed-history badge active)\\n"
        f"staleness pass: {staleness_ms:.3f} ms\\n"
        f"node rows (staleness subtracted, approximate): {rows_ms:.3f} ms\\n"
        f"payload build: {build_ms:.3f} ms\\n"
        f"json.dumps(payload): {encode_ms:.3f} ms\\n"
        f"build + serialize: {combined_ms:.3f} ms\\n\\n"
    )

    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(PROFILE_REPEATS):
        json.dumps(doc.scene_payload())
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime").print_stats(25)
    sys.stdout.write(stream.getvalue())


if __name__ == "__main__":
    main()
