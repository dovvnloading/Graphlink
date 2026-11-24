"""CI-safe task-level workflow evals and regression gates on all metrics."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.evals import workflow_tasks as evals


@pytest.fixture(autouse=True)
def _offline_network_guard(monkeypatch):
    # Even the mocked live-comparison test must never reach a real provider.
    def deny(*args, **kwargs):
        raise AssertionError("workflow evaluation attempted a real network call")

    monkeypatch.setattr(httpx.Client, "send", deny)
    monkeypatch.setattr(httpx.AsyncClient, "send", deny)


@pytest.mark.parametrize(("name", "steps", "cost", "retries", "effects"), [
    ("research-summary", 2, 0.03, 0, 2),
    ("table-chart-report", 3, 0.02, 0, 1),
    ("conditional-repair", 2, 0.04, 1, 2),
    ("mapped-summaries", 4, 0.06, 0, 3),
])
def test_scripted_task_metrics(tmp_path, name, steps, cost, retries, effects, record_property):
    result = asyncio.run(evals.evaluate_task(tmp_path / "task.db", name))
    assert result.task == name
    assert result.success
    assert result.step_count == steps
    assert result.cost_usd == cost
    assert result.retries == retries
    assert result.wrong_side_effect_count == 0
    assert result.effect_count == effects
    assert result.run_statuses == ("completed",) * (retries + 1)
    for metric in ("success", "step_count", "cost_usd", "retries", "wrong_side_effect_count"):
        record_property(metric, getattr(result, metric))


def test_repair_stops_on_success_or_exhaustion(tmp_path):
    passed = asyncio.run(evals.evaluate_task(tmp_path / "passed.db", "conditional-repair",
                                            overrides={"draft": "7"}))
    assert passed.success and passed.retries == 0 and passed.step_count == 1
    assert passed.effect_count == 1 and passed.cost_usd == 0.02
    exhausted = asyncio.run(evals.evaluate_task(
        tmp_path / "exhausted.db", "conditional-repair",
        overrides={"repair-1": "8", "repair-2": "10"},
    ))
    assert not exhausted.success
    assert exhausted.retries == 2 and exhausted.step_count == 3
    assert exhausted.effect_count == 3 and exhausted.cost_usd == 0.06
    assert exhausted.wrong_side_effect_count == passed.wrong_side_effect_count == 0


@pytest.mark.parametrize(("task", "overrides"), [
    ("research-summary", {"summary": "Trial A lasted 8 hours."}),
    ("table-chart-report", {"chart-summary": "West total 4; East total 5."}),
    ("mapped-summaries", {"map-1": "Beta: 999"}),
])
def test_wrong_model_output_does_not_score_success(tmp_path, task, overrides):
    result = asyncio.run(evals.evaluate_task(tmp_path / "wrong.db", task, overrides=overrides))
    assert not result.success
    assert result.run_statuses == ("completed",)
    assert result.wrong_side_effect_count == 0


def test_wrong_side_effect_metric_counts_duplicates_and_unapproved_actions(tmp_path):
    from backend.workflow.contracts import StepOutputSource, WorkflowInputSource

    async def scenario():
        task = evals.TaskRun(tmp_path / "forbidden.db", "forbidden", evals.SCRIPTED, None)
        # Even a workflow with preview approval must not gain real code
        # execution through the evaluation host.
        from backend.workflow.contracts import TextValue
        forbidden = task.step("forbidden", "python.execute", {
            "code": "raise RuntimeError('must not execute')",
            "inputs": {"subject": "text"}, "output_kind": "text",
        }, subject=WorkflowInputSource("text"))
        try:
            output = await task.run([forbidden], {"text": TextValue("synthetic")}, [
                ("text", "text", StepOutputSource("forbidden", "text")),
            ])
            assert output == {}
            result = task.result(True)
            assert not result.success and result.run_statuses == ("failed",)
            assert result.wrong_side_effect_count == 1 and result.effect_count == 1
        finally:
            await task.engine.shutdown()

    asyncio.run(scenario())
    model = ("model.generate", "summary", 1)
    assert evals.wrong_effect_count([model, model, ("model.generate", "summary", 2),
                                    ("python.execute", "write", 1)], [model]) == 3


def test_live_comparison_requires_opt_in_and_is_disabled_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(RuntimeError, match="disabled in CI"):
        asyncio.run(evals.comparison(["qwen3:8b"]))
    with pytest.raises(SystemExit) as exc:
        evals.main(["--model", "qwen3:8b"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        evals.main(["--live"])
    assert exc.value.code == 2


def test_comparison_records_model_version_and_price_with_mocked_transport(monkeypatch, capsys):
    from backend.providers import graph_local_ollama

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    tag_calls, model_calls = [], []

    async def tags(self, path):
        assert path == "/api/tags"
        tag_calls.append(path)
        return httpx.Response(200, request=httpx.Request("GET", "http://127.0.0.1:11434/api/tags"),
                              json={"models": [{"name": "qwen3:8b", "digest": "a" * 64}]})

    async def complete(*, prompt, model_id, endpoint):
        assert model_id == "qwen3:8b" and endpoint == "http://127.0.0.1:11434"
        model_calls.append(prompt)
        if "Summarize both trials" in prompt:
            reply = evals.RESEARCH_ANSWER
        elif "Report both region totals" in prompt:
            reply = evals.CHART_ANSWER
        elif "Return only the integer result" in prompt:
            reply = "7"
        else:
            reply, = [answer for document, answer in zip(evals.DOCUMENTS, evals.MAP_ANSWERS, strict=True)
                      if document in prompt]
        return {"message": {"content": reply}}

    monkeypatch.setattr(httpx.AsyncClient, "get", tags)
    monkeypatch.setattr(graph_local_ollama, "async_local_ollama_complete", complete)
    assert evals.main(["--live", "--model", "qwen3:8b"]) == 0
    offline, live = json.loads(capsys.readouterr().out)
    assert offline["model"] == evals.SCRIPTED.model
    assert offline["version"] == evals.SCRIPTED.version
    assert offline["price"]["model_per_call"] == 0.02
    assert all(task["success"] for task in offline["tasks"])
    assert live["model"] == "qwen3:8b" and live["version"] == "a" * 64
    assert live["price"]["currency"] == "USD" and live["price"]["model_per_call"] == 0
    assert all(task["success"] and task["cost_usd"] == 0 and task["wrong_side_effect_count"] == 0
               for task in live["tasks"])
    assert live["tasks"][2]["retries"] == 0
    assert len(tag_calls) == 2 and len(model_calls) == 6
