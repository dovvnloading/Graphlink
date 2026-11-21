"""Workspace Files UI: confined browsing, bounded previews, and grant checks."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.api import intents_harness, intents_harness_workspace
from backend.domain.graph import SceneDocument
from backend.harness import workspace as workspace_module
from backend.harness import workspace_files as files_module
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
    settings = FakeSettings(trusted)
    notifications = NotificationState()
    dispatcher = type("Dispatcher", (), {"_settings_manager": settings})()
    intents_harness.register_harness_intents(bus, document, notifications, dispatcher)
    bus.settings = settings
    bus.notifications = notifications
    return bus, node



def test_inflight_listing_does_not_disclose_names_after_revocation(tmp_path, monkeypatch):
    """#865: an async worker result cannot outlive its folder grant."""
    import threading

    project = tmp_path / "project"
    project.mkdir()
    (project / "private-name.md").write_text("secret")
    bus, node = harness_with_folder(project, trusted=[str(project.resolve())])
    entered, release = threading.Event(), threading.Event()
    original = intents_harness_workspace.list_workspace_files

    def paused_listing(*args):
        listing = original(*args)
        entered.set()
        assert release.wait(5), "listing worker never released"
        return listing

    monkeypatch.setattr(intents_harness_workspace, "list_workspace_files", paused_listing)

    async def scenario():
        handler = bus.handlers[("harness", "listWorkspaceFiles")]
        pending = asyncio.create_task(handler(node.id, "", None))
        assert await asyncio.to_thread(entered.wait, 5), "listing never reached the worker"
        bus.settings.trusted.clear()
        release.set()
        with pytest.raises(WorkspaceError, match="Workspace folder changed"):
            await pending
        # New requests may use scratch, but never expose the revoked names.
        fallback = await handler(node.id, "", None)
        assert fallback["isScratch"] is True
        assert all(entry["name"] != "private-name.md" for entry in fallback["entries"])

    try:
        asyncio.run(scenario())
    finally:
        release.set()




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


def test_many_files_have_deterministic_pages_without_hiding_flat_directory_entries(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    # Reverse creation order must not change the sorted first page.
    for i in reversed(range(305)):
        (root / f"{i:04}.txt").touch()
    first = list_workspace_files(root)
    assert first["truncated"] is True
    assert len(first["entries"]) == 300
    assert [entry["name"] for entry in first["entries"]] == [
        f"{i:04}.txt" for i in range(300)
    ]
    second = list_workspace_files(root, cursor=first["nextCursor"])
    assert [entry["name"] for entry in second["entries"]] == [
        f"{i:04}.txt" for i in range(300, 305)
    ]
    assert second["truncated"] is False
    assert second["nextCursor"] is None
    with pytest.raises(WorkspaceError, match="page cursor"):
        list_workspace_files(root, cursor="not a valid cursor")


def test_long_unicode_filename_cursor_is_accepted_and_still_bounded():
    # Linux limits filename *bytes*, so exercise the cross-platform cursor
    # codec directly rather than trying to create a 765-byte filename here.
    name = "中" * 255
    cursor = files_module._next_cursor({"isDir": False, "name": name})
    assert 1024 < len(cursor) <= files_module._MAX_CURSOR_CHARS
    assert files_module._page_key(cursor) == (True, name.casefold(), name)
    with pytest.raises(WorkspaceError, match="page cursor"):
        files_module._page_key("x" * (files_module._MAX_CURSOR_CHARS + 1))


def test_pagination_reuses_snapshot_and_only_resolves_page_candidates(tmp_path, monkeypatch):
    root = tmp_path / "many"
    root.mkdir()
    for i in reversed(range(905)):
        (root / f"{i:05}.txt").touch()

    # Snapshot reuse is keyed by both directory metadata and a 30-second
    # monotonic-clock window. Hold them stable so Windows timestamp updates
    # and CI delays cannot turn this into a snapshot-expiry test.
    frozen_stat = root.stat()
    original_stat = type(root).stat

    def stable_directory_stat(path, *args, **kwargs):
        return frozen_stat if path == root else original_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "stat", stable_directory_stat)
    monkeypatch.setattr(files_module, "time", SimpleNamespace(monotonic=lambda: 0.0))

    original_scan = files_module.os.scandir
    original_inside = files_module._inside
    counts = {"scan": 0, "resolve": 0}

    def count_scan(path):
        if path == root.resolve():
            counts["scan"] += 1
        return original_scan(path)

    def count_inside(*args):
        counts["resolve"] += 1
        return original_inside(*args)

    monkeypatch.setattr(files_module.os, "scandir", count_scan)
    monkeypatch.setattr(files_module, "_inside", count_inside)
    first = files_module.list_workspace_files(root)
    second = files_module.list_workspace_files(root, cursor=first["nextCursor"])
    third = files_module.list_workspace_files(root, cursor=second["nextCursor"])

    assert [page["entries"][0]["name"] for page in (first, second, third)] == [
        "00000.txt", "00300.txt", "00600.txt",
    ]
    assert counts["scan"] == 1  # No full directory traversal per page.
    assert counts["resolve"] < 920  # Not 905 path resolves * three pages.


def test_directory_snapshot_is_invalidated_by_content_changes(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    # Simulate Windows returning unchanged directory metadata after a create.
    original_stat = type(root).stat
    frozen_stat = root.stat()

    def stale_directory_stat(path, *args, **kwargs):
        return frozen_stat if path == root else original_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "stat", stale_directory_stat)
    (root / "b.txt").touch()
    assert [e["name"] for e in list_workspace_files(root)["entries"]] == ["b.txt"]
    (root / "a.txt").touch()
    assert [e["name"] for e in list_workspace_files(root)["entries"]] == [
        "a.txt", "b.txt",
    ]


def test_folder_listing_intent_accepts_next_page_cursor(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for i in range(302):
        (root / f"file_{i:03}.txt").touch()
    bus, node = harness_with_folder(root, trusted=[str(root.resolve())])
    first = bus.call("listWorkspaceFiles", node.id, "")
    second = bus.call("listWorkspaceFiles", node.id, "", first["nextCursor"])
    assert len(first["entries"]) == 300
    assert [entry["name"] for entry in second["entries"]] == [
        "file_300.txt", "file_301.txt",
    ]


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
    monkeypatch.setattr(intents_harness_workspace, "reveal_workspace", lambda root: revealed.append(root))
    bus2.call("revealWorkspace", node2.id)
    assert revealed == [workspace_dir(node2.state.harness_workspace_id)]
    with pytest.raises(WorkspaceError, match="no longer exists"):
        bus2.call("listWorkspaceFiles", "deleted-node")


def test_reveal_never_accepts_a_client_supplied_path(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    bus, node = harness_with_folder(project, trusted=[str(project.resolve())])
    revealed = []
    monkeypatch.setattr(intents_harness_workspace, "reveal_workspace", lambda root: revealed.append(root))
    bus.call("revealWorkspace", node.id)
    assert revealed == [project.resolve()]


def test_revocation_rejects_a_file_from_an_outdated_listing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.md").write_text("project version")
    bus, node = harness_with_folder(project, trusted=[str(project.resolve())])
    scratch = workspace_dir(node.state.harness_workspace_id)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "notes.md").write_text("scratch version")

    listing = bus.call("listWorkspaceFiles", node.id)
    assert listing["root"] == str(project.resolve())
    bus.settings.trusted.clear()
    assert bus.call("listWorkspaceFiles", node.id)["isScratch"] is True
    with pytest.raises(WorkspaceError, match="Workspace folder changed"):
        bus.call("readWorkspaceFile", node.id, "notes.md", listing["root"])
    # An unchanged, still-trusted listing can be read normally.
    bus.settings.trusted.append(str(project.resolve()))
    assert bus.call("readWorkspaceFile", node.id, "notes.md", listing["root"])["content"] == "project version"


def test_reveal_reports_a_nonzero_native_opener_exit(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(files_module.sys, "platform", "linux")
    commands = []

    class FailedOpener:
        def wait(self, timeout):
            assert timeout > 0
            return 3

    def fake_popen(command, **kwargs):
        commands.append((command, kwargs))
        return FailedOpener()

    monkeypatch.setattr(files_module.subprocess, "Popen", fake_popen)
    with pytest.raises(WorkspaceError, match="status 3"):
        files_module.reveal_workspace(project)

    bus, node = harness_with_folder(project, trusted=[str(project.resolve())])
    bus.call("revealWorkspace", node.id)
    assert commands[0][0] == ["xdg-open", str(project.resolve())]
    assert bus.notifications.msg_type == "error"
    assert "status 3" in bus.notifications.message
    assert "notification" in bus.published
