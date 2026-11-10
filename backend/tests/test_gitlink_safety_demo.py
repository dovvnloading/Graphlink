"""Keep the runnable demonstration honest against production code, not a mock gate."""

import json
import os
import subprocess
import sys

import pytest

from tools.gitlink_safety_demo import REPO_ROOT, SCENARIOS, make_repo, run_scenario


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_disposable_repository_safety_demo(scenario):
    result = run_scenario(scenario)
    assert result["outcome"]


def test_fixture_git_init_does_not_obey_inherited_repository_routing(tmp_path, monkeypatch):
    outside = tmp_path / "outside-git-dir"
    monkeypatch.setenv("GIT_DIR", str(outside))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "outside-work-tree"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "outside-index"))
    root = make_repo(tmp_path, "checkout")
    assert (root / ".git" / "HEAD").is_file()
    assert not outside.exists()
    assert not (tmp_path / "outside-work-tree").exists()
    assert not (tmp_path / "outside-index").exists()


def test_standalone_cli_writes_attributable_report_without_git_env_redirect(tmp_path):
    report = tmp_path / "report.json"
    outside = tmp_path / "not-a-repository"
    env = {**os.environ, "GIT_DIR": str(outside)}
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools/gitlink_safety_demo.py"), "--json", str(report)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] == data["total"] == len(SCENARIOS)
    assert len(data["revision"]) == 40
    assert isinstance(data["working_tree_dirty"], bool)
    assert {row["scenario"] for row in data["results"]} == set(SCENARIOS)
    assert not outside.exists()
