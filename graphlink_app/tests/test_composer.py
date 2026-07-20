"""Regression coverage for Composer draft and request lifecycle contracts."""

from graphlink_composer import ComposerAttachment, ComposerController, ComposerRequestState


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


def test_submitted_text_clears_immediately_and_returns_on_failure():
    controller = ComposerController()
    request_id = controller.begin_request(text="Retry this prompt")

    assert controller.clear_submitted_text()
    assert controller.draft.text == ""

    assert controller.fail(request_id, "Provider unavailable")
    assert controller.draft.text == "Retry this prompt"


def test_submitted_text_stays_empty_after_success():
    controller = ComposerController()
    request_id = controller.begin_request(text="Send this prompt")

    controller.clear_submitted_text()
    assert controller.complete(request_id)
    controller.clear_after_success()

    assert controller.draft.text == ""
