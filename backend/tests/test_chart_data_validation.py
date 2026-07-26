"""canonicalize_chart_data's own validation rules, and ChartDataAgent.
validate_chart_data's use of that same contract (Qt-removal plan R7.3).

R7.3 gap: graphlink_chart_data.py and graphlink_chart_agent.py are both
confirmed Qt-free survivor modules backend/ imports directly (canvas.py:32,
agents.py:75) - live production logic, not legacy-only code. Before this
file, their own validation/rejection rules had no backend/tests coverage at
all: backend/tests/test_canvas.py's chart tests only cover the "unsupported
chart_type string" rejection path (SceneDocument.add_chart_node's own guard),
never canonicalize_chart_data's own ChartDataError branch for malformed-but-
otherwise-valid-type data (non-finite numbers, wrong container types, sankey
cycles) - the exact defensive `except ChartDataError` at canvas.py:3503 this
file's absence left completely unexercised. Ported from graphlink_app/tests/
test_chart_nodes.py's own Qt-free test functions (the file's other tests
construct a real Qt ChatScene and stay behind).
"""

from graphlink_chart_agent import ChartDataAgent
from graphlink_chart_data import ChartDataError, canonicalize_chart_data


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


def test_chart_data_agent_validate_chart_data_uses_the_canonical_contract():
    # ChartDataAgent.validate_chart_data (graphlink_chart_agent.py:541-558) is
    # a thin wrapper: call canonicalize_chart_data, report (True, None) on
    # success or (False, message) on ChartDataError. This proves the wrapper
    # actually delegates rather than reimplementing (and drifting from) its
    # own copy of the validation rules.
    agent = ChartDataAgent()

    valid, error = agent.validate_chart_data(_bar_data(values="123"), "bar")
    assert valid is False
    assert "both be lists" in error

    valid, error = agent.validate_chart_data(
        {"type": "sankey", "flows": [{"source": "A", "target": "B", "value": float("nan")}]},
        "sankey",
    )
    assert valid is False
    assert "finite" in error

    valid, error = agent.validate_chart_data(_bar_data(), "bar")
    assert valid is True
    assert error is None


def test_chart_data_agent_validate_chart_data_canonicalizes_the_dict_in_place():
    # validate_chart_data's own contract (graphlink_chart_agent.py:555-556):
    # on success it clears and repopulates the SAME dict with the canonical
    # shape, rather than returning a new one - callers rely on the caller's
    # own `data` reference already being canonical afterward.
    agent = ChartDataAgent()
    data = _bar_data(values=["1", "2", "3"])  # strings - canonical form is floats

    valid, error = agent.validate_chart_data(data, "bar")

    assert valid is True
    assert error is None
    assert data["values"] == [1.0, 2.0, 3.0]
