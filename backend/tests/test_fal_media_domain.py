"""No-network tests for Fal Media Studio's pricing, schemas, queue and state.

These tests intentionally never call an external Fal endpoint: results and
pricing are controlled so a 'zero' price can never be accidentally fabricated.
"""
from __future__ import annotations

import json

import pytest

from backend.domain.graph import SceneDocument
from backend.domain.node_states import FalMediaState
from backend.fal_integration import FalError
from backend.fal_media_domain import (
    completed_media, fresh_media, model_catalog_page, outputs_for,
    parse_arguments, read_media, request_arguments, store_media,
    summarize_schema, validate_edit,
)
from graphlink_settings_store import SettingsManager


class FakeCatalogClient:
    def __init__(self, rates=None, should_fail=False):
        self.searches = []
        self.rates = rates or []
        self.should_fail = should_fail

    def search_models(self, *, query, category, limit, cursor):
        self.searches.append((query, category, limit, cursor))
        return {
            "models": [
                {"endpoint_id": "fal-ai/model-premium",
                 "metadata": {"display_name": "Studio Premium", "description": "High quality"}},
                {"endpoint_id": "fal-ai/model-economy",
                 "metadata": {"display_name": "Studio Economy", "description": "Fast and affordable"}},
                {"endpoint_id": "fal-ai/model-unknown",
                 "metadata": {"display_name": "Undisclosed rate"}},
                {"endpoint_id": "fal-ai/model-unit",
                 "metadata": {"display_name": "Megapixel billed"}},
                {"endpoint_id": "https://bad.example/path",
                 "metadata": {"display_name": "Unsafe endpoint"}},
            ],
            "has_more": True, "next_cursor": "page2",
        }

    def model_pricing_batch(self, ids):
        if self.should_fail:
            raise FalError("Pricing API unavailable.")
        return {"prices": self.rates}


def test_pricing_sort_groups_identical_units_and_puts_unpriced_last():
    rates = [
        {"endpoint_id": "fal-ai/model-premium", "unit_price": 0.35, "unit": "image", "currency": "USD"},
        {"endpoint_id": "fal-ai/model-economy", "unit_price": 0.01, "unit": "image", "currency": "USD"},
        {"endpoint_id": "fal-ai/model-unit", "unit_price": 0.002, "unit": "megapixel", "currency": "USD"},
    ]
    client = FakeCatalogClient(rates=rates)
    result = model_catalog_page(client, mode="image", operation="text-to-image", query="flux", limit=10)
    assert client.searches == [("flux", "text-to-image", 10, None)]
    assert [m["id"] for m in result["models"]] == [
        "fal-ai/model-economy", "fal-ai/model-premium",
        "fal-ai/model-unit", "fal-ai/model-unknown",
    ]
    assert result["models"][0]["price"] == 0.01
    assert result["models"][1]["unit"] == "image"
    assert result["models"][2]["unit"] == "megapixel"
    assert result["models"][3]["price"] is None
    assert result["hasMore"] is True and result["nextCursor"] == "page2"


def test_failed_pricing_never_guesses_free_or_zero_cost():
    result = model_catalog_page(FakeCatalogClient(should_fail=True),
                                mode="video", operation="text-to-video", query="")
    assert result["pricingWarning"]
    assert all(item["price"] is None for item in result["models"])


@pytest.mark.parametrize("invalid", [
    ("still", "text-to-image"), ("video", "text-to-image"),
    ("audio", "image-to-video"), ("image", "unknown"),
])
def test_invalid_media_category_is_rejected(invalid):
    client = FakeCatalogClient()
    with pytest.raises(FalError, match="category"):
        model_catalog_page(client, mode=invalid[0], operation=invalid[1], query="")
    assert client.searches == []


def test_openapi_schema_controls_are_bounded_and_keep_required_fields():
    details = {"openapi": {
        "openapi": "3.0.0",
        "components": {"schemas": {
            "Input": {"type": "object",
                      "required": ["prompt", "duration"],
                      "properties": {
                          "prompt": {"type": "string", "description": "What to generate"},
                          "duration": {"type": "integer", "enum": [5, 10, 15], "default": 5},
                          "image_url": {"type": "string", "format": "uri"},
                          "seed": {"type": "integer"},
                      }},
        }},
        "paths": {"/": {"post": {"requestBody": {"content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Input"}}
        }}}}},
    }}
    result = summarize_schema(details)
    assert result["warning"] == ""
    assert [field["name"] for field in result["fields"]] == ["prompt", "duration", "image_url", "seed"]
    assert result["required"] == ["prompt", "duration"]
    duration = result["fields"][1]
    assert duration["enum"] == [5, 10, 15]
    assert duration["default"] == 5


def test_state_edit_and_request_args_are_exactly_model_specific():
    media = fresh_media()
    changed = validate_edit(media, "image", "text-to-image", "fal-ai/model",
                            "Example", "Create a sunrise", '{"seed":100,"num_images":2}')
    assert changed["modelId"] == "fal-ai/model"
    assert changed["prompt"] == "Create a sunrise"
    assert request_arguments(changed) == {
        "seed": 100, "num_images": 2, "prompt": "Create a sunrise",
    }
    assert media["modelId"] == ""
    changed["status"] = "queued"
    with pytest.raises(FalError, match="active"):
        validate_edit(changed, "video", "text-to-video", "fal-ai/model2",
                      "Other", "Prompt", "{}")


@pytest.mark.parametrize("bad", ["null", "[]", "NaN", '{"a": NaN}', "not-json"])
def test_bad_model_json_is_rejected_before_any_remote_call(bad):
    with pytest.raises(FalError):
        parse_arguments(bad)


def test_speech_prompt_maps_to_text_parameter_not_image_prompt():
    media = validate_edit(fresh_media(), "audio", "text-to-speech",
                          "fal-ai/elevenlabs/tts/multilingual-v2",
                          "ElevenLabs", "Welcome to our studio", '{"voice":"calm"}')
    assert request_arguments(media) == {
        "voice": "calm", "text": "Welcome to our studio",
    }
    media["argumentsJson"] = '{"text":"Advanced transcript","voice":"calm"}'
    assert request_arguments(media)["text"] == "Advanced transcript"
    assert "prompt" not in request_arguments(media)


def test_output_gallery_handles_multiple_modalities_without_blob_inline():
    results = {
        "images": [{"url": "https://v3.fal.media/files/one.png"}],
        "video": {"url": "https://v3.fal.media/files/clip.mp4"},
        "audio": {"url": "https://v3.fal.media/files/voice.mp3"},
    }
    out = outputs_for(results, "video")
    assert set(x["kind"] for x in out) == {"image", "video", "audio"}
    assert len(out) == 3
    assert all("data:" not in x["url"] for x in out)
    media = fresh_media()
    media.update({"modelId": "fal-ai/model", "modelName": "Model", "mode": "video",
                  "prompt": "Movie", "requestId": "abc"})
    finished = completed_media(media, results, now="2026-11-13T00:00:00Z")
    assert finished["status"] == "completed"
    assert len(finished["history"][0]["outputs"]) == 3
    assert finished["history"][0]["requestId"] == "abc"
    assert media["history"] == []


def test_bad_media_url_ports_are_ignored_without_discarding_valid_outputs():
    result = {
        "images": [
            {"url": "https://v3.fal.media:not-a-port/image.png"},
            {"url": "https://v3.fal.media/files/good.png"},
            {"url": "https://user:password@v3.fal.media/files/signed.png"},
        ]
    }
    assert outputs_for(result, "image") == [
        {"url": "https://v3.fal.media/files/good.png", "kind": "image"}
    ]


def test_node_media_serialization_only_saves_bounded_model_state():
    doc = SceneDocument()
    node = doc.add_plugin_node("fal_media", 20, 40, None,
                               title="Fal Media Studio", state=FalMediaState())
    media = fresh_media()
    media["prompt"] = "Golden hour"
    media["modelId"] = "fal-ai/model"
    media["quote"] = {"status": "ready", "id": "secret-nonce"}
    store_media(node, media)
    node.state.catalog_json = json.dumps({"models": [1, 2, 3]})
    node.state.schema_json = json.dumps({"large": True})
    from backend.session_save import _serialize_fal_media_node
    from backend.session_load import _restore_fal_media_payload
    saved = _serialize_fal_media_node(node)
    assert saved["media"]["quote"] is None
    assert "catalog" not in saved and "schema" not in saved
    restored = _restore_fal_media_payload({"position": {"x": 20, "y": 40}, **saved})
    assert restored.kind == "fal_media"
    assert read_media(restored)["prompt"] == "Golden hour"
    assert restored.state.catalog_json == ""
    assert restored.state.schema_json == ""


def test_interrupted_submission_never_silently_retries_a_billable_job():
    from backend.session_load import _restore_fal_media_payload
    media = fresh_media()
    media["status"] = "submitting"
    restored = _restore_fal_media_payload({"position": {"x": 0, "y": 0},
                                          "node_type": "fal_media", "media": media})
    assert read_media(restored)["status"] == "unconfirmed"
    assert "Check your Fal dashboard" in read_media(restored)["error"]


def test_favorite_state_is_global_bounded_idempotent_and_survives_restart(tmp_path):
    path = tmp_path / "session.dat"
    manager = SettingsManager(path)
    assert manager.get_fal_media_favorites() == []
    manager.set_fal_media_favorite("fal-ai/model", True)
    manager.set_fal_media_favorite("fal-ai/model", True)
    manager.set_fal_media_favorite("fal-ai/second", True)
    assert manager.get_fal_media_favorites() == ["fal-ai/model", "fal-ai/second"]
    assert SettingsManager(path).get_fal_media_favorites() == ["fal-ai/model", "fal-ai/second"]
    manager.set_fal_media_favorite("fal-ai/model", False)
    assert manager.get_fal_media_favorites() == ["fal-ai/second"]
