"""Structural guard for the ADR-019 graph factory itself - NOT a timing/perf
test (that is stage 19.2/19.3's pytest-benchmark work, deliberately not
added here). This just proves the fixtures stay buildable and roughly the
right shape if SceneDocument's node-creation API changes underneath them,
so the factory can't silently rot into an inaccurate baseline generator."""

from backend.tests.perf.graph_factory import ALL_WORKLOADS, LARGE, SMALL, STRESS, TYPICAL, build_graph


def test_each_reference_workload_builds_the_expected_node_count():
    for workload in ALL_WORKLOADS:
        doc = workload.build()
        assert len(doc.nodes) == workload.node_count


def test_typical_workload_contains_the_expected_chart_and_image_nodes():
    doc = TYPICAL.build()
    kinds = [n.kind for n in doc.nodes.values()]
    assert kinds.count("chart") == TYPICAL.chart_count
    assert kinds.count("image") == TYPICAL.image_count
    assert kinds.count("chat") == TYPICAL.node_count - TYPICAL.chart_count - TYPICAL.image_count
    assert len(doc.nodes) == TYPICAL.node_count


def test_large_workload_contains_the_expected_chart_and_image_nodes():
    doc = LARGE.build()
    kinds = [n.kind for n in doc.nodes.values()]
    assert kinds.count("chart") == LARGE.chart_count
    assert kinds.count("image") == LARGE.image_count


def test_tree_topology_gives_every_non_root_node_exactly_one_parent_edge():
    # SMALL has no charts/images, so every edge beyond the tree's own N-1
    # is one of the deterministic extra cross-links - this isolates the
    # tree-shape guarantee from that additive noise.
    doc = build_graph(SMALL.node_count, content_bytes=SMALL.content_bytes, extra_edges=0)
    assert len(doc.edges) == SMALL.node_count - 1


def test_fixtures_are_fully_deterministic_across_two_builds():
    a = SMALL.build()
    b = SMALL.build()
    assert [n.id for n in a.nodes.values()] == [n.id for n in b.nodes.values()]
    assert a.scene_payload() == b.scene_payload()


def test_stress_workload_has_no_charts_or_images_by_design():
    # STRESS exists to answer "does it degrade or fall over", not to also
    # carry the render-cost noise chart/image nodes add (see measure_
    # baselines.py's own module docstring on add_chart_node's synchronous
    # render cost) - keeping it plain-chat-only isolates node-count scaling.
    doc = STRESS.build()
    kinds = [n.kind for n in doc.nodes.values()]
    assert kinds.count("chart") == 0
    assert kinds.count("image") == 0
