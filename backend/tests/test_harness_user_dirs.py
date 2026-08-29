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


# -- the launcher's own workspace choice ------------------------------------
#
# Binding a folder only AFTER the node existed meant the first run of every
# real piece of work went to scratch: you had to let it finish, rebind, and
# re-send the same task. These cover the launch-time path that removes that,
# and the fact that it buys no trust the per-node path did not already.


class _FakeBus:
    """Enough SessionBus for the two intents under test."""

    def __init__(self):
        self.intents = {}
        self.published = []

    def register_intent(self, topic, name, fn):
        self.intents[(topic, name)] = fn

    async def publish(self, topic):
        self.published.append(topic)

    async def dispatch(self, topic, name, args):
        return await self.intents[(topic, name)](*args)


class _RecordingSettings(FakeSettings):
    def __init__(self, trusted=()):
        super().__init__(trusted)
        self.granted = []

    def add_harness_trusted_dir(self, path):
        self.granted.append(path)
        self._trusted.append(path)


def _harness_bus(monkeypatch, settings, picked=None):
    from backend import native_dialogs
    from backend.api.intents_harness import register_harness_intents
    from backend.domain.graph import SceneDocument
    from backend.notifications import NotificationState

    async def fake_pick_folder(directory=None):
        return picked

    monkeypatch.setattr(native_dialogs, "pick_folder", fake_pick_folder)
    bus, document = _FakeBus(), SceneDocument()
    dispatcher = type("D", (), {"_settings_manager": settings, "start_harness_run": None})()

    async def start_harness_run(**kwargs):
        dispatcher.started = kwargs
        return "run-1"

    dispatcher.start_harness_run = start_harness_run
    register_harness_intents(bus, document, NotificationState(), dispatcher)
    return bus, document


def test_the_launcher_binds_its_folder_before_the_first_run(workspace_root, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    settings = _RecordingSettings([str(project.resolve())])
    bus, document = _harness_bus(monkeypatch, settings)

    node_id = asyncio.run(bus.dispatch("harness", "start", ["do the thing", 8, str(project)]))

    node = document.nodes[node_id]
    assert node.state.harness_workspace_path == str(project)
    # Bound BEFORE the run: the first task already works in the right place.
    root, is_user_dir = bound_root(
        node.state.harness_workspace_id, node.state.harness_workspace_path,
        settings_manager=settings,
    )
    assert is_user_dir and root == project.resolve()


def test_a_launcher_path_is_a_request_not_a_grant(workspace_root, tmp_path, monkeypatch):
    """The wire can name any folder; naming it must not make it trusted.
    The run-time gate is what settles it, exactly as for a session file."""
    project = tmp_path / "not-granted"
    project.mkdir()
    bus, document = _harness_bus(monkeypatch, _RecordingSettings([]))

    node_id = asyncio.run(bus.dispatch("harness", "start", ["do the thing", 8, str(project)]))

    node = document.nodes[node_id]
    root, is_user_dir = bound_root(
        node.state.harness_workspace_id, node.state.harness_workspace_path,
        settings_manager=_RecordingSettings([]),
    )
    assert not is_user_dir and root == ensure_workspace(node.state.harness_workspace_id)


def test_picking_a_launch_workspace_grants_it_and_answers_with_the_path(
    workspace_root, tmp_path, monkeypatch,
):
    project = tmp_path / "project"
    project.mkdir()
    settings = _RecordingSettings([])
    bus, _document = _harness_bus(monkeypatch, settings, picked=str(project))

    answer = asyncio.run(bus.dispatch("harness", "pickLaunchWorkspace", []))

    assert answer == str(project.resolve())
    assert settings.granted == [str(project.resolve())], "the pick IS the grant"


def test_a_cancelled_launch_picker_grants_nothing(workspace_root, monkeypatch):
    settings = _RecordingSettings([])
    bus, _document = _harness_bus(monkeypatch, settings, picked=None)

    assert asyncio.run(bus.dispatch("harness", "pickLaunchWorkspace", [])) is None
    assert settings.granted == []
