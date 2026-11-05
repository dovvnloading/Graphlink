"""ADR-016 stage 16.5: deterministic, CI-safe coverage for the local eval
harness (backend/evals/). Every test here runs in the default (live=False)
mode, where backend.evals.runner patches api_provider.chat via
unittest.mock.patch.object itself - no real provider, no network, safe on
every CI run.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from backend.evals.fixtures import EvalFixture, load_fixtures
from backend.evals.runner import (
    EvalResult,
    run_agent_build_fixture,
    run_all,
    run_chart_fixture,
    run_structured_output_fixture,
)


# -- fixture loading ----------------------------------------------------


def test_load_fixtures_finds_the_shipped_chart_goldens():
    fixtures = load_fixtures("chart")
    assert len(fixtures) >= 3
    assert all(isinstance(f, EvalFixture) for f in fixtures)
    assert all(f.kind == "chart" for f in fixtures)
    chart_types = {f.input["chart_type"] for f in fixtures}
    assert {"bar", "line", "pie"} <= chart_types


def test_load_fixtures_finds_the_shipped_structured_output_golden():
    fixtures = load_fixtures("structured_output")
    assert len(fixtures) >= 1
    assert all(f.kind == "structured_output" for f in fixtures)


def test_load_fixtures_finds_the_shipped_agent_build_placeholder():
    fixtures = load_fixtures("agent_build")
    assert len(fixtures) >= 1
    assert all(f.kind == "agent_build" for f in fixtures)


def test_load_fixtures_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        load_fixtures("not_a_real_kind")


# -- chart scorer ---------------------------------------------------------


def _chart_fixture_by_name(name):
    fixtures = {f.name: f for f in load_fixtures("chart")}
    return fixtures[name]


def test_a_correct_bar_chart_fixture_scores_passed():
    fixture = _chart_fixture_by_name("bar_support_tickets")
    result = run_chart_fixture(fixture)
    assert result.status == "passed", result.detail
    assert result.kind == "chart"
    assert result.fixture_name == "bar_support_tickets"


def test_a_correct_line_chart_fixture_scores_passed():
    fixture = _chart_fixture_by_name("line_daily_active_users")
    result = run_chart_fixture(fixture)
    assert result.status == "passed", result.detail


def test_a_correct_pie_chart_fixture_scores_passed():
    # Pins the exact case the harness must actually catch by RUNNING
    # canonicalize_chart_data rather than guessing: pie's own model-facing
    # prompt never asks for xAxis/yAxis, but canonicalize_chart_data still
    # defaults them in ("Sequence"/"Value") since pie is in the same
    # {"bar","line","pie"} branch as the other two - the shipped fixture's
    # `expected` includes them for exactly that reason.
    fixture = _chart_fixture_by_name("pie_engineering_budget")
    assert "xAxis" not in fixture.scripted_response
    assert fixture.expected["xAxis"] == "Sequence"
    assert fixture.expected["yAxis"] == "Value"

    result = run_chart_fixture(fixture)
    assert result.status == "passed", result.detail


def test_a_deliberately_wrong_chart_fixture_scores_failed():
    """Proves the scorer actually discriminates, not just always reports
    success: mutate one expected value away from what
    canonicalize_chart_data really produces, and confirm the harness
    reports failed rather than silently passing."""
    original = _chart_fixture_by_name("bar_support_tickets")
    wrong_expected = dict(original.expected)
    wrong_expected["values"] = [999999] + list(wrong_expected["values"][1:])
    wrong_fixture = dataclasses.replace(original, expected=wrong_expected)

    result = run_chart_fixture(wrong_fixture)

    assert result.status == "failed"
    assert "did not match" in result.detail


def test_chart_fixture_with_an_error_scripted_response_scores_failed():
    """The agent's own fully-degraded case (a top-level "error" key, its
    own heuristic fallback found nothing usable either) must score failed,
    not raise and not silently pass."""
    fixture = EvalFixture(
        name="unanswerable",
        kind="chart",
        input={"chart_type": "bar", "source_text": "asdf"},
        scripted_response='{"error": "Could not find sufficient data to generate a bar chart."}',
        expected=None,
    )
    result = run_chart_fixture(fixture)
    assert result.status == "failed"
    assert "error" in result.detail.lower()


# -- structured_output scorer ----------------------------------------------


def test_the_structured_output_fixture_scores_passed():
    fixtures = load_fixtures("structured_output")
    assert fixtures, "expected at least one shipped structured_output fixture"
    result = run_structured_output_fixture(fixtures[0])
    assert result.status == "passed", result.detail
    assert result.kind == "structured_output"


def test_a_deliberately_wrong_structured_output_fixture_scores_failed():
    original = load_fixtures("structured_output")[0]
    wrong_expected = dict(original.expected)
    wrong_expected["confidence"] = 0.0
    wrong_fixture = dataclasses.replace(original, expected=wrong_expected)

    result = run_structured_output_fixture(wrong_fixture)

    assert result.status == "failed"


# -- real Builder evals -------------------------------------------------------


def _builder_fixture(name: str) -> EvalFixture:
    return {f.name: f for f in load_fixtures("agent_build")}[name]


def test_every_shipped_builder_fixture_scores_passed():
    fixtures = load_fixtures("agent_build")
    assert len(fixtures) >= 5, "the Builder scenarios must not be placeholders"
    for fixture in fixtures:
        result = run_agent_build_fixture(fixture)
        assert result.status == "passed", (fixture.name, result.detail)
        assert "real Builder" in result.detail


def test_builder_eval_detects_wrong_graph_output():
    original = _builder_fixture("research_and_summarize_branch")
    wrong = dict(original.expected)
    wrong["nodes"] = [{"kind": "note", "content": "fabricated result"}]

    result = run_agent_build_fixture(dataclasses.replace(original, expected=wrong))
    assert result.status == "failed"
    assert "nodes did not match" in result.detail


def test_builder_eval_detects_an_exhausted_model_script():
    original = _builder_fixture("tool_error_recovery")
    bad_input = copy.deepcopy(original.input)
    bad_input["runs"][0]["turns"].pop()

    result = run_agent_build_fixture(dataclasses.replace(original, input=bad_input))
    assert result.status == "failed"
    assert "script exhausted" in result.detail


def test_builder_eval_rejects_an_unexpected_approval():
    original = _builder_fixture("copilot_denial")
    bad_input = copy.deepcopy(original.input)
    bad_input["runs"][0]["approvals"] = []

    result = run_agent_build_fixture(dataclasses.replace(original, input=bad_input))
    assert result.status == "failed"
    assert "approval script mismatch" in result.detail


def test_builder_eval_rejects_unsafe_tool_names_before_invocation():
    original = _builder_fixture("tool_error_recovery")
    bad_input = copy.deepcopy(original.input)
    bad_input["runs"][0]["turns"][0]["tool_calls"][0]["name"] = "run_node"

    result = run_agent_build_fixture(dataclasses.replace(original, input=bad_input))
    assert result.status == "failed"
    assert "outside the offline eval allowlist" in result.detail


def test_builder_eval_invalid_fixture_is_failed_not_na_or_exception():
    fixture = EvalFixture(
        name="missing-script", kind="agent_build", input={"goal": "hello"},
        scripted_response=None, expected=None,
    )
    result = run_agent_build_fixture(fixture)
    assert result.status == "failed"
    assert "expected result object" in result.detail


def test_builder_eval_stays_offline_even_when_live_flag_is_requested():
    result = run_agent_build_fixture(_builder_fixture("step_budget_resume"), live=True)
    assert result.status == "passed", result.detail


# -- run_all ----------------------------------------------------------------


def test_run_all_covers_every_shipped_fixture_with_no_crashes():
    expected_count = sum(len(load_fixtures(kind)) for kind in ("chart", "structured_output", "agent_build"))
    assert expected_count > 0

    results = run_all()

    assert len(results) == expected_count
    assert all(isinstance(r, EvalResult) for r in results)
    assert all(r.status in ("passed", "failed", "not_implemented") for r in results)


def test_run_all_every_shipped_fixture_passes_including_builder():
    """All dimensions now score real behavior and reject broken goldens."""
    results = run_all()
    failures = [(r.kind, r.fixture_name, r.detail) for r in results if r.status != "passed"]
    assert failures == []
    assert any(r.kind == "agent_build" for r in results)
