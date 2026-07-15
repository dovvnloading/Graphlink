"""Focused regression coverage for canvas utilities and group invariants."""

from unittest.mock import MagicMock

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent, Qt

from graphlink_canvas_items import Container, Frame, Note
from graphlink_scene import ChatScene
from graphlink_session.serializers import SceneSerializer
from graphlink_utility import (
    UtilityKind,
    UtilityOperationController,
    UtilityOperationState,
    render_context,
    source_snapshot,
)


def make_scene():
    window = MagicMock()
    scene = ChatScene(window)
    window.chat_view.scene.return_value = scene
    return window, scene


def test_frame_construction_uses_installed_qtawesome_icons():
    _, scene = make_scene()
    node = scene.add_chat_node("A")
    frame = Frame([node])
    scene.addItem(frame)
    scene.frames.append(frame)
    assert frame.lock_icon is not None
    assert frame.unlock_icon is not None


def test_group_moves_remove_stale_membership_and_preserve_invariants():
    _, scene = make_scene()
    first = scene.add_chat_node("first")
    second = scene.add_chat_node("second")
    first.setSelected(True)
    second.setSelected(True)
    scene.createContainer()
    container = scene.containers[-1]
    assert scene.validate_group_invariants() == []

    scene.clearSelection()
    first.setSelected(True)
    scene.createFrame()
    frame = scene.frames[-1]

    assert first in frame.nodes
    assert first not in container.contained_items
    assert second in container.contained_items
    assert scene.validate_group_invariants() == []


def test_nested_container_delete_reparents_children_without_dangling_parent_entry():
    _, scene = make_scene()
    node = scene.add_chat_node("child")
    inner = Container([node])
    scene.addItem(inner)
    scene.containers.append(inner)
    outer = Container([inner])
    scene.addItem(outer)
    scene.containers.append(outer)

    scene.deleteContainer(inner)

    assert inner not in outer.contained_items
    assert node in outer.contained_items
    assert node.parentItem() is outer
    assert scene.validate_group_invariants() == []


def test_utility_operation_controller_guards_completion_after_cancel():
    controller = UtilityOperationController()
    source = source_snapshot(MagicMock(scenePos=lambda: QPointF(10, 20)), "source")
    operation_id = controller.begin(UtilityKind.TAKEAWAY, [source], chat_epoch=3)
    controller.mark_running(operation_id)
    assert controller.cancel(operation_id)
    assert controller.get(operation_id).state == UtilityOperationState.CANCELLED
    assert controller.complete(operation_id, "late result") is None


def test_utility_context_is_bounded_and_reports_omitted_sources():
    source_a = source_snapshot(MagicMock(scenePos=lambda: QPointF()), "a" * 20)
    source_b = source_snapshot(MagicMock(scenePos=lambda: QPointF()), "b" * 20)
    rendered, omitted = render_context([source_a, source_b], max_chars=35)
    assert "Source 1" in rendered
    assert source_b.source_id in omitted


def test_note_provenance_and_exact_frame_geometry_are_serialized():
    window, scene = make_scene()
    node = scene.add_chat_node("source")
    note = scene.add_note(QPointF(30, 40))
    note.content = "generated"
    note.note_role = "explainer"
    note.operation_id = "operation-1"
    note.source_ids = [node.persistent_id if hasattr(node, "persistent_id") else "source-1"]
    frame = Frame([node])
    scene.addItem(frame)
    scene.frames.append(frame)
    frame.rect = QRectF(2, 3, 410, 220)
    frame.expanded_rect = QRectF(4, 5, 510, 320)

    payload = SceneSerializer(window).serialize_chat_data()
    note_payload = payload["notes_data"][0]
    frame_payload = payload["frames"][0]
    assert note_payload["role"] == "explainer"
    assert note_payload["operation_id"] == "operation-1"
    assert frame_payload["rect"]["width"] == 410
    assert frame_payload["expanded_rect"]["height"] == 320


def test_utility_items_are_searchable_and_note_picker_is_safe_without_a_view():
    _, scene = make_scene()
    note = scene.add_note(QPointF())
    note.content = "utility provenance"
    note.show_color_picker()
    assert note in scene.find_items("provenance")


def test_note_editor_supports_bounded_content_and_undo_redo():
    note = Note(QPointF())
    note.editing = True
    note.edit_text = "before"
    note.cursor_pos = len(note.edit_text)
    note.selection_start = note.selection_end = note.cursor_pos
    note._push_edit_snapshot()
    note.edit_text += " after"
    note.undo_edit()
    assert note.edit_text == "before"
    note.redo_edit()
    assert note.edit_text == "before after"
    note.content = "x" * (note.MAX_CONTENT_LENGTH + 10)
    assert len(note.content) == note.MAX_CONTENT_LENGTH


def test_frame_can_shrink_again_after_manual_resize():
    _, scene = make_scene()
    node = scene.add_chat_node("small")
    frame = Frame([node])
    scene.addItem(frame)
    scene.frames.append(frame)
    frame.rect = QRectF(0, 0, 1200, 900)
    frame._user_resized = True
    frame.fit_to_content()
    assert frame.rect.width() < 1200
    assert frame.rect.height() < 900
