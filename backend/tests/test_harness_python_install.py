"""Approval, index restrictions, and actual offline cache reuse for python.install."""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.harness import python_install as install
from backend.harness.tools_python_install import register_harness_python_install_tool
from backend.providers.base import ToolCall
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry
from graphlink_process_env import safe_subprocess_env


def _registry():
    registry = ToolRegistry()
    register_harness_python_install_tool(registry)
    return registry


def _ctx(root, approve, *, workspace_id="test-agent", session_grants=None):
    ctx = RunContext(
        granted_scopes=frozenset({CODE_EXECUTE}),
        request_approval=approve,
        session_grants=session_grants,
    )
    ctx.harness_workspace_id = workspace_id
    ctx.harness_workspace_dir = root
    ctx.harness_workspace_is_user_dir = True
    return ctx


def _call(packages, cid="install"):
    return ToolCall(id=cid, name="python.install", arguments={"packages": packages})


@pytest.mark.parametrize(
    "packages",
    [
        [], "numpy", [42], ["-r other.txt"], ["--index-url=https://elsewhere"],
        ["pkg @ https://elsewhere/wheel.whl"], ["file:///tmp/other.whl"],
        ["../other"], ["git+https://elsewhere/project"],
        ["hello\n--no-binary :all:"], ["name; python_version > '3'"],
        ["foo"] * 33, ["foo" * 181],
    ],
)
def test_invalid_requirements_are_rejected_before_approval(tmp_path, packages):
    async def unexpected(_call):
        pytest.fail("invalid requirements must never request permission")

    result = asyncio.run(_registry().invoke(
        _call(packages), _ctx(tmp_path, unexpected),
    ))
    assert result.is_error
    assert not (tmp_path / ".venv").exists()


def test_unknown_arguments_cannot_hide_unapproved_pip_options(tmp_path):
    async def unexpected(_call):
        pytest.fail("unrecognized fields must be rejected before permission")

    result = asyncio.run(_registry().invoke(
        ToolCall("bad", "python.install", {
            "packages": ["example"], "index_url": "https://elsewhere"
        }),
        _ctx(tmp_path, unexpected),
    ))
    assert result.is_error and "only the packages" in result.content


def test_fresh_approval_ignores_session_grants_and_discloses_package_list(tmp_path, monkeypatch):
    # No real venv or network is necessary to exercise the consent boundary.
    monkeypatch.setattr(
        install, "ensure_python_environment",
        lambda wid, root, *, is_scratch: install.planned_install_interpreter(
            wid, root, is_scratch=is_scratch,
        ),
    )
    observed = []

    def fake_worker(interpreter, cwd, packages, cancel_event, cache_root):
        observed.append((interpreter, cwd, packages))
        from backend.tools import ToolResult
        return ToolResult("installed")

    monkeypatch.setattr(install, "_install_in_worker", fake_worker)
    seen = []

    async def approve(call):
        seen.append((call.name, list(call.arguments["packages"])))
        ctx.python_install_approved_interpreter = install.planned_install_interpreter(
            "test-agent", tmp_path, is_scratch=False,
        )
        return "session"

    grants = {"python.install"}
    ctx = _ctx(tmp_path, approve, session_grants=grants)
    req = _call(["requests>=2,<3", "idna==3.10"])
    for _ in range(2):
        result = asyncio.run(_registry().invoke(req, ctx))
        assert not result.is_error, result.content
    assert seen == [
        ("python.install", ["requests>=2,<3", "idna==3.10"]),
        ("python.install", ["requests>=2,<3", "idna==3.10"]),
    ]
    assert len(observed) == 2
    assert ctx._approved_fingerprints == set()
    assert ctx.python_install_approved_interpreter is None
    assert install.install_approval_summary(
        req.arguments["packages"], observed[0][0],
    ) == (
        f"python.install\n--- environment\n{observed[0][0]}\n"
        "--- packages\nrequests>=2,<3\nidna==3.10"
    )


def test_changed_pending_requirements_do_not_install(tmp_path, monkeypatch):
    async def approve(disclosed):
        disclosed.arguments["packages"][0] = "surprise"
        return True

    ctx = _ctx(tmp_path, approve)
    result = asyncio.run(_registry().invoke(_call(["allowed"]), ctx))
    assert result.is_error and "changed" in result.content
    assert not (tmp_path / "envs").exists()


def test_rebound_environment_cannot_use_previous_approval(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    async def approve(call):
        ctx.python_install_approved_interpreter = install.planned_install_interpreter(
            "test-agent", project, is_scratch=False,
        )
        ctx.harness_workspace_dir = other  # rebind while approval is pending
        return True

    ctx = _ctx(project, approve)
    result = asyncio.run(_registry().invoke(_call(["requests"]), ctx))
    assert result.is_error and "changed" in result.content.lower()


def test_pip_subprocess_uses_safe_env_and_never_a_shell(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://untrusted.invalid/simple")
    monkeypatch.setenv("GRAPHLINK_PROVIDER_SECRET", "do-not-inherit")

    class Process:
        stdout = io.StringIO("pip succeeded\n")
        returncode = 0

        def poll(self):
            return 0

    class Guard:
        def popen_kwargs(self):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(install, "create_execution_guard", Guard)

    def spawn(_guard, argv, **kwargs):
        seen.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(install, "spawn_guarded_subprocess", spawn)
    monkeypatch.setattr(install, "assign_guarded_subprocess", lambda g, p: None)
    code, output, ended = install._run_pip(
        [sys.executable, "-m", "pip", "--version"], tmp_path, None,
    )
    assert code == 0 and ended == "ok" and "succeeded" in output
    assert len(seen) == 1
    assert isinstance(seen[0][0], list)
    assert seen[0][1]["env"]["PIP_CONFIG_FILE"] == os.devnull
    assert "PIP_EXTRA_INDEX_URL" not in seen[0][1]["env"]
    assert "GRAPHLINK_PROVIDER_SECRET" not in seen[0][1]["env"]
    assert "shell" not in seen[0][1]


def _make_wheel(parent: Path) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    filename = parent / "graphlink_offline_fixture-1.0.0-py3-none-any.whl"
    info = "graphlink_offline_fixture-1.0.0.dist-info"
    with zipfile.ZipFile(filename, "w") as wheel:
        wheel.writestr("graphlink_offline_fixture.py", "VALUE = 173\n")
        wheel.writestr(
            f"{info}/METADATA",
            "Metadata-Version: 2.1\nName: graphlink-offline-fixture\nVersion: 1.0.0\n",
        )
        wheel.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: graphlink-test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(f"{info}/RECORD", "")
    return filename


def test_downloaded_wheel_is_reused_offline_across_environments(tmp_path, monkeypatch):
    wheel_source = _make_wheel(tmp_path / "seed")
    cache = tmp_path / "shared-cache"
    monkeypatch.setattr(install, "PYTHON_WHEEL_CACHE_ROOT", cache)
    real_run = install._run_pip
    downloads = []

    def simulate_index_once(argv, cwd, cancel_event):
        if "download" in argv:
            downloads.append(argv)
            assert len(downloads) == 1, "second environment must work offline"
            assert "--only-binary=:all:" in argv
            assert f"--index-url={install.DEFAULT_PYPI_INDEX}" in argv
            shutil.copy2(wheel_source, cache / "wheels" / wheel_source.name)
            return 0, "downloaded from default index", "ok"
        assert "install" in argv
        assert "--no-index" in argv and "--only-binary=:all:" in argv
        return real_run(argv, cwd, cancel_event)

    monkeypatch.setattr(install, "_run_pip", simulate_index_once)

    for number in (1, 2):
        project = tmp_path / f"project-{number}"
        project.mkdir()
        venv.EnvBuilder(with_pip=True).create(project / ".venv")

        async def approve(call, root=project):
            ctx.python_install_approved_interpreter = install.planned_install_interpreter(
                "test-agent", root, is_scratch=False,
            )
            return True

        ctx = _ctx(project, approve)
        result = asyncio.run(_registry().invoke(
            _call(["graphlink-offline-fixture==1.0.0"], str(number)), ctx,
        ))
        assert not result.is_error, result.content
        interpreter = install.planned_install_interpreter(
            "test-agent", project, is_scratch=False,
        )
        imported = subprocess.run(
            [str(interpreter), "-c", "import graphlink_offline_fixture; print(graphlink_offline_fixture.VALUE)"],
            env=safe_subprocess_env(), cwd=project, capture_output=True,
            text=True, timeout=20, check=True,
        )
        assert imported.stdout.strip() == "173"

    assert len(downloads) == 1
    assert (cache / "wheels" / wheel_source.name).is_file()
