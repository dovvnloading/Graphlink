"""No-network regression tests for Graphlink's full fal.ai integration.

All tests use deterministic mock transports: no charges, no real API calls.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from backend.fal_integration import (
    FalClient, FalError, media_urls,
)
from backend.providers.base import ToolCall
from backend.tools import FS_READ, PROVIDER_CALL, RunContext, ToolRegistry
from backend.tools_fal import register_fal_tools
from graphlink_settings_store import SettingsManager

REQUEST_ID = "764cabcf-b745-4b3e-ae38-1200304cf45b"


class FakeResponse:
    def __init__(self, payload, status=200, lines=()):
        self.status_code = status
        self.payload = payload
        self.content = json.dumps(payload).encode()
        self.headers = {"Content-Type": "application/json"}
        self.lines = lines

    def iter_content(self, chunk_size=65536):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset+chunk_size]

    def json(self):
        return self.payload

    def iter_lines(self, decode_unicode=False):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected request!")
        return self.responses.pop(0)


def client(*responses):
    session = FakeSession(*responses)
    return FalClient("test-id:test-secret", session=session), session


def test_universal_nested_model_ids_and_host_and_key():
    c, session = client(FakeResponse({"request_id": REQUEST_ID, "status": "IN_QUEUE"}))
    result = c.submit("fal-ai/flux/schnell", {"prompt": "hello"})
    assert result["request_id"] == REQUEST_ID
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://queue.fal.run/fal-ai/flux/schnell")
    assert kwargs["headers"]["Authorization"] == "Key test-id:test-secret"
    assert kwargs["json"] == {"prompt": "hello"}
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("bad", [
    "", "fal-ai", "../x", "/fal-ai/model", "fal-ai/../secret",
    "fal-ai/model?x=1", "fal-ai/model#fragment", "http://evil.com/model",
    "fal-ai//model", "fal-ai/model/", "fal-ai/%2F", "fal-ai/model\nHost: evil",
    "fal-ai/model:123", "fal-ai/./foo",
])
def test_rejects_model_path_injection_before_network(bad):
    c, session = client()
    with pytest.raises(FalError):
        c.run(bad, {})
    assert session.calls == []


def test_nested_queues_have_two_segment_request_routes():
    c, session = client(
        FakeResponse({"status": "IN_PROGRESS", "request_id": REQUEST_ID}),
        FakeResponse({"images": [{"url": "https://v3.fal.media/files/test.png"}]}),
        FakeResponse({"status": "CANCELLATION_REQUESTED"}),
    )
    model = "blackforestlabs/flux-3-max/text-to-video"
    assert c.status(model, REQUEST_ID)["status"] == "IN_PROGRESS"
    assert c.result(model, REQUEST_ID)["images"]
    assert c.cancel(model, REQUEST_ID)["status"] == "CANCELLATION_REQUESTED"
    urls = [item[1] for item in session.calls]
    assert urls == [
        f"https://queue.fal.run/blackforestlabs/flux-3-max/requests/{REQUEST_ID}/status",
        f"https://queue.fal.run/blackforestlabs/flux-3-max/requests/{REQUEST_ID}",
        f"https://queue.fal.run/blackforestlabs/flux-3-max/requests/{REQUEST_ID}/cancel",
    ]


def test_invalid_uuid_never_emits_any_http_request():
    c, session = client()
    with pytest.raises(FalError, match="UUID"):
        c.result("fal-ai/model", "../../../secrets")
    assert session.calls == []


def test_request_options_are_checked_and_translated():
    c, session = client(FakeResponse({"request_id": REQUEST_ID}))
    c.submit("fal-ai/model", {"prompt": "ok"}, options={
        "priority": "low", "start_timeout": 45,
        "runner_hint": "my-lora", "no_retry": True, "store_io": False,
        "object_lifecycle": {"expiration_duration_seconds": 3600},
        "retry_config": {"server_error": {"retries": 0}},
        "max_queue_length": 15, "webhook_url": "https://example.org/webhooks/fal",
    })
    _, url, kwargs = session.calls[0]
    assert "fal_max_queue_length=15" in url
    assert "fal_webhook=https%3A%2F%2Fexample.org%2Fwebhooks%2Ffal" in url
    assert kwargs["headers"]["X-Fal-Request-Start-Timeout"] == "45"
    assert kwargs["headers"]["X-Fal-Queue-Priority"] == "low"
    assert kwargs["headers"]["X-Fal-Store-IO"] == "0"
    assert kwargs["headers"]["X-Fal-No-Retry"] == "true"
    assert json.loads(kwargs["headers"]["X-Fal-Object-Lifecycle-Preference"]) == {
        "expiration_duration_seconds": 3600,
    }


@pytest.mark.parametrize("opts", [
    {"priority": "high"}, {"start_timeout": 0.01}, {"store_io": "false"},
    {"Authorization": "attacker-key"}, {"webhook_url": "http://evil.invalid"},
    {"max_queue_length": -2}, {"runner_hint": "x\r\nAuthorization: evil"},
])
def test_invalid_options_cannot_change_destination_or_headers(opts):
    c, session = client()
    with pytest.raises(FalError):
        c.submit("fal-ai/model", {}, options=opts)
    assert session.calls == []


def test_sync_is_separate_hostname_and_cannot_take_queue_only_options():
    c, session = client(FakeResponse({"video": {"url": "https://v3.fal.media/clip.mp4"}}))
    assert c.run("fal-ai/model", {"prompt": "a cat"})["video"]
    assert session.calls[0][1] == "https://fal.run/fal-ai/model"
    with pytest.raises(FalError, match="queue-only"):
        c.run("fal-ai/model", {}, options={"webhook_url": "https://example.org/"})
    assert len(session.calls) == 1


def test_http_errors_are_sanitized_and_redirects_not_followed():
    c, session = client(FakeResponse({"detail": "test-id:test-secret"}, status=422))
    with pytest.raises(FalError) as err:
        c.run("fal-ai/model", {})
    assert "422" in str(err.value)
    assert "test-secret" not in str(err.value)
    assert session.calls[0][2]["allow_redirects"] is False
    c2, _ = client(FakeResponse({"detail": "https://evil.invalid"}, status=302))
    with pytest.raises(FalError, match="302"):
        c2.run("fal-ai/model", {})


def test_http_response_is_bounded_while_streaming():
    class OversizedResponse(FakeResponse):
        def __init__(self):
            super().__init__({})

        def iter_content(self, chunk_size=65536):
            for _ in range(80):
                yield b"x" * chunk_size

    c, session = client(OversizedResponse())
    with pytest.raises(FalError, match="larger than 4 MiB"):
        c.run("fal-ai/model", {})
    assert session.calls[0][2]["stream"] is True


def test_large_http_content_length_is_rejected_before_body_read():
    response = FakeResponse({"ok": True})
    response.headers["Content-Length"] = str(8 * 1024 * 1024)

    def forbidden_stream(chunk_size=65536):
        raise AssertionError("body should not be consumed")
        yield b""  # pragma: no cover

    response.iter_content = forbidden_stream
    c, _ = client(response)
    with pytest.raises(FalError, match="larger than 4 MiB"):
        c.run("fal-ai/model", {})


@pytest.mark.parametrize("webhook", [
    "https://127.0.0.1/callback",
    "https://[::1]/callback",
    "https://10.1.2.3/callback",
    "https://localhost/callback",
    "https://my-server.local/callback",
    "https://example.org:444/callback",
    "https://example.org:notaport/callback",
])
def test_webhook_must_not_target_private_or_local_hosts(webhook):
    c, session = client()
    with pytest.raises(FalError, match="public HTTPS"):
        c.submit("fal-ai/model", {}, options={"webhook_url": webhook})
    assert session.calls == []


def test_success_must_have_valid_request_uuid():
    c, session = client(FakeResponse({"request_id": "not-a-uuid"}))
    with pytest.raises(FalError, match="UUID"):
        c.submit("fal-ai/model", {})
    assert len(session.calls) == 1


def test_wait_handles_queue_transition_and_returns_real_result(monkeypatch):
    c, session = client(
        FakeResponse({"status": "IN_QUEUE"}),
        FakeResponse({"status": "IN_PROGRESS"}),
        FakeResponse({"status": "COMPLETED"}),
        FakeResponse({"images": [{"url": "https://v3.fal.media/ok.png"}]}),
    )
    monkeypatch.setattr("backend.fal_integration.time.sleep", lambda seconds: None)
    output = c.wait("fal-ai/model", REQUEST_ID, timeout=60)
    assert output["images"][0]["url"].endswith("ok.png")
    assert len(session.calls) == 4


def test_completed_queue_status_is_not_necessarily_a_success():
    c, session = client(FakeResponse({"status": "COMPLETED", "error": "generation failed"}))
    with pytest.raises(FalError, match="failed"):
        c.wait("fal-ai/model", REQUEST_ID)
    assert len(session.calls) == 1


def test_sse_stream_is_bounded_to_fal_hostname_and_stops_at_completed():
    events = [
        'data: {"status":"IN_QUEUE"}',
        ': keepalive',
        'data: {"status":"IN_PROGRESS"}',
        'data: {"status":"COMPLETED"}',
        'data: {"status":"IN_PROGRESS"}',
    ]
    c, session = client(FakeResponse({}, lines=events))
    result = list(c.stream_status("fal-ai/model", REQUEST_ID))
    assert [s["status"] for s in result] == ["IN_QUEUE", "IN_PROGRESS", "COMPLETED"]
    assert session.calls[0][1] == f"https://queue.fal.run/fal-ai/model/requests/{REQUEST_ID}/status/stream"
    assert session.calls[0][2]["stream"] is True


def test_media_url_discovery_supports_arbitrary_model_shapes():
    output = {"images": [{"url": "https://cdn.example/one.png"}],
              "video": {"url": "https://cdn.example/movie.mp4"},
              "untrusted": {"url": "http://insecure.example/a"}}
    assert set(media_urls(output)) == {
        "https://cdn.example/one.png", "https://cdn.example/movie.mp4",
    }


def test_api_key_sources_and_bounded_persistent_job_history(tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setenv("GRAPHLINK_FAL_API_KEY", "env-key")
    path = tmp_path / "session.dat"
    manager = SettingsManager(path)
    assert manager.get_fal_api_key() == "env-key"
    assert manager.get_fal_api_key_source() == "environment"
    manager.set_fal_api_key("saved-key")
    assert manager.get_fal_api_key() == "saved-key"
    assert manager.get_fal_api_key_source() == "stored"
    manager.upsert_fal_job("fal-ai/model", REQUEST_ID, "IN_QUEUE")
    manager.upsert_fal_job("fal-ai/model", REQUEST_ID, "COMPLETED", {
        "images": [{"url": "https://v3.fal.media/output.png"}],
    })
    assert len(manager.get_fal_jobs()) == 1
    assert manager.get_fal_jobs()[0]["mediaUrls"][0].endswith("output.png")
    assert manager.get_fal_jobs()[0]["resultJson"]
    assert SettingsManager(path).get_fal_jobs()[0]["status"] == "COMPLETED"
    manager.set_fal_api_key("")
    assert manager.get_fal_api_key() == "env-key"


def test_optional_sdk_upload_is_injected_key_not_process_env(tmp_path, monkeypatch):
    image = tmp_path / "picture.png"
    image.write_bytes(b"PNG")
    observed = []

    class FakeSDKClient:
        def __init__(self, key):
            observed.append(key)

        def upload_file(self, path):
            assert path == str(image)
            return "https://v3.fal.media/files/picture.png"

    monkeypatch.setitem(sys.modules, "fal_client", types.SimpleNamespace(SyncClient=FakeSDKClient))
    c = FalClient("private-key")
    assert c.upload_file(image).endswith("picture.png")
    assert observed == ["private-key"]


def test_tool_registration_and_fresh_billable_approval(monkeypatch, tmp_path):
    import backend.tools_fal as mod

    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_fal_api_key("fake-key")
    calls = []

    class FakeFal:
        def __init__(self, key):
            assert key == "fake-key"

        def submit(self, model_id, arguments, *, options=None):
            calls.append((model_id, arguments))
            return {"request_id": REQUEST_ID, "status": "IN_QUEUE"}

    monkeypatch.setattr(mod, "FalClient", FakeFal)
    registry = ToolRegistry()
    register_fal_tools(registry, manager)
    assert set(s.name for s in registry.specs()) == {
        "fal.submit", "fal.run", "fal.status", "fal.result",
        "fal.cancel", "fal.wait", "fal.upload",
        "fal.search_models", "fal.model_details", "fal.model_pricing",
        "fal.estimate_cost", "fal.model_io", "fal.list_jobs",
    }
    assert registry.approval_for("fal.submit") == "always"
    assert registry.scopes_for("fal.upload") == frozenset({PROVIDER_CALL, FS_READ})
    approvals = []

    async def approve(call):
        approvals.append(call.name)
        return "session"

    ctx = RunContext(granted_scopes=frozenset({PROVIDER_CALL}),
                     request_approval=approve)
    call = ToolCall(id="id", name="fal.submit",
                    arguments={"model_id": "fal-ai/model", "arguments": {"prompt": "a"}})
    assert not asyncio.run(registry.invoke(call, ctx)).is_error
    assert not asyncio.run(registry.invoke(call, ctx)).is_error
    assert approvals == ["fal.submit", "fal.submit"]
    assert len(calls) == 2
    denied = asyncio.run(registry.invoke(
        ToolCall(id="id2", name="fal.upload", arguments={"path": "/tmp/secret"}),
        ctx,
    ))
    assert denied.is_error
    assert approvals == ["fal.submit", "fal.submit"]


def test_live_model_search_including_cursor_and_category():
    c, session = client(FakeResponse({
        "models": [
            {"endpoint_id": "fal-ai/flux/schnell", "metadata": {"category": "text-to-image"}}
        ],
        "next_cursor": "Mg==", "has_more": True,
    }))
    results = c.search_models("image", category="text-to-image", limit=5, cursor="MQ==")
    assert results["models"][0]["endpoint_id"] == "fal-ai/flux/schnell"
    assert session.calls[0][0] == "GET"
    assert session.calls[0][1].startswith("https://api.fal.ai/v1/models?")
    assert "q=image" in session.calls[0][1]
    assert "category=text-to-image" in session.calls[0][1]
    assert "cursor=MQ%3D%3D" in session.calls[0][1]
    assert session.calls[0][2]["headers"]["Authorization"].startswith("Key ")


def test_live_model_schema_lookup_and_pricing():
    c, session = client(
        FakeResponse({"models": [{
            "endpoint_id": "fal-ai/flux/schnell", "openapi": {"openapi": "3.0.0"}
        }]}),
        FakeResponse({"prices": [{
            "endpoint_id": "fal-ai/flux/schnell", "unit_price": 0.01, "unit": "megapixel"
        }]}),
        FakeResponse({"estimate_type": "historical_api_price", "total_cost": 0.03, "currency": "USD"}),
    )
    details = c.model_details("fal-ai/flux/schnell")
    assert details["openapi"]["openapi"] == "3.0.0"
    assert "expand=openapi-3.0" in session.calls[0][1]
    prices = c.model_pricing("fal-ai/flux/schnell")
    assert prices["prices"][0]["unit_price"] == 0.01
    assert session.calls[1][1].startswith("https://api.fal.ai/v1/models/pricing?")
    estimate = c.estimate_cost("fal-ai/flux/schnell", quantity=3)
    assert estimate["currency"] == "USD"
    assert session.calls[2][1] == "https://api.fal.ai/v1/models/pricing/estimate"
    assert session.calls[2][2]["json"] == {
        "estimate_type": "historical_api_price",
        "endpoints": {"fal-ai/flux/schnell": {"call_quantity": 3}},
    }


@pytest.mark.parametrize("bad", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"sort": "random"},
    {"status": "hacked"}, {"cursor": "x" * 513}, {"query": "q" * 201},
])
def test_catalog_rejects_malformed_parameters_without_network(bad):
    c, session = client()
    with pytest.raises(FalError):
        c.search_models(**bad)
    assert session.calls == []


def test_estimate_cost_rejects_invalid_quantities_before_http():
    c, session = client()
    with pytest.raises(FalError):
        c.estimate_cost("fal-ai/flux/schnell", quantity=-1)
    with pytest.raises(FalError):
        c.estimate_cost("fal-ai/flux/schnell", basis="not-a-basis")
    assert session.calls == []


def test_platform_api_rejects_unexpected_hostname_even_with_key():
    c, session = client()
    with pytest.raises(FalError, match="Unapproved"):
        c._call("POST", "https://evil.example", "v1/models",
                arguments={"data": "private"})
    assert session.calls == []


def test_tool_model_discovery_is_auto_and_only_requires_provider_scope():
    manager = SettingsManager.__new__(SettingsManager)
    registry = ToolRegistry()
    register_fal_tools(registry, manager)
    for operation in ("fal.search_models", "fal.model_details", "fal.model_pricing", "fal.estimate_cost"):
        assert registry.approval_for(operation) == "auto"
        assert registry.scopes_for(operation) == frozenset({PROVIDER_CALL})
