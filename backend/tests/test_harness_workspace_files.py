"""Workspace Files UI: confined browsing, bounded previews, and grant checks."""
from __future__ import annotations

import asyncio

import pytest

from backend.api import intents_harness
from backend.domain.graph import SceneDocument
from backend.harness import workspace as workspace_module
from backend.harness.workspace import WorkspaceError, workspace_dir
from backend.harness.workspace_files import list_workspace_files, read_workspace_file
from backend.notifications import NotificationState


@pytest.fixture(autouse=True)
def isolated_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "scratch")


class FakeSettings:
    def __init__(self, trusted=()):
        self.trusted = list(trusted)

    def get_harness_trusted_dirs(self):
        return self.trusted


class FakeBus:
    def __init__(self):
        self.handlers = {}
        self.published = []

    def register_intent(self, topic, name, handler):
        self.handlers[(topic, name)] = handler

    async def publish(self, topic):
        self.published.append(topic)

    def call(self, name, *args):
        return asyncio.run(self.handlers[("harness", name)](*args))


def harness_with_folder(folder="", trusted=()):
    document, bus = SceneDocument(), FakeBus()
    node = document.add_harness_node(0, 0, "Read files")
    node.state.harness_workspace_path = str(folder)
    dispatcher = type("Dispatcher", (), {"_settings_manager": FakeSettings(trusted)})()
    intents_harness.register_harness_intents(bus, document, NotificationState(), dispatcher)
    return bus, node


def test_browse_nested_directories_and_filter_metadata(tmp_path):
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret")
    (root / "b.txt").write_text("B")
    (root / "a.txt").write_text("A")
    (root / "docs" / "readme.md").write_text("# Hello")
    listing = list_workspace_files(root)
    assert [(item["name"], item["isDir"]) for item in listing["entries"]] == [
        ("docs", True), ("a.txt", False), ("b.txt", False),
    ]
    assert listing["path"] == ""
    assert [item["path"] for item in list_workspace_files(root, "docs")["entries"]] == ["docs/readme.md"]
    assert read_workspace_file(root, "docs/readme.md")["content"] == "# Hello"
    with pytest.raises(WorkspaceError, match="metadata"):
        read_workspace_file(root, ".git/config")


def test_traversal_and_outside_symlinks_are_never_listed_or_read(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not in workspace")
    with pytest.raises(WorkspaceError, match="outside"):
        list_workspace_files(root, "../outside")
    with pytest.raises(WorkspaceError, match="outside"):
        read_workspace_file(root, "../outside/secret.txt")
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating directory symlinks is unavailable")
    assert list_workspace_files(root)["entries"] == []
    with pytest.raises(WorkspaceError, match="outside"):
        read_workspace_file(root, "escape/secret.txt")


def test_previews_reject_binary_and_oversized_files_and_truncate_text(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("a" * 40_100, encoding="utf-8")
    result = read_workspace_file(root, "notes.txt")
    assert result["truncated"] is True
    assert len(result["content"]) == 40_000

    (root / "photo.png").write_bytes(b"\x89PNG\x00image")
    with pytest.raises(WorkspaceError, match="Binary"):
        read_workspace_file(root, "photo.png")

    (root / "huge.txt").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(WorkspaceError, match="limit"):
        read_workspace_file(root, "huge.txt")
    with pytest.raises(WorkspaceError, match="Invalid"):
        list_workspace_files(root, "x" * 4001)


def test_many_files_are_capped_before_sorting(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for i in range(305):
        (root / f"{i:04}.txt").touch()
    listing = list_workspace_files(root)
    assert listing["truncated"] is True
    assert len(listing["entries"]) == 300


def test_intents_only_expose_currently_trusted_workspace(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "private.txt").write_text("trusted")
    bus, node = harness_with_folder(project, trusted=[str(project.resolve())])
    listing = bus.call("listWorkspaceFiles", node.id, "")
    assert listing["root"] == str(project.resolve())
    assert listing["isScratch"] is False
    assert [entry["name"] for entry in listing["entries"]] == ["private.txt"]
    assert bus.call("readWorkspaceFile", node.id, "private.txt")["content"] == "trusted"

    # A session can claim this was the last run root; that is NOT permission.
    node.state.harness_workspace_active = str(project)
    # Revoking trust switches all three UI actions to the agent's scratch dir.
    # The returned bus handler closes over the dispatcher; use a new one
    # with the same node binding and no grant for the attempted read/reveal.
    bus2, node2 = harness_with_folder(project, trusted=[])
    node2.state.harness_workspace_active = str(project)
    scratch_listing = bus2.call("listWorkspaceFiles", node2.id)
    assert scratch_listing["isScratch"] is True
    assert scratch_listing["root"] == str(workspace_dir(node2.state.harness_workspace_id))
    assert scratch_listing["entries"] == []
    with pytest.raises(WorkspaceError, match="Not a workspace file"):
        bus2.call("readWorkspaceFile", node2.id, "private.txt")

    revealed = []
    monkeypatch.setattr(intents_harness, "reveal_workspace", lambda root: revealed.append(root))
    bus2.call("revealWorkspace", node2.id)
    assert revealed == [workspace_dir(node2.state.harness_workspace_id)]
    with pytest.raises(WorkspaceError, match="no longer exists"):
        bus2.call("listWorkspaceFiles", "deleted-node")


def test_reveal_never_accepts_a_client_supplied_path(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    bus, node = harness_with_folder(project, trusted=[str(project.resolve())])
    revealed = []
    monkeypatch.setattr(intents_harness, "reveal_workspace", lambda root: revealed.append(root))
    bus.call("revealWorkspace", node.id)
    assert revealed == [project.resolve()]
