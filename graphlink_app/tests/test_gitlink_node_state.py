"""Phase 7 prerequisite increment 6: GitlinkNode's source-of-truth inversion
(repo/branch/local_root/scope_mode become consistently authoritative on read,
not just on write; task_prompt's already-correct mirror gets a getter that
actually uses it) plus regression coverage for the write-approval flow
(the fingerprint gate, restore_saved_state's "approval never survives a
reload" invariant, and apply_approved_changes itself) - none of which had any
test coverage before this increment (confirmed via repo-wide grep).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QMessageBox

_APP = QApplication.instance() or QApplication([])

from graphlink_plugins.graphlink_plugin_gitlink import (
    GITLINK_STATE_APPLIED,
    GITLINK_STATE_APPROVED,
    GITLINK_STATE_DRAFT,
    GITLINK_STATE_PREVIEWED,
    GitlinkNode,
)
from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.serializers import SceneSerializer


def _make_window_and_scene():
    window = MagicMock()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    return window, scene


def _desynced_widget_value(widget, mirror_getter):
    """Sets the widget's text WITHOUT firing textChanged (blockSignals), then
    confirms the getter still returns the pre-existing mirror value - proof a
    getter reads the mirror and not the live widget."""
    before = mirror_getter()
    widget.blockSignals(True)
    widget.setPlainText("SHOULD NOT BE VISIBLE THROUGH THE GETTER")
    widget.blockSignals(False)
    after = mirror_getter()
    return before, after


class TestTaskPromptMirrorAuthority:
    def test_get_task_prompt_reads_the_mirror_not_the_live_widget(self):
        node = GitlinkNode(parent_node=None)
        node.seed_prompt("original prompt")

        before, after = _desynced_widget_value(node.task_input, node.get_task_prompt)

        assert before == after == "original prompt"

    def test_seed_prompt_keeps_widget_and_mirror_in_sync(self):
        node = GitlinkNode(parent_node=None)
        node.seed_prompt("seeded")
        assert node.get_task_prompt() == "seeded"
        assert node.task_input.toPlainText() == "seeded"


class TestRepoStateReadAuthority:
    def test_resolve_repo_and_branch_reads_repo_state_not_the_widget(self):
        node = GitlinkNode(parent_node=None)
        # Deliberately desync: repo_state has real values, the widgets are
        # still blank (never touched) - if _resolve_repo_and_branch fell back
        # to reading the widgets, this would raise "Enter a repository..."
        node.repo_state["repo"] = "owner/repo"
        node.repo_state["branch"] = "main"
        node._github_request = MagicMock(return_value={"default_branch": "main"})

        repo_name, branch_name = node._resolve_repo_and_branch()

        assert repo_name == "owner/repo"
        assert branch_name == "main"

    def test_ensure_repository_snapshot_reads_local_root_from_repo_state(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node._resolve_repo_and_branch = MagicMock(return_value=("owner/repo", "main"))
        node.repo_state["local_root"] = str(tmp_path)

        result = node._ensure_repository_snapshot()

        assert result == tmp_path

    def test_apply_approved_changes_reads_local_root_from_repo_state(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [{"path": "a.py", "operation": "create", "content": "print(1)"}]
        node.repo_state["local_root"] = str(tmp_path)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            node.apply_approved_changes()

        assert (tmp_path / "a.py").read_text() == "print(1)"
        assert node.change_state == GITLINK_STATE_APPLIED

    def test_build_context_bundle_reads_scope_mode_from_repo_state(self, tmp_path):
        (tmp_path / "a.py").write_text("print(1)")
        node = GitlinkNode(parent_node=None)
        node._resolve_repo_and_branch = MagicMock(return_value=("owner/repo", "main"))
        node.repo_state["local_root"] = str(tmp_path)
        node.repo_state["scope_mode"] = "full"
        node.repo_file_paths = ["a.py"]

        context_xml = node.build_context_bundle()

        assert "a.py" in context_xml
        assert node.context_stats["scanned_files"] == 1


class TestSelectedPathsMirrorAuthority:
    def test_build_context_bundle_selected_mode_uses_the_mirror_not_the_widget(self, tmp_path):
        # file_list is deliberately left empty (no QListWidgetItems at all) -
        # if build_context_bundle's scope resolution called get_selected_paths()
        # (which reads file_list.selectedItems() live) instead of trusting
        # self.selected_paths, this would raise "Select one or more files...".
        (tmp_path / "fake.py").write_text("print(1)")
        node = GitlinkNode(parent_node=None)
        node._resolve_repo_and_branch = MagicMock(return_value=("owner/repo", "main"))
        node.repo_state["local_root"] = str(tmp_path)
        node.repo_state["scope_mode"] = "selected"
        node.selected_paths = ["fake.py"]

        context_xml = node.build_context_bundle()

        assert "fake.py" in context_xml


class TestSerializerRoundTrip:
    def test_full_round_trip_preserves_repo_state_and_task_prompt(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = GitlinkNode(parent, settings_manager=None)
        node.seed_prompt("fix the bug")
        node.repo_state["repo"] = "owner/repo"
        node.repo_state["branch"] = "main"
        node.repo_state["scope_mode"] = "full"
        node.repo_state["local_root"] = "/tmp/somewhere"
        # selected_paths can only be restored as a subset of repo_file_paths -
        # _populate_file_list (called by restore_saved_state) only creates
        # QListWidgetItems for paths in repo_file_paths, so a selected path
        # outside that list has no item to mark selected on restore.
        node.repo_file_paths = ["a.py", "b.py", "c.py"]
        node.selected_paths = ["a.py", "b.py"]
        node.context_xml = "<gitlink_context></gitlink_context>"
        scene.addItem(node)
        scene.gitlink_nodes.append(node)

        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        node_payload = SceneSerializer(window).serialize_node(node, [parent, node])

        assert node_payload["task_prompt"] == "fix the bug"
        assert node_payload["repo_state"]["repo"] == "owner/repo"
        assert node_payload["repo_state"]["scope_mode"] == "full"
        assert node_payload["selected_paths"] == ["a.py", "b.py"]

        target_window, target_scene = _make_window_and_scene()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        restored_node = deserializer.deserialize_node(1, node_payload, {0: restored_parent})

        assert restored_node.get_task_prompt() == "fix the bug"
        assert restored_node.repo_state["repo"] == "owner/repo"
        assert restored_node.selected_paths == ["a.py", "b.py"]

    def test_gitlink_requested_is_wired_on_restore(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = GitlinkNode(parent, settings_manager=None)
        scene.addItem(node)
        scene.gitlink_nodes.append(node)
        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        node_payload = SceneSerializer(window).serialize_node(node, [parent, node])

        target_window, target_scene = _make_window_and_scene()
        target_window.execute_gitlink_node = MagicMock()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        restored_node = deserializer.deserialize_node(1, node_payload, {0: restored_parent})

        restored_node.gitlink_requested.emit(restored_node)
        target_window.execute_gitlink_node.assert_called_once_with(restored_node)


class TestApprovalNeverSurvivesReload:
    def test_restoring_a_proposal_never_exceeds_previewed(self):
        node = GitlinkNode(parent_node=None)

        node.restore_saved_state(
            proposal_data={"files": [{"path": "a.py", "operation": "update", "content": "x"}]},
        )

        assert node.change_state in (GITLINK_STATE_DRAFT, GITLINK_STATE_PREVIEWED)
        assert node.change_state != GITLINK_STATE_APPROVED
        assert node.change_state != GITLINK_STATE_APPLIED
        assert node._approved_fingerprint is None

    def test_restoring_after_a_real_applied_state_still_resets_to_at_most_previewed(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [{"path": "a.py", "operation": "create", "content": "x"}]
        node.repo_state["local_root"] = str(tmp_path)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            node.apply_approved_changes()
        assert node.change_state == GITLINK_STATE_APPLIED

        node.restore_saved_state(
            proposal_data={"files": [{"path": "b.py", "operation": "update", "content": "y"}]},
        )

        assert node.change_state in (GITLINK_STATE_DRAFT, GITLINK_STATE_PREVIEWED)
        assert node._approved_fingerprint is None


class TestApplyApprovedChangesOrchestration:
    def test_happy_path_approve_writes_files_and_reaches_applied(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [{"path": "a.py", "operation": "create", "content": "print(1)"}]
        node.repo_state["local_root"] = str(tmp_path)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            node.apply_approved_changes()

        assert (tmp_path / "a.py").read_text() == "print(1)"
        assert node.change_state == GITLINK_STATE_APPLIED

    def test_user_cancelling_leaves_everything_unchanged(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [{"path": "a.py", "operation": "create", "content": "print(1)"}]
        node.repo_state["local_root"] = str(tmp_path)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            node.apply_approved_changes()

        assert not (tmp_path / "a.py").exists()
        assert node.change_state != GITLINK_STATE_APPLIED

    def test_simulated_race_mutates_pending_changes_during_the_dialog_and_is_caught(self, tmp_path):
        # Reproduces the exact scenario the fingerprint gate exists for: a
        # background worker's finished signal calls set_proposal() (which
        # unconditionally overwrites pending_changes) while the confirmation
        # dialog is open. Here the monkeypatched QMessageBox.question mutates
        # pending_changes itself as a stand-in for that race, then returns Yes.
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [{"path": "a.py", "operation": "create", "content": "print(1)"}]
        node.repo_state["local_root"] = str(tmp_path)

        def _mutate_then_approve(*args, **kwargs):
            node.pending_changes = [{"path": "b.py", "operation": "create", "content": "print(2)"}]
            return QMessageBox.StandardButton.Yes

        with patch.object(QMessageBox, "question", side_effect=_mutate_then_approve):
            node.apply_approved_changes()

        assert not (tmp_path / "a.py").exists()
        assert not (tmp_path / "b.py").exists()
        assert node.change_state == GITLINK_STATE_PREVIEWED
        assert node._approved_fingerprint is None

    def test_missing_content_key_aborts_with_no_partial_writes(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [
            {"path": "a.py", "operation": "create", "content": "print(1)"},
            {"path": "b.py", "operation": "update"},  # missing 'content'
        ]
        node.repo_state["local_root"] = str(tmp_path)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            node.apply_approved_changes()

        # validate_pending_changes runs before any writes in the try block, so
        # a.py (which would have written fine) must NOT exist either.
        assert not (tmp_path / "a.py").exists()
        assert not (tmp_path / "b.py").exists()
        assert node.change_state == GITLINK_STATE_PREVIEWED

    def test_unrecognized_operation_value_does_not_silently_wipe_a_real_file(self, tmp_path):
        # Adversarial-review finding: validate_pending_changes originally
        # enumerated "update"/"create" by name, but apply_change_set's real
        # dispatch treats ANY non-"delete" operation as a write. A
        # session-restored proposal with a corrupted/unrecognized operation
        # value (here "modify" - a plausible typo/stale-schema value) used to
        # sail past validation and reach the write branch, which defaults
        # missing content to "" and overwrote a real file's real content.
        important_file = tmp_path / "important.py"
        important_file.write_text("REAL CONTENT THAT MUST SURVIVE")
        node = GitlinkNode(parent_node=None)
        node.restore_saved_state(
            proposal_data={"files": [{"path": "important.py", "operation": "modify"}]},
        )
        node.repo_state["local_root"] = str(tmp_path)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            node.apply_approved_changes()

        assert important_file.read_text() == "REAL CONTENT THAT MUST SURVIVE"
        assert node.change_state == GITLINK_STATE_PREVIEWED

    def test_delete_of_nonexistent_file_is_a_no_op_within_the_full_flow(self, tmp_path):
        node = GitlinkNode(parent_node=None)
        node.pending_changes = [{"path": "never_existed.py", "operation": "delete"}]
        node.repo_state["local_root"] = str(tmp_path)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            node.apply_approved_changes()

        assert node.change_state == GITLINK_STATE_APPLIED
