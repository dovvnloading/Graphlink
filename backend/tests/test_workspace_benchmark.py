"""Offline contract tests only: CI must never execute the live benchmark."""

from __future__ import annotations

import json

import pytest

from backend.evals.workspace_benchmark import CASES, Result, UsageMeter, _price, grade, main


def _activity(*names):
    return [{"tool": name, "outcome": "ok"} for name in names]


def test_fixed_cases_have_real_inputs_artifacts_and_tool_requirements():
    assert [case.name for case in CASES] == [
        "read_and_extract", "combine_two_files", "precise_edit", "search_and_deduplicate",
    ]
    assert all(case.inputs and case.expected and case.required_tools for case in CASES)
    assert len({case.name for case in CASES}) == len(CASES)


def test_grade_checks_exact_edit_and_tool_evidence(tmp_path):
    case = CASES[2]
    path = tmp_path / "config" / "runtime.ini"
    path.parent.mkdir()
    path.write_text(case.expected["config/runtime.ini"], encoding="utf-8")
    assert grade(case, tmp_path, "done", _activity("fs.read", "fs.edit"))[0]
    assert not grade(case, tmp_path, "done", _activity("fs.read", "fs.write"))[0]
    path.write_text(case.expected["config/runtime.ini"].replace("timeout_ms=500", "timeout_ms=600"), encoding="utf-8")
    passed, detail = grade(case, tmp_path, "done", _activity("fs.read", "fs.edit"))
    assert not passed and "unexpected changes" in detail


def test_grade_json_requires_correct_values_and_two_reads(tmp_path):
    case = CASES[1]
    for relpath, contents in case.inputs.items():
        path = tmp_path / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    (tmp_path / "totals.json").write_text(json.dumps(case.expected["totals.json"]), encoding="utf-8")
    assert grade(case, tmp_path, "done", _activity("fs.read", "fs.read", "fs.write"))[0]
    assert not grade(case, tmp_path, "done", _activity("fs.read", "fs.write"))[0]
    (tmp_path / "totals.json").write_text('{"lynx":19}', encoding="utf-8")
    assert not grade(case, tmp_path, "done", _activity("fs.read", "fs.read", "fs.write"))[0]
    assert not grade(case, tmp_path, "failed", _activity("fs.read", "fs.read", "fs.write"))[0]


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
