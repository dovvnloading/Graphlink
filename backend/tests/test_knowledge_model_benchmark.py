"""Pure scoring and invocation safeguards; the live benchmark never runs in CI."""

from __future__ import annotations

import pytest

from backend.evals import knowledge_model_benchmark as benchmark


def test_macro_averages_both_modes_without_a_quality_gate():
    details = [
        {"scores": {
            "keyword_only": {"recall_at_5": 0.0, "mrr": 0.0},
            "hybrid": {"recall_at_5": 1.0, "mrr": 1.0},
        }},
        {"scores": {
            "keyword_only": {"recall_at_5": 0.5, "mrr": 0.25},
            "hybrid": {"recall_at_5": 0.0, "mrr": 0.0},
        }},
    ]
    assert benchmark._metrics(details) == {
        "keyword_only": {"recall_at_5": 0.25, "mrr": 0.125},
        "hybrid": {"recall_at_5": 0.5, "mrr": 0.5},
    }
    with pytest.raises(ValueError, match="at least one question"):
        benchmark._metrics([])


def test_cli_requires_opt_in_before_model_or_fixture_access(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(benchmark, "run_benchmark", lambda: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as error:
        benchmark.main([])
    assert error.value.code == 2
    assert "--run" in capsys.readouterr().err


@pytest.mark.parametrize("environment", ["CI", "GITHUB_ACTIONS"])
def test_live_benchmark_is_disabled_in_ci_even_with_opt_in(monkeypatch, environment, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv(environment, "true")
    real_run = benchmark.run_benchmark
    monkeypatch.setattr(benchmark, "run_benchmark", lambda: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--run"])
    assert error.value.code == 2
    assert "disabled in CI" in capsys.readouterr().err
    # The direct Python API refuses CI too, before any settings or DB access.
    with pytest.raises(RuntimeError, match="disabled in CI"):
        real_run(settings_manager=object())


def test_cli_reports_worse_hybrid_scores_without_failing(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(benchmark, "run_benchmark", lambda: {
        "metrics": {
            "keyword_only": {"recall_at_5": 1.0, "mrr": 1.0},
            "hybrid": {"recall_at_5": 0.0, "mrr": 0.0},
        },
    })
    assert benchmark.main(["--run"]) == 0
    output = capsys.readouterr().out
    assert '"keyword_only"' in output
    assert '"hybrid"' in output
