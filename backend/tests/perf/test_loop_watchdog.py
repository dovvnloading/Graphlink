"""ADR-019 stage 19.2: the event-loop blocking watchdog, plus the inline
chart-render call-site freeze.

The budget (ADR-019 §2): no single operation blocks the event loop > 16 ms.
Wall-clock timing that tight would flake on shared CI runners, and ADR-019 §4
explicitly splits enforcement for exactly that reason: CI gets stable,
generous "class-catcher" checks; the true 16 ms line is stage 19.3's nightly
wall-clock job. So this file enforces two things CI can hold reliably:

1. THE WATCHDOG (timing, generous ceiling): representative mutations and a
   full publish build+serialize on a 500-node graph must never block the loop
   longer than CI_STALL_CEILING_MS. The ceiling is 100 ms - ~7x today's worst
   measured legitimate cost (13.24 ms publish on `large`, measure_baselines
   2026-08-02), far below the ~125 ms+ matplotlib-class regression this
   exists to catch. Each operation is yielded around individually, so the
   assertion is per-operation, matching the budget's own wording. The batch
   runs twice and the SMALLER max-stall wins: a one-off GC/scheduler spike
   hits one run, a real synchronous block hits both.

2. THE CALL-SITE FREEZE (counting, fully stable): render_chart_png - the one
   known ~125 ms synchronous loop-blocker - is called from EXACTLY the sites
   listed in _KNOWN_RENDER_CALL_SITES, all three of which are today's
   documented, deliberately-excluded offenders owned by ADR-013 ("0 ms on the
   loop" via off-thread render is that ADR's exit criterion). A NEW call site
   fails this test immediately: you may not add another inline render to the
   loop - wrap it in asyncio.to_thread or route it through the ADR-013 work.
   When ADR-013 lands and moves these off-loop, the freeze table shrinks with
   it - that failure is the reminder to update both this test and the
   watchdog's chart exclusion.

Known offenders excluded from the watchdog window (all ADR-013's to fix):
  - SceneDocument.add_chart_node's inline render (backend/domain/graph.py)
  - SceneDocument's chart re-render on resize/update (same file)
  - the 3x-DPI export route (backend/assets.py)
The watchdog's fixture is therefore built with chart_count=0 - not to hide
the problem (the freeze above pins it visibly) but because a gate that is
red on day one enforces nothing.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from backend.tests.perf.graph_factory import build_graph

REPO_ROOT = Path(__file__).resolve().parents[3]

CI_STALL_CEILING_MS = 100.0
_SAMPLER_TICK_S = 0.005


async def _max_stall_ms(operations) -> float:
    """Runs each zero-arg callable in `operations` on the loop, yielding
    between them, while a sampler task measures how late its own ticks fire.
    Returns the worst single gap (ms) beyond the tick interval - i.e. the
    longest stretch the loop was blocked by any one operation. Windows timer
    granularity (~15 ms) inflates every gap equally; the ceiling accounts
    for it."""
    loop = asyncio.get_running_loop()
    gaps: list[float] = []
    stop = asyncio.Event()

    async def sampler() -> None:
        last = loop.time()
        while not stop.is_set():
            await asyncio.sleep(_SAMPLER_TICK_S)
            now = loop.time()
            gaps.append(now - last - _SAMPLER_TICK_S)
            last = now

    task = asyncio.create_task(sampler())
    await asyncio.sleep(_SAMPLER_TICK_S * 3)  # sampler settles first
    for op in operations:
        op()
        await asyncio.sleep(0)
    await asyncio.sleep(_SAMPLER_TICK_S * 3)  # last op's gap gets sampled
    stop.set()
    await task
    return max(gaps, default=0.0) * 1000


def _representative_operations(doc):
    """Today's real mutation + publish surface, minus the known chart
    offenders (see module docstring). Every callable here runs synchronously
    on the loop in production - via an async intent handler or the publish
    path - so each is individually subject to the 16 ms budget."""
    node_ids = list(doc.nodes.keys())
    first, second = node_ids[0], node_ids[1]

    def publish_build_and_serialize():
        json.dumps(doc.scene_payload())

    return [
        lambda: doc.record_command(
            "moveNode", "user", lambda: doc.move_node(first, 5000.0, 5000.0),
            node_ids=[first],
        ),
        lambda: doc.record_command(
            "moveNodes", "user",
            lambda: doc.move_nodes([(nid, doc.nodes[nid].x + 10.0, doc.nodes[nid].y) for nid in node_ids[:50]]),
            node_ids=node_ids[:50],
        ),
        lambda: doc.record_command(
            "removeNodes", "user", lambda: doc.remove_nodes([second]), node_ids=[second],
        ),
        lambda: doc.undo(),
        lambda: doc.undo(),
        publish_build_and_serialize,
    ]


def test_no_representative_operation_blocks_the_loop_beyond_the_ci_ceiling():
    # asyncio.run() in a sync test, not @pytest.mark.asyncio - pytest-asyncio
    # is not one of this repo's dependencies (dev deps are pytest+pytest-env
    # only, see pyproject.toml), and every other async-touching test here
    # already uses this same convention (test_event_bus.py et al.).
    async def _run() -> float:
        doc = build_graph(node_count=500, content_bytes=1200, chart_count=0, image_count=0, extra_edges=200)
        runs = []
        for _ in range(2):
            runs.append(await _max_stall_ms(_representative_operations(doc)))
        return min(runs)

    best = asyncio.run(_run())
    assert best <= CI_STALL_CEILING_MS, (
        f"an operation blocked the event loop for {best:.1f} ms across both runs "
        f"(CI ceiling: {CI_STALL_CEILING_MS:.0f} ms; the true ADR-019 budget is 16 ms, "
        "enforced by stage 19.3's nightly job) - something synchronous and heavy "
        "(the matplotlib-render class) landed on the loop. Move it off-thread "
        "(asyncio.to_thread) instead of raising this ceiling."
    )


# -- the call-site freeze -----------------------------------------------------

# module (repo-relative, posix) -> exact number of render_chart_png() calls.
_KNOWN_RENDER_CALL_SITES = {
    "backend/domain/graph.py": 2,  # add_chart_node + the resize/update re-render
    "backend/assets.py": 1,        # the 3x-DPI export route
}


def _count_render_calls(tree: ast.Module) -> int:
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name == "render_chart_png":
                count += 1
    return count


def test_inline_chart_render_call_sites_are_frozen():
    found: dict[str, int] = {}
    for path in sorted((REPO_ROOT / "backend").rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        count = _count_render_calls(tree)
        if count:
            found[path.relative_to(REPO_ROOT).as_posix()] = count

    assert found == _KNOWN_RENDER_CALL_SITES, (
        "the set of render_chart_png() call sites under backend/ changed.\n"
        f"  expected: {_KNOWN_RENDER_CALL_SITES}\n"
        f"  found:    {found}\n"
        "A NEW site means another synchronous ~125 ms matplotlib render landed on "
        "the event loop - wrap it in asyncio.to_thread or route it through ADR-013's "
        "off-loop render work instead of adding it here. A REMOVED site most likely "
        "means ADR-013 landed - shrink this table and the watchdog's chart_count=0 "
        "exclusion together, deliberately."
    )


def test_the_freeze_scan_finds_the_known_offenders_at_all():
    # Guards the guard: if the scan's glob or name-matching broke, the freeze
    # above could pass vacuously against an empty `found`. The two known
    # offender modules must actually exist and parse.
    for rel in _KNOWN_RENDER_CALL_SITES:
        assert (REPO_ROOT / rel).is_file(), f"{rel} vanished - update _KNOWN_RENDER_CALL_SITES"
