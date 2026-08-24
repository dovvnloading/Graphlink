"""User-directory workspaces: the trust gate is the whole security story,
so the tests concentrate there - a trusted dir binds, an untrusted request
degrades to scratch, confinement holds against the user dir, and the
transcript never lands inside it."""

from __future__ import annotations

import asyncio

import pytest

from backend.harness import workspace as workspace_module
from backend.harness.workspace import bound_root, ensure_workspace, resolve_under_root
from backend.harness.tools_fs import register_harness_fs_tools
from backend.providers.base import ToolCall
from backend.tools import FS_READ, FS_WRITE, RunContext, ToolRegistry


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "scratch")
    return tmp_path


class FakeSettings:
    def __init__(self, trusted):
        self._trusted = list(trusted)

    def get_harness_trusted_dirs(self):
        return list(self._trusted)


def test_a_trusted_existing_dir_binds_as_a_user_dir(workspace_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    settings = FakeSettings([str(project.resolve())])
    root, is_user_dir = bound_root("ws1", str(project), settings_manager=settings)
    assert is_user_dir and root == project.resolve()


def test_an_untrusted_dir_silently_falls_back_to_scratch(workspace_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    # The path is a real dir but NOT in the trust list: a session file's
    # request must never be honored on trust it does not carry.
    root, is_user_dir = bound_root("ws1", str(project), settings_manager=FakeSettings([]))
    assert not is_user_dir
    assert root == ensure_workspace("ws1")


def test_a_trusted_but_now_missing_dir_falls_back_to_scratch(workspace_root, tmp_path):
    gone = tmp_path / "gone"
    settings = FakeSettings([str((tmp_path / "gone").resolve())])
    root, is_user_dir = bound_root("ws1", str(gone), settings_manager=settings)
    assert not is_user_dir and root == ensure_workspace("ws1")


def test_no_path_or_no_settings_is_scratch(workspace_root):
    assert bound_root("ws1", "", settings_manager=FakeSettings(["/anything"]))[1] is False
    assert bound_root("ws1", "/some/dir", settings_manager=None)[1] is False


def test_confinement_and_write_hold_against_a_user_dir(workspace_root, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "readme.txt").write_text("hello from the project", encoding="utf-8")
    registry = ToolRegistry()
    register_harness_fs_tools(registry)

    async def approve(_call):
        return True

    # A run bound to the user dir carries it as the resolved root.
    ctx = RunContext(granted_scopes=frozenset({FS_READ, FS_WRITE}), request_approval=approve)
    ctx.harness_workspace_dir = project.resolve()

    async def go():
        read = await registry.invoke(ToolCall(id="1", name="fs.read", arguments={"path": "readme.txt"}), ctx)
        wrote = await registry.invoke(
            ToolCall(id="2", name="fs.write", arguments={"path": "out.txt", "content": "written"}), ctx,
        )
        escape = await registry.invoke(
            ToolCall(id="3", name="fs.read", arguments={"path": "../secret.txt"}), ctx,
        )
        return read, wrote, escape

    read, wrote, escape = asyncio.run(go())
    assert not read.is_error and "hello from the project" in read.content
    assert not wrote.is_error and (project / "out.txt").read_text(encoding="utf-8") == "written"
    assert escape.is_error and "outside" in escape.content


def test_scratch_resolve_helper_still_confines(workspace_root):
    ensure_workspace("ws1")
    with pytest.raises(workspace_module.WorkspaceError):
        resolve_under_root(workspace_module.workspace_dir("ws1"), "../elsewhere")
