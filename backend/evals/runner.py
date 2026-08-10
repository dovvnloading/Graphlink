"""ADR-016 stage 16.5 eval scorers: one function per eval dimension, plus
``run_all`` to run every shipped fixture across all three.

Default (``live=False``) mode patches ``api_provider.chat`` with
``unittest.mock.patch.object`` so a run is fully deterministic and safe on
any machine, with no configured provider and no network - this is the mode
backend/tests/test_evals_harness.py exercises, and the mode
``python -m backend.evals`` uses unless ``--live`` is passed. ``patch.object``
is used (not pytest's own ``monkeypatch`` fixture) specifically because this
module must also work as a plain script outside pytest - see
backend/evals/README.md.

``live=True`` skips the patch and lets api_provider.chat() reach whatever
provider is actually configured (a real API key, or a running Ollama/
llama.cpp) - a human running this manually, per the ADR's own "+ optional
LLM-judge" language. This module does not implement judging logic beyond
that straight call-through: there is no scoring model in the loop, only the
same deterministic scorers used in patched mode, now fed a real response.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest import mock

import api_provider
import graphlink_task_config as config
from graphlink_chart_data import CHART_JSON_SCHEMAS, ChartDataError, canonicalize_chart_data, chart_generation_messages

from backend.evals.fixtures import EvalFixture, load_fixtures
from backend.structured_output import StructuredOutputError, respond_json

# Valid EvalResult.status values. "not_implemented" is informational, never
# a failure - see run_agent_build_fixture and backend/evals/__main__.py's
# exit-code logic.
_STATUSES = ("passed", "failed", "not_implemented")


@dataclass(frozen=True)
class EvalResult:
    fixture_name: str
    kind: str
    status: str  # one of _STATUSES
    detail: str


def _pretty(data) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _mismatch_detail(actual, expected) -> str:
    return (
        "output did not match the golden fixture's expected value.\n"
        f"--- actual ---\n{_pretty(actual)}\n"
        f"--- expected ---\n{_pretty(expected)}"
    )


def _scripted_chat(fixture: EvalFixture):
    """The single fake-response seam: every call to api_provider.chat()
    returns fixture.scripted_response verbatim, regardless of task/messages/
    kwargs. Works both inside pytest and as a standalone script (unlike
    pytest's own monkeypatch fixture, which only exists inside a test)."""
    return mock.patch.object(
        api_provider, "chat", return_value={"message": {"content": fixture.scripted_response}}
    )


# -- chart ------------------------------------------------------------------


def run_chart_fixture(fixture: EvalFixture, *, live: bool = False) -> EvalResult:
    """ADR-013 stage 13.3: drives the real backend.agents._call_chart_agent
    path's own machinery directly - respond_json against
    graphlink_chart_data.CHART_JSON_SCHEMAS/chart_generation_messages, the
    exact schema+messages backend/agents.py's _call_chart_agent builds -
    then canonicalizes the result exactly the way backend/api/
    intents_chart.py's generate_chart does downstream
    (``canonicalize_chart_data(result, chart_type)``) before diffing against
    fixture.expected. Calls respond_json directly rather than importing
    backend.agents (a much heavier module - SessionBus/RunLifecycle/etc -
    for a single function this eval doesn't otherwise need)."""
    chart_type = fixture.input["chart_type"]
    source_text = fixture.input["source_text"]
    messages = chart_generation_messages(source_text, chart_type)
    schema = CHART_JSON_SCHEMAS[chart_type]

    try:
        if live:
            parsed = respond_json(config.TASK_CHART, messages, schema, schema_name=f"{chart_type}_chart")
        else:
            with _scripted_chat(fixture):
                parsed = respond_json(config.TASK_CHART, messages, schema, schema_name=f"{chart_type}_chart")
    except StructuredOutputError as exc:
        return EvalResult(fixture.name, "chart", "failed", f"respond_json raised StructuredOutputError: {exc}")

    try:
        canonical = canonicalize_chart_data(parsed, chart_type)
    except ChartDataError as exc:
        return EvalResult(fixture.name, "chart", "failed", f"agent output failed canonicalization: {exc}")

    if fixture.expected is not None and canonical != fixture.expected:
        return EvalResult(fixture.name, "chart", "failed", _mismatch_detail(canonical, fixture.expected))

    return EvalResult(fixture.name, "chart", "passed", "canonical chart output matched the golden fixture")


# -- structured_output --------------------------------------------------


def run_structured_output_fixture(fixture: EvalFixture, *, live: bool = False) -> EvalResult:
    """Drives the real backend.structured_output.respond_json against
    fixture.input's schema/messages, and diffs the parsed dict against
    fixture.expected."""
    task = fixture.input.get("task", "task_chat")
    schema = fixture.input["schema"]
    schema_name = fixture.input.get("schema_name", "response")
    messages = fixture.input["messages"]

    try:
        if live:
            result = respond_json(task, messages, schema, schema_name=schema_name)
        else:
            with _scripted_chat(fixture):
                result = respond_json(task, messages, schema, schema_name=schema_name)
    except StructuredOutputError as exc:
        return EvalResult(
            fixture.name, "structured_output", "failed",
            f"respond_json raised StructuredOutputError: {exc}",
        )

    if fixture.expected is not None and result != fixture.expected:
        return EvalResult(fixture.name, "structured_output", "failed", _mismatch_detail(result, fixture.expected))

    return EvalResult(fixture.name, "structured_output", "passed", "parsed object matched the golden fixture")


# -- agent_build (ADR-008 stub) ----------------------------------------------


def run_agent_build_fixture(fixture: EvalFixture, *, live: bool = False) -> EvalResult:
    """ADR-008's Builder agent tool-use loop (plan -> propose tool call ->
    execute -> observe -> checkpoint) does not exist as shipped code - it is
    design-doc text only (doc/adr/ADR-008-agentic-graph-construction.md,
    stages 8.1-8.6, all pending: no create_node/run_node tool, no loop, no
    checkpoints). Scoring "did the build succeed" against nothing to run is
    not meaningful, so this dimension is honest scaffolding: it ALWAYS
    reports not_implemented, on every fixture, regardless of input - never a
    fake pass, and never an exception that would misread as a harness bug
    once a real loop lands here. ``live`` is accepted for interface
    symmetry with the other two runners but has no effect."""
    return EvalResult(
        fixture.name,
        "agent_build",
        "not_implemented",
        "ADR-008 Builder agent loop is not implemented yet - this dimension is "
        "scaffolding only, see doc/adr/ADR-008-agentic-graph-construction.md",
    )


# -- run everything -----------------------------------------------------


def run_all(*, live: bool = False) -> list[EvalResult]:
    """Loads and runs every fixture across all three eval kinds, in a
    stable order (chart, then structured_output, then agent_build)."""
    results: list[EvalResult] = []
    for kind, run_fixture in (
        ("chart", run_chart_fixture),
        ("structured_output", run_structured_output_fixture),
        ("agent_build", run_agent_build_fixture),
    ):
        for fixture in load_fixtures(kind):
            results.append(run_fixture(fixture, live=live))
    return results
