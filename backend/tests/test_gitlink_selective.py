"""Selective Gitlink Apply: client picks paths, backend owns every write.

Exercises the pure selection boundary, real on-disk subset application, the
async dispatcher/fingerprint lifecycle, and the actual WebSocket intent. No
network, LLM or desktop window is required.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.agents import AgentDispatcher, _call_gitlink_apply, _fingerprint_changes
from backend.canvas import register_canvas
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.tests.conftest import gitlink_apply_slots
from graphlink_plugins.gitlink.change_review import (
    prepare_change_review,
    select_gitlink_changes,
)


def _proposal(tmp_path):
    (tmp_path / "a.py").write_text("original A", encoding="utf-8")
    (tmp_path / "b.py").write_text("original B", encoding="utf-8")
    return prepare_change_review([
        {"path": "a.py", "operation": "update", "content": "new A"},
        {"path": "b.py", "operation": "update", "content": "new B"},
        {"path": "new/c.py", "operation": "create", "content": "new C"},
    ], str(tmp_path))[0]


def test_selection_is_server_owned_and_keeps_original_proposal_order(tmp_path):
    reviewed = _proposal(tmp_path)
    selected = select_gitlink_changes(reviewed, ["new/c.py", "a.py"])

    assert [change["path"] for change in selected] == ["a.py", "new/c.py"]
    assert all(selected[i] is not reviewed[i] for i in [0, 1])
    assert selected[0]["content"] == "new A"
    assert selected[0]["expected_sha256"] == reviewed[0]["expected_sha256"]
    selected[0]["content"] = "changed only in frozen copy"
    assert reviewed[0]["content"] == "new A"


def test_legacy_omitted_selection_selects_all_without_dropping_baselines(tmp_path):
    reviewed = _proposal(tmp_path)
    selected = select_gitlink_changes(reviewed, None)
    assert selected == reviewed
    assert selected is not reviewed
    assert all(row is not original for row, original in zip(selected, reviewed))


@pytest.mark.parametrize("invalid", [
    [],
    ["unknown.py"],
    ["a.py", "a.py"],
    ["a.py", "unknown.py"],
    ["a.py", None],
    ["a.py", 3],
    "a.py",
    {"a.py": "arbitrary user-supplied content"},
    ["/etc/passwd"],
])
def test_selective_apply_rejects_empty_duplicate_unknown_or_malformed_paths(tmp_path, invalid):
    reviewed = _proposal(tmp_path)
    with pytest.raises(RuntimeError, match="Select|match|duplicate"):
        select_gitlink_changes(reviewed, invalid)
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "original A"


@pytest.mark.parametrize("bad_path", ["a/../b.py", "/etc/passwd"])
def test_selection_rejects_noncanonical_server_entries(tmp_path, bad_path):
    reviewed = _proposal(tmp_path)
    reviewed[1]["path"] = bad_path
    with pytest.raises(RuntimeError, match="inside|noncanonical"):
        select_gitlink_changes(reviewed, ["a.py"])


def test_selection_rejects_casefold_duplicate_server_paths(tmp_path):
    reviewed = _proposal(tmp_path)
    reviewed[1]["path"] = "A.py"
    with pytest.raises(RuntimeError, match="duplicate"):
        select_gitlink_changes(reviewed, ["a.py"])


def test_selective_apply_writes_only_requested_file_and_ignores_unselected_conflict(tmp_path):
    reviewed = _proposal(tmp_path)
    (tmp_path / "b.py").write_text("user edited B after preview", encoding="utf-8")

    selected = select_gitlink_changes(reviewed, ["a.py"])
    assert _call_gitlink_apply(tmp_path, selected) == 1
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new A"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "user edited B after preview"
    assert not (tmp_path / "new/c.py").exists()


def test_selected_conflict_aborts_without_touching_unselected_file(tmp_path):
    reviewed = _proposal(tmp_path)
    (tmp_path / "a.py").write_text("changed after preview", encoding="utf-8")
    selected = select_gitlink_changes(reviewed, ["a.py", "new/c.py"])

    with pytest.raises(RuntimeError, match="changed since the preview"):
        _call_gitlink_apply(tmp_path, selected)
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "changed after preview"
    assert not (tmp_path / "new/c.py").exists()


def _dispatch_env(reviewed, local_root):
    bus = SessionBus("gitlink-selective-apply")
    notifications = NotificationState()
    bus.register_topic("scene", lambda: {})
    bus.register_topic("notification", notifications.payload)

    class _Settings:
        def get_enable_system_prompt(self):
            return True

    dispatcher = AgentDispatcher(_Settings())  # type: ignore[arg-type]
    state = SimpleNamespace(
        gitlink_pending_changes=reviewed,
        gitlink_change_fingerprint=_fingerprint_changes(reviewed),
        gitlink_change_local_root=local_root,
        gitlink_change_state="previewed",
        gitlink_error="",
    )
    node = SimpleNamespace(state=state, pending_request_id=None)
    return bus, notifications, dispatcher, node


def test_dispatcher_freezes_selected_subset_after_full_fingerprint_check(tmp_path):
    reviewed = _proposal(tmp_path)

    async def run():
        bus, notifications, dispatcher, node = _dispatch_env(reviewed, str(tmp_path))
        successful, failures = [], []
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=_fingerprint_changes(reviewed), local_root=str(tmp_path),
            selected_paths=["new/c.py", "a.py"],
            on_success=successful.append, on_failure=failures.append,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert successful == [2]
        assert failures == []
        assert node.pending_request_id is None
        assert gitlink_apply_slots(dispatcher) == {}
        assert "Applied 2 file changes" in notifications.message

    asyncio.run(run())
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new A"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "original B"
    assert (tmp_path / "new/c.py").read_text(encoding="utf-8") == "new C"


def test_subset_fingerprint_cannot_replace_original_whole_proposal_fingerprint(tmp_path):
    reviewed = _proposal(tmp_path)
    wrong_fingerprint = _fingerprint_changes(select_gitlink_changes(reviewed, ["a.py"]))

    async def run():
        bus, notifications, dispatcher, node = _dispatch_env(reviewed, str(tmp_path))
        failures = []
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=wrong_fingerprint, local_root=str(tmp_path),
            selected_paths=["a.py"],
            on_success=lambda _: pytest.fail("unapproved subset written"),
            on_failure=failures.append,
        )
        assert len(failures) == 1
        assert "changed after approval" in failures[0]
        assert node.pending_request_id is None
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "original A"


def test_invalid_selection_releases_busy_claim_and_does_not_write(tmp_path):
    reviewed = _proposal(tmp_path)

    async def run():
        bus, notifications, dispatcher, node = _dispatch_env(reviewed, str(tmp_path))
        failures = []
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=_fingerprint_changes(reviewed), local_root=str(tmp_path),
            selected_paths=["unapproved.py"],
            on_success=lambda _: pytest.fail("unknown file written"),
            on_failure=failures.append,
        )
        assert len(failures) == 1
        assert "no longer match" in failures[0]
        assert node.pending_request_id is None
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "original A"


def test_real_intent_forwards_only_fingerprint_and_selected_paths_and_consumes_all():
    class _CapturingDispatcher:
        def __init__(self):
            self.calls = []

        async def start_gitlink_apply(self, **kwargs):
            self.calls.append(kwargs)

    async def run():
        bus = SessionBus("gitlink-selective-wire")
        notification = NotificationState()
        bus.register_topic("notification", notification.payload)
        composer = ComposerDocument()
        bus.register_topic("app-composer", composer.payload)
        dispatcher = _CapturingDispatcher()
        document = register_canvas(bus, notification, dispatcher, composer)
        node = document.add_gitlink_node(0, 0, None)
        document.set_gitlink_local_root(node.id, "C:/checkout")
        node.state.gitlink_pending_changes = [
            {"path": "a.py", "operation": "update", "content": "A"},
            {"path": "b.py", "operation": "update", "content": "B"},
        ]
        node.state.gitlink_change_fingerprint = "whole-proposal-fp"

        result = await bus.dispatch_intent(
            "scene", "applyGitlinkChanges", [node.id, "whole-proposal-fp", ["b.py"]]
        )
        assert result == node.id
        assert len(dispatcher.calls) == 1
        call = dispatcher.calls[0]
        assert call["client_fingerprint"] == "whole-proposal-fp"
        assert call["selected_paths"] == ["b.py"]
        assert call["local_root"] == "C:/checkout"
        assert "pending_changes" not in call
        assert "files" not in call

        # Successful subset Apply consumes the ENTIRE proposal. An unchecked
        # row cannot be replayed using the original fingerprint.
        call["on_success"](1)
        assert node.state.gitlink_pending_changes == []
        assert node.state.gitlink_change_fingerprint is None
        assert node.state.gitlink_change_state == "applied"

    asyncio.run(run())
