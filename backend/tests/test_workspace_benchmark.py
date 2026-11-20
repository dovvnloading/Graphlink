"""Offline contract tests only: CI must never execute the live benchmark."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.evals import workspace_benchmark as benchmark
from backend.evals.workspace_benchmark import CASES, Result, UsageMeter, _price, grade, main


def _activity(*calls):
    return [{"tool": tool, "outcome": "ok", "path": path} for tool, path in calls]


def test_fixed_cases_have_real_inputs_artifacts_and_tool_requirements():
    assert [case.name for case in CASES] == [
        "read_and_extract", "combine_two_files", "precise_edit", "search_and_deduplicate",
    ]
    assert all(case.inputs and case.expected and case.required_tools and case.required_paths for case in CASES)
    assert len({case.name for case in CASES}) == len(CASES)


def test_grade_checks_exact_edit_and_tool_evidence(tmp_path):
    case = CASES[2]
    path = tmp_path / "config" / "runtime.ini"
    path.parent.mkdir()
    path.write_text(case.expected["config/runtime.ini"], encoding="utf-8")
    valid = _activity(("fs.read", "config/runtime.ini"), ("fs.edit", "config/runtime.ini"))
    assert grade(case, tmp_path, "done", valid)[0]
    assert not grade(case, tmp_path, "done", _activity(
        ("fs.read", "config/runtime.ini"), ("fs.write", "config/runtime.ini"),
    ))[0]
    passed, detail = grade(case, tmp_path, "done", valid + _activity(("fs.write", "config/runtime.ini")))
    assert not passed and "forbidden successful fs.write" in detail
    assert not grade(case, tmp_path, "done", _activity(
        ("fs.read", "config/runtime.ini"), ("fs.edit", "other.ini"),
    ))[0]
    # A denied/failed rewrite did not modify the file and is not a successful forbidden operation.
    assert grade(case, tmp_path, "done", valid + [
        {"tool": "fs.write", "outcome": "error", "path": "config/runtime.ini"},
    ])[0]
    path.write_text(case.expected["config/runtime.ini"].replace("timeout_ms=500", "timeout_ms=600"), encoding="utf-8")
    passed, detail = grade(case, tmp_path, "done", valid)
    assert not passed and "unexpected changes" in detail


def test_grade_json_requires_correct_values_and_distinct_source_reads(tmp_path):
    case = CASES[1]
    for relpath, contents in case.inputs.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    (tmp_path / "totals.json").write_text(json.dumps(case.expected["totals.json"]), encoding="utf-8")
    valid = _activity(
        ("fs.read", "reports/january.csv"),
        ("fs.read", "./reports/february.csv"),
        ("fs.write", "totals.json"),
    )
    assert grade(case, tmp_path, "done", valid)[0]
    duplicate_reads = _activity(
        ("fs.read", "reports/january.csv"),
        ("fs.read", "reports/january.csv"),
        ("fs.write", "totals.json"),
    )
    passed, detail = grade(case, tmp_path, "done", duplicate_reads)
    assert not passed and "reports/february.csv" in detail
    assert not grade(case, tmp_path, "done", valid[1:])[0]
    # Display-log rows without structured paths cannot verify either input.
    assert not grade(case, tmp_path, "done", [
        {"tool": "fs.read", "outcome": "ok"},
        {"tool": "fs.read", "outcome": "ok"},
        {"tool": "fs.write", "outcome": "ok"},
    ])[0]
    (tmp_path / "totals.json").write_text('{"lynx":19}', encoding="utf-8")
    assert not grade(case, tmp_path, "done", valid)[0]
    assert not grade(case, tmp_path, "failed", valid)[0]


def test_drive_grades_full_tool_audit_not_capped_canvas_activity(tmp_path, monkeypatch):
    """Required calls may have fallen out of the last 100 UI rows."""
    from backend.harness import workspace as workspace_module
    from backend.providers.base import ToolCall
    from backend.tools import ToolRegistry, ToolResult

    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "workspaces")

    async def fake_invoke(self, call, context):
        return ToolResult(content="ok")

    monkeypatch.setattr(ToolRegistry, "invoke", fake_invoke)

    async def fake_harness(*, document, harness_node_id, registry, **_kwargs):
        state = document.nodes[harness_node_id].state
        calls = [
            ("fs.read", "config/runtime.ini"),
            ("fs.edit", "config/runtime.ini"),
            *[("fs.list", None) for _ in range(101)],
        ]
        for index, (name, path) in enumerate(calls):
            await registry.invoke(ToolCall(str(index), name, {"path": path} if path else {}), None)
        # Simulate the production 100-row display cap, with both required calls gone.
        state.harness_activity = [{"tool": name, "outcome": "ok"} for name, _ in calls][-100:]
        workspace = workspace_module.ensure_workspace(state.harness_workspace_id)
        (workspace / "config/runtime.ini").write_text(CASES[2].expected["config/runtime.ini"], encoding="utf-8")
        state.harness_spent_turns = 2
        state.harness_status = "done"

    monkeypatch.setattr("backend.harness.loop.run_harness", fake_harness)
    result = asyncio.run(benchmark._drive(CASES[2], object(), SimpleNamespace(model_id="test"), 2, 3))
    assert result.passed, result.detail
    assert result.tools[:2] == ("fs.read", "fs.edit")
    assert len(result.tools) == 103
    assert result.tools.count("fs.list") == 101


def test_drive_keeps_observed_provider_usage_after_late_error(tmp_path, monkeypatch):
    import api_provider
    from backend.harness import workspace as workspace_module

    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api_provider, "chat_turn_with_tools", lambda *_a, **_kw: {
        "message": {"content": "done"}, "tool_calls": [],
        "usage": {"prompt_tokens": 23, "completion_tokens": 5},
    })

    async def broken_harness(**_kwargs):
        api_provider.chat_turn_with_tools("task_chat", [{"role": "user", "content": "hello"}], ())
        await asyncio.sleep(0.01)
        raise RuntimeError("failed after a billed turn")

    monkeypatch.setattr("backend.harness.loop.run_harness", broken_harness)
    result = asyncio.run(benchmark._drive(CASES[0], object(), SimpleNamespace(model_id="test"), 2, 3))
    assert result.status == "error"
    assert not result.passed and "failed after a billed turn" in result.detail
    assert result.seconds > 0
    assert (result.prompt_tokens, result.completion_tokens, result.metered_turns) == (23, 5, 1)
    assert not result.usage_complete
    assert _price(result, (1.0, 1.0)) is None


def test_suite_error_records_time_and_unknown_usage_instead_of_zero(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(benchmark, "_runtime",
                        lambda *_args: (object(), SimpleNamespace(model_id="test")))

    async def delayed_error(*_args):
        await asyncio.sleep(0.02)
        raise RuntimeError("late failure")

    monkeypatch.setattr(benchmark, "_drive", delayed_error)
    report = asyncio.run(benchmark.run_suite("OpenAI", "test", prices=(1.0, 1.0)))
    assert all(not row["passed"] and row["status"] == "error" for row in report["cases"])
    assert all(row["seconds"] > 0 and not row["usage_complete"] for row in report["cases"])
    assert report["summary"]["seconds"] > 0
    assert report["summary"]["prompt_tokens"] is None
    assert report["summary"]["completion_tokens"] is None
    assert report["summary"]["cost_usd"] is None
    assert not report["summary"]["usage_complete"]
    benchmark._print_report(report)
    output = capsys.readouterr().out
    assert "unknown input" in output
    assert "n/a (incomplete usage)" in output


def test_meter_prefers_reported_tokens_and_labels_fallback(monkeypatch):
    monkeypatch.setattr(UsageMeter, "_count", staticmethod(lambda _: 3))
    meter = UsageMeter()
    def reported(*_args, **_kwargs):
        return {"message": {"content": "done"}, "tool_calls": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 7}}
    def unreported(*_args, **_kwargs):
        return {"message": {"content": "done"}, "tool_calls": [], "usage": None}
    meter.capture(reported, "task_chat", [{"role": "user", "content": "hi"}])
    meter.capture(unreported, "task_chat", [{"role": "user", "content": "hi"}])
    assert (meter.prompt_tokens, meter.completion_tokens) == (103, 10)
    assert (meter.provider_reported_turns, meter.metered_turns) == (1, 2)


def test_price_refuses_to_guess_unknown_rate():
    result = Result("test", True, "done", "ok", 1.0, 1, 1_000_000, 500_000, 1, 1, None, ())
    assert _price(result, (2.0, 4.0)) == 4.0
    assert _price(result, None) is None
    assert _price(Result(**{**result.__dict__, "usage_complete": False}), (2.0, 4.0)) is None


def test_cli_requires_opt_in_and_refuses_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--provider", "Ollama", "--model", "test"])
    assert exc.value.code == 2
    monkeypatch.setenv("CI", "true")
    with pytest.raises(SystemExit) as exc:
        main(["--run", "--provider", "Ollama", "--model", "test"])
    assert exc.value.code == 2
