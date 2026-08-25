"""ADR-022 stage 22.2: property-based tests for graphlink_chart_data.py.

Covers canonicalize_chart_data's own documented non-mutation contract,
idempotency on already-canonical output, _assert_acyclic's cycle detection,
and _legacy_sankey_flows' O(1) size-bound rejection (the module's own
"60,000x-DoS-regression" comment - a size-vs-time property, not just a
correctness one).
"""

from __future__ import annotations

import copy
import time

from hypothesis import given, settings, strategies as st

import graphlink_chart_data as chart_data


_bar_payload_strategy = st.builds(
    lambda labels, values, title: {
        "type": "bar",
        "title": title,
        "labels": labels,
        "values": values,
    },
    labels=st.lists(st.text(min_size=1, max_size=15).filter(str.strip), min_size=1, max_size=20),
    values=st.lists(
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6), min_size=1, max_size=20
    ),
    title=st.text(max_size=30),
).filter(lambda payload: len(payload["labels"]) == len(payload["values"]))


@given(payload=_bar_payload_strategy)
def test_canonicalize_chart_data_does_not_mutate_its_input(payload):
    original = copy.deepcopy(payload)
    chart_data.canonicalize_chart_data(payload, "bar")
    assert payload == original


@given(payload=_bar_payload_strategy)
def test_canonicalize_chart_data_is_idempotent_on_already_canonical_output(payload):
    canonical = chart_data.canonicalize_chart_data(payload, "bar")
    twice_canonical = chart_data.canonicalize_chart_data(canonical, "bar")
    assert canonical == twice_canonical


@given(
    node_names=st.lists(
        st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=8),
        min_size=2,
        max_size=8,
        unique=True,
    )
)
def test_assert_acyclic_accepts_a_simple_chain_with_no_cycle(node_names):
    # A -> B -> C -> ... is acyclic by construction for any distinct name list.
    flows = [{"source": a, "target": b, "value": 1.0} for a, b in zip(node_names, node_names[1:])]
    chart_data._assert_acyclic(flows)  # must not raise


@given(
    node_names=st.lists(
        st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=8),
        min_size=2,
        max_size=8,
        unique=True,
    )
)
def test_assert_acyclic_rejects_a_cycle_of_any_length(node_names):
    # A -> B -> C -> ... -> A: closing the chain back to its own start is a
    # cycle regardless of how many distinct nodes are in it.
    chain = [{"source": a, "target": b, "value": 1.0} for a, b in zip(node_names, node_names[1:])]
    closing = {"source": node_names[-1], "target": node_names[0], "value": 1.0}
    try:
        chart_data._assert_acyclic(chain + [closing])
    except chart_data.ChartDataError:
        pass
    else:
        raise AssertionError("expected a ChartDataError for a closed cycle")


@given(oversized_link_count=st.integers(min_value=chart_data.MAX_SANKEY_FLOWS + 1, max_value=chart_data.MAX_SANKEY_FLOWS + 5))
@settings(deadline=None)  # this test's whole point is a wall-clock assertion; the ci deadline profile doesn't apply here
def test_legacy_sankey_flows_rejects_oversized_payload_in_roughly_constant_time(oversized_link_count):
    # Primary assertion: rejection happens at all (kills a mutant that drops
    # the bound check outright). Secondary: it happens within a generous
    # ceiling (same "class catcher, not a tight budget" philosophy as
    # backend/tests/perf/test_loop_watchdog.py) - a coarse guard against
    # reintroducing the pre-fix O(n) work before the bound was ever checked.
    payload = {
        "data": {
            "nodes": [{"name": f"n{i}"} for i in range(oversized_link_count)],
            "links": [{"source": 0, "target": 1, "value": 1} for _ in range(oversized_link_count)],
        }
    }
    started = time.perf_counter()
    try:
        chart_data._legacy_sankey_flows(payload)
    except chart_data.ChartDataError:
        pass
    else:
        raise AssertionError("expected a ChartDataError for an oversized legacy sankey payload")
    elapsed = time.perf_counter() - started
    # Generous ceiling for a shared/loaded CI runner (same "generous class
    # catcher" philosophy as backend/tests/perf/test_loop_watchdog.py) - the
    # real regression this guards was ~60,000x slower, not a few percent, so
    # this only needs to catch a gross reintroduction, not a tight budget.
    assert elapsed < 0.25
