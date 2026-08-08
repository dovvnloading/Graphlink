"""ADR-016 stage 16.5: deterministic, CI-safe coverage for the local eval
harness (backend/evals/). Every test here runs in the default (live=False)
mode, where backend.evals.runner patches api_provider.chat via
unittest.mock.patch.object itself - no real provider, no network, safe on
every CI run.
"""

from __future__ import annotations

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


# -- agent_build stub -----------------------------------------------------


def test_run_agent_build_fixture_always_reports_not_implemented_and_names_adr_008():
    fixture = EvalFixture(
        name="anything",
        kind="agent_build",
        input={"goal": "whatever"},
        scripted_response=None,
        expected=None,
    )
    result = run_agent_build_fixture(fixture)
    assert result.status == "not_implemented"
    assert "ADR-008" in result.detail


def test_run_agent_build_fixture_never_raises_regardless_of_input_shape():
    """The stub must never look hard enough at fixture content to crash on
    it - not even garbage scripted_response/expected values."""
    fixture = EvalFixture(
        name="garbage",
        kind="agent_build",
        input={},
        scripted_response="not json at all {{{",
        expected={"whatever": object()},
    )
    result = run_agent_build_fixture(fixture)
    assert result.status == "not_implemented"


def test_run_agent_build_fixture_never_scores_passed_or_failed():
    for fixture in load_fixtures("agent_build"):
        result = run_agent_build_fixture(fixture)
        assert result.status == "not_implemented"


# -- run_all ----------------------------------------------------------------


def test_run_all_covers_every_shipped_fixture_with_no_crashes():
    expected_count = sum(len(load_fixtures(kind)) for kind in ("chart", "structured_output", "agent_build"))
    assert expected_count > 0

    results = run_all()

    assert len(results) == expected_count
    assert all(isinstance(r, EvalResult) for r in results)
    assert all(r.status in ("passed", "failed", "not_implemented") for r in results)


def test_run_all_shipped_chart_and_structured_output_fixtures_all_pass():
    """The goldens actually shipped in backend/evals/fixtures/ must be
    correct, not just loadable - a regression in canonicalize_chart_data or
    respond_json should show up here."""
    results = run_all()
    scored = [r for r in results if r.kind != "agent_build"]
    assert scored, "expected at least one non-agent_build fixture"
    failures = [(r.fixture_name, r.detail) for r in scored if r.status != "passed"]
    assert failures == []


def test_run_all_agent_build_fixtures_are_all_not_implemented():
    results = run_all()
    agent_build_results = [r for r in results if r.kind == "agent_build"]
    assert agent_build_results
    assert all(r.status == "not_implemented" for r in agent_build_results)
