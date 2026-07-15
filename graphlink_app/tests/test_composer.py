"""Regression coverage for Composer draft and request lifecycle contracts."""

from PySide6.QtTest import QSignalSpy

from graphlink_composer import ComposerAttachment, ComposerController, ComposerRequestState
from graphlink_widgets import ComposerWidget


def test_controller_request_snapshot_is_immutable_and_tracks_attachments():
    controller = ComposerController()
    attachment = {"path": "C:/notes.md", "name": "notes.md", "kind": "document"}

    request_id = controller.begin_request(text="Summarize this", attachments=[attachment])

    assert controller.state is ComposerRequestState.PREPARING
    assert controller.active_snapshot.text == "Summarize this"
    assert controller.active_snapshot.attachment_paths == ("C:/notes.md",)
    assert controller.is_current(request_id)


def test_controller_ignores_stale_completion_and_preserves_failed_draft():
    controller = ComposerController()
    controller.update_text("Keep this if the request fails")
    controller.set_attachments([ComposerAttachment("a", "a.txt", "a.txt", "document")])
    request_id = controller.begin_request(text=controller.draft.text)

    assert not controller.complete("stale", "wrong request")
    assert controller.fail(request_id, "Provider unavailable")
    assert controller.state is ComposerRequestState.FAILED
    assert controller.draft.text == "Keep this if the request fails"
    assert len(controller.draft.attachments) == 1


def test_controller_round_trips_restored_draft_and_clears_only_after_success():
    controller = ComposerController()
    controller.restore_draft({
        "draft_id": "saved-draft",
        "text": "Continue from here",
        "context_mode": "selection",
        "context_refs": ["node-1"],
        "attachments": [{"path": "a.py", "name": "a.py", "kind": "document", "token_count": 12}],
    })

    assert controller.draft.restored
    assert controller.draft.draft_id == "saved-draft"
    assert controller.draft.context_refs == ["node-1"]
    assert controller.serialize_draft()["attachments"][0]["token_count"] == 12

    request_id = controller.begin_request(text=controller.draft.text)
    assert controller.complete(request_id)
    assert controller.draft.text == "Continue from here"
    controller.clear_after_success()
    assert controller.draft.text == ""
    assert controller.draft.attachments == []


def test_composer_exposes_visible_context_and_accessible_actions():
    composer = ComposerWidget()
    composer.set_context_anchor(type("Node", (), {"title": "Chart analysis"})())
    composer.set_context_items([{"path": "chart.csv", "name": "chart.csv", "kind": "document"}])

    assert composer.context_label.text() == "Responding to Chart analysis"
    assert "1 attachment" in composer.context_summary.text()
    assert composer.send_button.accessibleName() == "Send message"
    assert composer.attach_file_btn.accessibleName() == "Attach context"
    assert composer.context_review_button.isEnabled()


def test_composer_forwards_send_and_preserves_text_on_clear_boundary():
    composer = ComposerWidget()
    spy = QSignalSpy(composer.sendRequested)
    composer.setText("hello graph")
    composer.send_button.click()

    assert spy.count() == 1
    assert composer.text() == "hello graph"
    composer.clear()
    assert composer.text() == ""
