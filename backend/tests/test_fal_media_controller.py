"""End-to-end, offline Fal Media Studio node controller tests.

Uses the real SceneDocument and SessionBus command/WS surfaces while
substituting only the remote paid Fal API. No network and no real charges.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.domain.graph import SceneDocument
from backend.domain.node_states import FalMediaState
from backend.events import SessionBus
from backend.fal_integration import FalError
from backend.fal_media_controller import FalMediaController
from backend.fal_media_domain import read_media
from backend.api.intents_fal_media import register_fal_media_intents
from graphlink_settings_store import SettingsManager

REQUEST_ID = "764cabcf-b745-4b3e-ae38-1200304cf45b"


class FakeFal:
    def __init__(self):
        self.calls = []
        self.finished = False

    def model_pricing(self, model_id):
        self.calls.append(("pricing", model_id))
        return {"prices": [{
            "endpoint_id": model_id, "unit_price": 0.02,
            "unit": "image", "currency": "USD",
        }]}

    def estimate_cost(self, model_id, *, quantity=1):
        self.calls.append(("estimate", model_id, quantity))
        return {"total_cost": 0.03, "currency": "USD"}

    def submit(self, model_id, arguments, *, options=None):
        self.calls.append(("submit", model_id, arguments, options))
        return {"request_id": REQUEST_ID, "status": "IN_QUEUE", "queue_position": 2}

    def status(self, model_id, request_id):
        self.calls.append(("status", model_id, request_id))
        if self.finished:
            return {"status": "COMPLETED"}
        return {"status": "IN_QUEUE", "queue_position": 1}

    def result(self, model_id, request_id):
        self.calls.append(("result", model_id, request_id))
        return {"images": [{"url": "https://v3.fal.media/files/output.png"}]}

    def cancel(self, model_id, request_id):
        self.calls.append(("cancel", model_id, request_id))
        return {"status": "CANCELLATION_REQUESTED"}

    def search_models(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {"models": [{
            "endpoint_id": "fal-ai/flux/schnell",
            "metadata": {"display_name": "FLUX Schnell", "category": "text-to-image"},
        }], "has_more": False, "next_cursor": ""}

    def model_pricing_batch(self, ids):
        self.calls.append(("pricing_batch", ids))
        return {"prices": [{
            "endpoint_id": "fal-ai/flux/schnell", "unit_price": 0.006,
            "unit": "image", "currency": "USD",
        }]}

    def model_details(self, model_id):
        self.calls.append(("details", model_id))
        return {"endpoint_id": model_id, "openapi": {
            "paths": {"/": {"post": {"requestBody": {"content": {
                "application/json": {
                    "schema": {"type": "object",
                               "properties": {"prompt": {"type": "string"}},
                               "required": ["prompt"]},
                },
            }}}}},
        }}


class Recorder:
    def __init__(self):
        self.snapshots = []

    async def send_json(self, payload):
        self.snapshots.append(payload)


def create_studio(tmp_path):
    doc = SceneDocument()
    node = doc.add_plugin_node("fal_media", 15, 30, None,
                               title="Fal Media Studio", state=FalMediaState())
    bus = SessionBus("fal-media-test")
    bus.register_topic("scene", doc.scene_payload)
    recorder = Recorder()
    bus.attach(recorder)
    manager = SettingsManager(tmp_path / "session.dat")
    register_fal_media_intents(bus, doc, manager)
    return doc, node, bus, manager, recorder


def invoke(bus, name, *args):
    return asyncio.run(bus.dispatch_intent("scene", name, list(args)))


def test_end_to_end_schema_selection_quote_billing_and_output(tmp_path, monkeypatch):
    doc, node, bus, manager, recorder = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)

    invoke(bus, "searchFalMediaCatalog", node.id, "image", "text-to-image", "flux", "")
    cat = json.loads(node.state.catalog_json)
    assert cat["status"] == "ready"
    assert cat["models"][0]["price"] == 0.006
    assert cat["models"][0]["unit"] == "image"
    assert len(fake.calls) == 2
    assert fake.calls[0][0] == "search"
    assert fake.calls[1][0] == "pricing_batch"

    invoke(bus, "saveFalMediaDraft", node.id, "image", "text-to-image", "fal-ai/flux/schnell",
           "FLUX Schnell", "A cinematic sunrise", '{"seed":99}')
    assert read_media(node)["modelId"] == "fal-ai/flux/schnell"
    invoke(bus, "inspectFalMediaModel", node.id, "fal-ai/flux/schnell")
    details = json.loads(node.state.schema_json)
    assert details["modelId"] == "fal-ai/flux/schnell"
    assert details["fields"][0]["name"] == "prompt"

    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image", "fal-ai/flux/schnell",
           "FLUX Schnell", "A cinematic sunrise", '{"seed":99}')
    quote = read_media(node)["quote"]
    assert quote["price"] == 0.02
    assert quote["estimate"] == 0.03
    assert quote["unit"] == "image"
    assert quote["status"] == "ready"
    assert len([c for c in fake.calls if c[0] == "submit"]) == 0

    with pytest.raises(FalError, match="Quote expired"):
        invoke(bus, "generateFalMedia", node.id, "not-the-reviewed-id")
    assert len([c for c in fake.calls if c[0] == "submit"]) == 0

    invoke(bus, "generateFalMedia", node.id, quote["id"])
    assert read_media(node)["status"] == "queued"
    assert read_media(node)["requestId"] == REQUEST_ID
    assert manager.get_fal_jobs()[0]["requestId"] == REQUEST_ID
    submissions = [c for c in fake.calls if c[0] == "submit"]
    assert submissions == [("submit", "fal-ai/flux/schnell",
                            {"seed": 99, "prompt": "A cinematic sunrise"}, {"store_io": False})]

    with pytest.raises(FalError):
        invoke(bus, "generateFalMedia", node.id, quote["id"])
    assert len([c for c in fake.calls if c[0] == "submit"]) == 1

    invoke(bus, "refreshFalMedia", node.id)
    assert read_media(node)["status"] == "queued"
    assert read_media(node)["queuePosition"] == 1
    fake.finished = True
    invoke(bus, "refreshFalMedia", node.id)
    after = read_media(node)
    assert after["status"] == "completed"
    assert len(after["history"]) == 1
    assert after["history"][0]["outputs"][0]["url"] == "https://v3.fal.media/files/output.png"
    assert after["quote"] is None
    assert "fal-ai/flux/schnell" in recorder.snapshots[-1]["payload"]["nodes"][0]["falMediaJson"]


def test_favorite_setting_is_replay_safe_and_reflected_in_catalog(tmp_path, monkeypatch):
    doc, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "searchFalMediaCatalog", node.id, "image", "text-to-image", "", "")
    invoke(bus, "setFalMediaFavorite", node.id, "fal-ai/flux/schnell", True)
    invoke(bus, "setFalMediaFavorite", node.id, "fal-ai/flux/schnell", True)
    assert manager.get_fal_media_favorites() == ["fal-ai/flux/schnell"]
    assert json.loads(node.state.catalog_json)["favorites"] == ["fal-ai/flux/schnell"]
    invoke(bus, "setFalMediaFavorite", node.id, "fal-ai/flux/schnell", False)
    assert manager.get_fal_media_favorites() == []


def test_cannot_modify_active_generation_parameters(tmp_path, monkeypatch):
    _, node, bus, _, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    initial = ("image", "text-to-image", "fal-ai/flux/schnell", "Schnell",
               "A solar eclipse", "{}")
    invoke(bus, "prepareFalMedia", node.id, *initial)
    nonce = read_media(node)["quote"]["id"]
    invoke(bus, "generateFalMedia", node.id, nonce)
    with pytest.raises(FalError, match="active"):
        invoke(bus, "saveFalMediaDraft", node.id, "video", "text-to-video",
               "fal-ai/video", "Video", "Different prompt", "{}")
    assert read_media(node)["prompt"] == "A solar eclipse"


def test_stale_nonce_refused_if_inputs_changed(tmp_path, monkeypatch):
    _, node, bus, _, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    initial = ("image", "text-to-image", "fal-ai/flux/schnell",
               "Schnell", "Initial text", "{}")
    invoke(bus, "prepareFalMedia", node.id, *initial)
    old_nonce = read_media(node)["quote"]["id"]
    invoke(bus, "saveFalMediaDraft", node.id, "image", "text-to-image", "fal-ai/flux/schnell",
           "Schnell", "Updated text", "{}")
    with pytest.raises(FalError, match="Quote expired"):
        invoke(bus, "generateFalMedia", node.id, old_nonce)
    assert [entry for entry in fake.calls if entry[0] == "submit"] == []


def test_remote_cancellation_is_user_initiated_and_does_not_rebill(tmp_path, monkeypatch):
    _, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
           "fal-ai/flux/schnell", "Schnell", "A photograph", "{}")
    invoke(bus, "generateFalMedia", node.id, read_media(node)["quote"]["id"])
    invoke(bus, "cancelFalMedia", node.id)
    assert read_media(node)["status"] == "cancelling"
    assert fake.calls[-1][0] == "cancel"
    assert len([entry for entry in fake.calls if entry[0] == "submit"]) == 1


def test_catalog_search_invalid_model_category_rejected_without_remote_call(tmp_path, monkeypatch):
    _, node, bus, _, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "searchFalMediaCatalog", node.id, "image", "text-to-video", "", "")
    assert json.loads(node.state.catalog_json)["status"] == "error"
    assert fake.calls == []


def test_failed_submit_requires_explicit_dashboard_acknowledgement_before_retry(tmp_path, monkeypatch):
    _, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()

    def uncertain_submit(model_id, arguments, *, options=None):
        raise FalError("Could not reach fal.ai (ReadTimeout).")

    fake.submit = uncertain_submit
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
           "fal-ai/flux/schnell", "Schnell", "A painting", "{}")
    nonce = read_media(node)["quote"]["id"]
    invoke(bus, "generateFalMedia", node.id, nonce)
    assert read_media(node)["status"] == "unconfirmed"
    assert "Check the Fal dashboard" in read_media(node)["error"]
    assert read_media(node)["requestId"] == ""
    with pytest.raises(FalError, match="Wait"):
        invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
               "fal-ai/flux/schnell", "Schnell", "A painting", "{}")
    with pytest.raises(FalError, match="already"):
        invoke(bus, "generateFalMedia", node.id, nonce)
    assert manager.get_fal_jobs() == []

    invoke(bus, "acknowledgeFalMediaSubmission", node.id)
    assert read_media(node)["status"] == "idle"
    assert read_media(node)["quote"] is None
    with pytest.raises(FalError):
        invoke(bus, "acknowledgeFalMediaSubmission", node.id)
    with pytest.raises(FalError, match="Quote expired"):
        invoke(bus, "generateFalMedia", node.id, nonce)


def test_no_preview_when_remote_response_is_from_an_untrusted_origin(tmp_path):
    from backend.fal_media_preview import safe_media_host
    assert not safe_media_host("https://fal.media.evil.example/asset.mp4")


def test_accepted_paid_job_id_survives_settings_history_write_failure(tmp_path, monkeypatch):
    _, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)

    def broken_ledger(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(manager, "upsert_fal_job", broken_ledger)
    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
           "fal-ai/flux/schnell", "Schnell", "Mountain lake", "{}")
    invoke(bus, "generateFalMedia", node.id, read_media(node)["quote"]["id"])

    media = read_media(node)
    assert media["status"] == "queued"
    assert media["requestId"] == REQUEST_ID
    assert "Settings history" in media["error"]
    assert len([call for call in fake.calls if call[0] == "submit"]) == 1
    with pytest.raises(FalError, match="already"):
        invoke(bus, "generateFalMedia", node.id, "reused-approval")


def test_restored_quote_cannot_authorize_paid_generation(tmp_path, monkeypatch):
    doc, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
           "fal-ai/flux/schnell", "Schnell", "Sunrise", "{}")
    previous_nonce = read_media(node)["quote"]["id"]

    # The scene persists a UI quote but not the server-side one-time consent.
    restarted_controller = FalMediaController(bus, doc, manager)
    with pytest.raises(FalError, match="Quote expired"):
        asyncio.run(restarted_controller.generate(node.id, previous_nonce))
    assert not [call for call in fake.calls if call[0] == "submit"]


def test_interrupted_submission_requires_user_dashboard_check(tmp_path):
    from backend.fal_media_domain import store_media

    doc, node, bus, manager, _ = create_studio(tmp_path)
    media = read_media(node)
    media.update({"status": "submitting", "modelId": "fal-ai/flux/schnell"})
    store_media(node, media)
    recovered = FalMediaController(bus, doc, manager)

    # A currently active POST must not be misclassified as interrupted.
    recovered._submissions_in_flight.add(node.id)
    asyncio.run(recovered.refresh(node.id))
    assert read_media(node)["status"] == "submitting"

    recovered._submissions_in_flight.discard(node.id)
    asyncio.run(recovered.refresh(node.id))
    assert read_media(node)["status"] == "unconfirmed"
    assert "dashboard" in read_media(node)["error"].lower()
    with pytest.raises(FalError, match="Wait"):
        invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
               "fal-ai/flux/schnell", "Schnell", "Sunrise", "{}")
    invoke(bus, "acknowledgeFalMediaSubmission", node.id)
    assert read_media(node)["status"] == "idle"


def test_failed_fal_job_is_recorded_as_failed_not_completed(tmp_path, monkeypatch):
    _, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
           "fal-ai/flux/schnell", "Schnell", "Sky", "{}")
    invoke(bus, "generateFalMedia", node.id, read_media(node)["quote"]["id"])
    fake.status = lambda *_: {"status": "COMPLETED", "error": "model failed"}
    invoke(bus, "refreshFalMedia", node.id)
    assert read_media(node)["status"] == "failed"
    assert manager.get_fal_jobs()[0]["status"] == "FAILED"


def test_stale_status_poll_cannot_undo_successful_cancellation(tmp_path, monkeypatch):
    import threading

    doc, node, bus, manager, _ = create_studio(tmp_path)
    fake = FakeFal()
    monkeypatch.setattr(FalMediaController, "_client", lambda self: fake)
    invoke(bus, "prepareFalMedia", node.id, "image", "text-to-image",
           "fal-ai/flux/schnell", "Schnell", "Sky", "{}")
    invoke(bus, "generateFalMedia", node.id, read_media(node)["quote"]["id"])

    started = threading.Event()
    release = threading.Event()

    def slow_status(*args):
        started.set()
        release.wait(timeout=5)
        return {"status": "IN_QUEUE", "queue_position": 1}

    fake.status = slow_status
    fake.cancel = lambda *_: {"status": "OK"}
    controller = FalMediaController(bus, doc, manager)

    async def race():
        poll = asyncio.create_task(controller.refresh(node.id))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            await controller.cancel(node.id)
        finally:
            release.set()
        await poll

    asyncio.run(race())
    assert read_media(node)["status"] == "cancelled"
