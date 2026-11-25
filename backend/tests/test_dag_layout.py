"""Graphlink optional DAG canvas organization: topological invariants and preview safety."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math

import pytest

from backend.canvas import SceneDocument
from backend.domain.dag_layout import (
    MAX_DAG_LAYOUT_NODES, _accepted_rank_edges, dag_layout_supported,
    organize_dag_layout,
)
from backend.domain.layout import NODE_GAP_Y
from backend.domain.model import SceneError
from backend.domain.organization import make_organization_plan, apply_organization_plan
from backend.events import SessionBus
from backend.api.intents_view import register_view_intents


def make_converging_scene() -> tuple[SceneDocument, dict[str, str]]:
    doc = SceneDocument()
    a = doc.add_chat_node(-1000, 200, "alpha", True)
    b = doc.add_chat_node(1400, 100, "beta", True)
    c = doc.add_chat_node(900, 400, "left-result", False)
    d = doc.add_chat_node(-300, 500, "right-result", False)
    end = doc.add_chat_node(200, 700, "converged", False)
    doc.connect(a.id, c.id)
    doc.connect(b.id, d.id)
    doc.connect(c.id, end.id)
    doc.connect(d.id, end.id)
    return doc, {"a": a.id, "b": b.id, "c": c.id, "d": d.id, "end": end.id}


def _no_overlaps(doc: SceneDocument) -> None:
    leaves = [node for node in doc.nodes.values() if node.kind not in ("frame", "container")
              and not node.is_docked]
    for i, left in enumerate(leaves):
        aw, ah = doc.node_footprint(left)
        for right in leaves[i + 1:]:
            bw, bh = doc.node_footprint(right)
            assert (left.x + aw <= right.x or right.x + bw <= left.x or
                    left.y + ah <= right.y or right.y + bh <= left.y), (
                        left.id, right.id)


def test_converging_branch_is_below_both_parents_and_stays_centred():
    doc, ids = make_converging_scene()
    pre = [(n.id, n.x, n.y) for n in doc.nodes.values()]
    edges = deepcopy(doc.edges)
    plan = make_organization_plan(doc, layout_style="dag")
    assert plan.effective_style == "dag"
    assert plan.layout_style == "dag"
    assert [(n.id, n.x, n.y) for n in doc.nodes.values()] == pre
    assert doc.edges == edges

    projected = plan.nodes
    c, d, end = [projected[ids[name]] for name in ("c", "d", "end")]
    assert end.y >= c.y + doc.node_footprint(c)[1] + NODE_GAP_Y
    assert end.y >= d.y + doc.node_footprint(d)[1] + NODE_GAP_Y
    centres = sorted([c.x + doc.node_footprint(c)[0] / 2,
                      d.x + doc.node_footprint(d)[0] / 2])
    assert centres[0] <= end.x + doc.node_footprint(end)[0] / 2 <= centres[1]

    preview = plan.preview(doc)
    assert preview["layoutStyle"] == preview["effectiveLayoutStyle"] == "dag"
    assert preview["layoutNote"] == ""
    assert preview["movedCount"] == len(plan.changed_ids)
    apply_organization_plan(doc, plan)
    _no_overlaps(doc)
    assert doc.edges == edges
    second = make_organization_plan(doc, layout_style="dag")
    assert not second.changed_ids


def test_rank_solver_reorders_crossing_siblings_deterministically():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "A", True)
    b = doc.add_chat_node(1000, 0, "B", True)
    x = doc.add_chat_node(0, 900, "X", False)
    y = doc.add_chat_node(1000, 900, "Y", False)
    doc.connect(a.id, y.id)
    doc.connect(b.id, x.id)
    plan = make_organization_plan(doc, layout_style="dag")
    assert plan.nodes[y.id].x < plan.nodes[x.id].x
    assert plan.nodes[y.id].y == plan.nodes[x.id].y
    _no_overlaps(_as_projected(doc, plan))
    assert make_organization_plan(doc, layout_style="dag").plan_id == plan.plan_id


def _as_projected(doc: SceneDocument, plan):
    from copy import copy
    shadow = copy(doc)
    shadow.nodes = plan.nodes
    return shadow


def test_cyclic_legacy_links_remain_on_scene_but_cannot_impose_invalid_ranks():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    b = doc.add_chat_node(200, 0, "b", True)
    c = doc.add_chat_node(400, 0, "c", True)
    doc.connect(a.id, b.id)
    doc.connect(b.id, c.id)
    doc.connect_unchecked(c.id, a.id)
    before = deepcopy(doc.edges)
    accepted = _accepted_rank_edges(doc, set(doc.nodes))
    assert len(accepted) == 2
    assert not any(edge == (c.id, a.id) for edge in accepted)
    assert organize_dag_layout(doc)
    assert doc.edges == before
    _no_overlaps(doc)
    assert doc.nodes[a.id].y < doc.nodes[b.id].y < doc.nodes[c.id].y


def test_grouped_graphs_keep_group_safe_tree_organizer_with_explicit_feedback():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    b = doc.add_chat_node(600, 0, "b", False)
    frame = doc.create_frame([a.id, b.id])
    assert not dag_layout_supported(doc)
    before = [(node.id, node.x, node.y) for node in doc.nodes.values()]
    plan = make_organization_plan(doc, layout_style="dag")
    assert plan.layout_style == "dag"
    assert plan.effective_style == "tree"
    preview = plan.preview(doc)
    assert "group-safe tree organizer" in preview["layoutNote"]
    assert before == [(node.id, node.x, node.y) for node in doc.nodes.values()]
    assert frame.id in plan.nodes
    apply_organization_plan(doc, plan)


def test_dag_scoped_plan_preserves_fixed_positions_and_is_fingerprint_fenced():
    doc, ids = make_converging_scene()
    fixed = ids["a"]
    original = doc.nodes[fixed]
    old = (original.x, original.y)
    plan = make_organization_plan(doc, "all", fixed_node_ids=[fixed], layout_style="dag")
    assert plan.effective_style == "dag"
    assert (plan.nodes[fixed].x, plan.nodes[fixed].y) == old
    apply_organization_plan(doc, plan)
    assert (doc.nodes[fixed].x, doc.nodes[fixed].y) == old
    _no_overlaps(doc)
    # Every strategy needs its own plan ID; an after-the-fact switch can
    # never silently apply the wrong positions on a stale preview.
    assert make_organization_plan(doc, layout_style="tree").plan_id != (
        make_organization_plan(doc, layout_style="dag").plan_id)


def test_no_new_layout_strategy_can_bypass_geometric_safety_and_limits():
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "a", True)
    with pytest.raises(SceneError, match="layout_style"):
        make_organization_plan(doc, layout_style="untrusted")
    assert MAX_DAG_LAYOUT_NODES >= 100
    a.x = math.inf
    with pytest.raises((SceneError, ValueError)):
        make_organization_plan(doc, layout_style="dag")


def test_preview_apply_wire_accepts_dag_only_with_matching_plan_and_undo():
    async def run():
        doc, ids = make_converging_scene()
        bus = SessionBus("dag-layout")
        bus.register_topic("scene", doc.scene_payload)
        register_view_intents(bus, doc)
        preview = await bus.dispatch_intent(
            "scene", "previewOrganize", ["all", None, None, None, True, "dag"],
        )
        assert preview["layoutStyle"] == "dag"
        assert preview["effectiveLayoutStyle"] == "dag"
        assert "planId" in preview
        with pytest.raises(SceneError, match="preview changed"):
            await bus.dispatch_intent(
                "scene", "applyOrganize",
                ["all", None, None, None, True, preview["planId"], "tree"],
            )
        before = {nid: (node.x, node.y) for nid, node in doc.nodes.items()}
        await bus.dispatch_intent(
            "scene", "applyOrganize",
            ["all", None, None, None, True, preview["planId"], "dag"],
        )
        assert before != {nid: (node.x, node.y) for nid, node in doc.nodes.items()}
        assert doc.undo().command_type == "organizeNodes"
        assert before == {nid: (node.x, node.y) for nid, node in doc.nodes.items()}
        assert doc.redo().command_type == "organizeNodes"

    asyncio.run(run())


def test_crossing_diagnostics_are_exact_bounded_and_nonmutating():
    from backend.domain.layout_quality import (
        MAX_MEASURED_CROSSING_EDGES, connection_diagnostics,
    )
    rects = {
        "A": (0, 0, 10, 10), "B": (100, 0, 10, 10),
        "C": (0, 100, 10, 10), "D": (100, 100, 10, 10),
    }
    crossing = [("A", "D"), ("B", "C")]
    before = deepcopy(rects)
    metrics = connection_diagnostics(rects, crossing)
    assert metrics["crossings"] == 1
    assert metrics["measuredEdges"] == 2
    assert metrics["meanEdgeLength"] == pytest.approx(math.sqrt(20000))
    # Shared endpoints are not spurious crossings. Moving C/D to align the
    # actual links removes the one crossing, without touching the source.
    assert connection_diagnostics(rects, [("A", "D"), ("A", "C")])["crossings"] == 0
    swapped = {**rects, "C": rects["D"], "D": rects["C"]}
    assert connection_diagnostics(swapped, crossing)["crossings"] == 0
    assert rects == before
    many = [("A", "D")] * (MAX_MEASURED_CROSSING_EDGES + 1)
    large = connection_diagnostics(rects, many)
    assert large["crossings"] is None
    assert large["measuredEdges"] == len(many)
    assert large["meanEdgeLength"] == pytest.approx(math.sqrt(20000))


def test_dag_preview_reports_real_geometry_diagnostics_without_changing_scene():
    doc, ids = make_converging_scene()
    before = [(node.id, node.x, node.y) for node in doc.nodes.values()]
    plan = make_organization_plan(doc, layout_style="dag")
    preview = plan.preview(doc)
    assert preview["measuredEdges"] == len(doc.edges)
    for key in ("crossingsBefore", "crossingsAfter",
                "meanEdgeLengthBefore", "meanEdgeLengthAfter"):
        assert isinstance(preview[key], (int, float))
        assert math.isfinite(preview[key])
        assert preview[key] >= 0
    assert before == [(node.id, node.x, node.y) for node in doc.nodes.values()]
