"""canonicalize_chart_data's own validation rules (Qt-removal plan R7.3).

R7.3 gap: graphlink_chart_data.py is a confirmed Qt-free survivor module
backend/ imports directly (from backend/domain/graph.py) - live production
logic, not legacy-only code. Before this file, its own validation/rejection
rules had no backend/tests coverage at all: backend/tests/test_canvas.py's
chart tests only cover the "unsupported chart_type string" rejection path
(SceneDocument.add_chart_node's own guard), never canonicalize_chart_data's
own ChartDataError branch for malformed-but-otherwise-valid-type data
(non-finite numbers, wrong container types, sankey cycles) - the exact
defensive `except ChartDataError` guard in canvas.py's generateChart wrapper
this file's absence left completely unexercised. Ported from graphlink_app/
tests/test_chart_nodes.py's own Qt-free test functions (the file's other
tests construct a real Qt ChatScene and stay behind).

ADR-013 stage 13.3 retired ChartDataAgent (graphlink_chart_agent.py) - this
file's own tests of its validate_chart_data wrapper went with it; the
canonicalize_chart_data tests below are unaffected (they exercise the
canonical validator directly, not through any wrapper)."""

import pytest

from graphlink_chart_data import CHART_SPEC_VERSION, ChartDataError, canonicalize_chart_data


def _bar_data(**overrides):
    data = {
        "type": "bar",
        "title": "Sales",
        "labels": ["A", "B", "C"],
        "values": [1, 2, 3],
    }
    data.update(overrides)
    return data


def test_canonicalize_chart_data_rejects_wrong_containers_and_non_finite_numbers():
    try:
        canonicalize_chart_data(_bar_data(values={"A": 1}), "bar")
        assert False, "expected ChartDataError for a non-list values container"
    except ChartDataError as exc:
        assert "both be lists" in str(exc)

    try:
        canonicalize_chart_data(_bar_data(values=[1, float("nan"), 3]), "bar")
        assert False, "expected ChartDataError for a non-finite value"
    except ChartDataError as exc:
        assert "finite" in str(exc)


def test_canonicalize_chart_data_aggregates_duplicate_sankey_flows_and_rejects_cycles():
    canonical = canonicalize_chart_data(
        {
            "type": "sankey",
            "flows": [
                {"source": "A", "target": "B", "value": 1},
                {"source": "A", "target": "B", "value": 2},
            ],
        }
    )
    assert canonical["flows"] == [{"source": "A", "target": "B", "value": 3.0}]

    try:
        canonicalize_chart_data(
            {
                "type": "sankey",
                "flows": [
                    {"source": "A", "target": "B", "value": 1},
                    {"source": "B", "target": "A", "value": 1},
                ],
            }
        )
        assert False, "expected ChartDataError for a sankey cycle"
    except ChartDataError as exc:
        assert "cannot contain cycles" in str(exc)


def test_an_oversized_legacy_sankey_payload_is_rejected_without_unbounded_work():
    """Regression: the legacy nested {data:{nodes,links}} shape was fully
    materialized (a full pass building `names` from every node, a full
    pass building `flows` from every link) BEFORE MAX_SANKEY_FLOWS was
    ever checked - unlike the direct {"flows": [...]} shape, rejected in
    O(1) by a plain len() that never touches an element. This runs on the
    FastAPI event loop when restoring a saved chat (session_load.py is not
    offloaded to a worker thread), so an oversized legacy-format payload
    blocked every other session on the process for however long the two
    passes took. Asserts wall-clock time directly, not an internal call
    count, so this test would also catch the bound being silently moved
    back after the loops in some future edit."""
    import time

    huge_links = [{"source": 0, "target": 1, "value": 1}] * 2_000_000
    payload = {"type": "sankey", "data": {"nodes": [{"name": "A"}, {"name": "B"}], "links": huge_links}}

    start = time.monotonic()
    with pytest.raises(ChartDataError, match="at most"):
        canonicalize_chart_data(payload, "sankey")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, (
        f"rejecting an oversized legacy sankey payload took {elapsed:.3f}s - "
        "the size check must run before the payload is materialized, not after"
    )


def test_an_oversized_legacy_sankey_node_list_is_also_rejected_without_unbounded_work():
    """The links-count bound alone would still leave the names-building
    loop unbounded whenever `nodes` is huge but `links` is small."""
    import time

    huge_nodes = [{"name": f"n{i}"} for i in range(2_000_000)]
    payload = {"type": "sankey", "data": {"nodes": huge_nodes, "links": [{"source": 0, "target": 1, "value": 1}]}}

    start = time.monotonic()
    with pytest.raises(ChartDataError, match="at most"):
        canonicalize_chart_data(payload, "sankey")
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"rejecting an oversized legacy sankey node list took {elapsed:.3f}s"


def test_canonicalize_chart_data_stamps_the_spec_version():
    # ADR-013 stage 13.1: every canonical shape carries an explicit,
    # typed version - a future migration reads this rather than sniffing
    # field presence to know which shape it's looking at.
    canonical = canonicalize_chart_data(_bar_data(), "bar")
    assert canonical["version"] == CHART_SPEC_VERSION == 1
