"""Agent discovery, projection and cross-model handoffs, without billable calls."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.fal_integration import FalClient, FalError
from backend.fal_json import page_value, select_pointer
from backend.providers.base import ToolCall
from backend.tests.test_fal_integration import FakeResponse, FakeSession, REQUEST_ID
from backend.tools import FS_READ, PROVIDER_CALL, RunContext, ToolRegistry
from backend.tools_fal import register_fal_tools

SOURCE = "fal-ai/source/transcribe"
TARGET = "fal-ai/target/synthesize"
SECOND_ID = "098c5f9a-db7b-44ab-aad4-23abf9e1c8f0"


def setup_tools(monkeypatch, responses, *, approve=True, key="demo", manager=None):
    transport = FakeSession(*[FakeResponse(r) for r in responses])
    client = FalClient(key, session=transport)
    monkeypatch.setattr("backend.tools_fal.FalClient", lambda _key: client)
    manager = manager or SimpleNamespace(get_fal_api_key=lambda: key, upsert_fal_job=lambda *args: None)
    registry = ToolRegistry()
    register_fal_tools(registry, manager)
    decisions = []

    async def approval(call):
        decisions.append(call.name)
        return approve

    ctx = RunContext(frozenset({PROVIDER_CALL, FS_READ}), approval, session_grants=set())

    def invoke(name, **args):
        return asyncio.run(registry.invoke(ToolCall(id="t", name=name, arguments=args), ctx))

    return invoke, decisions, transport, ctx


def test_cross_model_handoff_uses_full_original_value_and_approves_each_step(monkeypatch):
    transcript = "words " * 10000
    source_result = {"transcript": transcript, "assets": [{"file_url": "https://fal.media/sample.wav"}]}
    invoke, approvals, session, _ = setup_tools(monkeypatch, [
        source_result, source_result, {"request_id": SECOND_ID},
        source_result, {"request_id": SECOND_ID},
    ])
    inspected = json.loads(invoke("fal.result", model_id=SOURCE, request_id=REQUEST_ID).content)
    assert any(x["key"] == "transcript" and "inspect" in x for x in inspected["entries"])
    asset, = inspected["assets"]
    assert asset["path"] == "/assets/0/file_url"
    assert select_pointer(source_result, asset["path"]) == asset["url"]
    bindings = [
        {"argument": name, "model_id": SOURCE, "request_id": REQUEST_ID, "pointer": pointer}
        for name, pointer in [("text", "/transcript"), ("reference_url", asset["path"])]
    ]
    for _ in range(2):
        outcome = invoke("fal.submit", model_id=TARGET, arguments={"speed": 1.1}, input_bindings=bindings)
        assert json.loads(outcome.content)["request_id"] == SECOND_ID
    assert approvals == ["fal.submit", "fal.submit"]
    assert [m for m, _, _ in session.calls] == ["GET", "GET", "POST", "GET", "POST"]
    assert session.calls[-1][2]["json"] == {"speed": 1.1, "text": transcript, "reference_url": asset["url"]}


@pytest.mark.parametrize("change", [
    {"pointer": "/missing"}, {"request_id": "bad"}, {"argument": "language"}, {"pointer": None},
])
def test_binding_errors_never_submit(monkeypatch, change):
    invoke, _, session, _ = setup_tools(monkeypatch, [{"text": "hello"}])
    binding = {"argument": "text", "model_id": SOURCE, "request_id": REQUEST_ID, "pointer": "/text", **change}
    assert invoke("fal.run", model_id=TARGET, arguments={"language": "en"}, input_bindings=[binding]).is_error
    assert all(m == "GET" for m, _, _ in session.calls)


def test_denial_precedes_input_binding_download(monkeypatch):
    invoke, approvals, session, _ = setup_tools(monkeypatch, [], approve=False)
    denied = invoke("fal.submit", model_id=TARGET, arguments={}, input_bindings=[
        {"argument": "text", "model_id": SOURCE, "request_id": REQUEST_ID, "pointer": "/text"}])
    assert denied.is_error and approvals == ["fal.submit"]
    assert not session.calls


def test_anonymous_catalog_and_model_io_and_authenticated_operations(monkeypatch):
    model = {"endpoint_id": SOURCE, "openapi": {"paths": {
        f"/{SOURCE}": {"post": {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}}},
        "/fal-ai/source/requests/{request_id}": {"get": {"responses": {"200": {"content": {
            "application/json": {"schema": {"type": "string", "enum": ["yes", "no"]}}}}}}},
    }}}
    invoke, approvals, session, _ = setup_tools(monkeypatch, [{"models": [model]}, {"models": [model]}], key="")
    assert not invoke("fal.search_models", query="transcript").is_error
    assert json.loads(invoke("fal.model_io", model_id=SOURCE, pointer="/output/enum").content)["data"] == ["yes", "no"]
    assert invoke("fal.model_pricing", model_id=SOURCE).is_error
    assert approvals == []
    assert invoke("fal.submit", model_id=SOURCE, arguments={}).is_error
    assert len(session.calls) == 2 and all("Authorization" not in opts["headers"] for _, _, opts in session.calls)


def test_result_selection_with_escape_and_pagination(monkeypatch):
    original = {"a/b": [{"~": "word" * 4000}]}
    invoke, _, _, _ = setup_tools(monkeypatch, [original, original])
    args = {"model_id": SOURCE, "request_id": REQUEST_ID, "pointer": "/a~1b/0/~0"}
    first = json.loads(invoke("fal.result", **args).content)
    second = json.loads(invoke("fal.result", **args, offset=first["next_offset"]).content)
    assert first["data"] + second["data"] == original["a/b"][0]["~"][:second["next_offset"]]
    assert first["total"] == 16000
    assert select_pointer({"": 42}, "/") == 42
    for bad in ("/01", "/-1", "/~2", "0", "/999999999999999999999"):
        with pytest.raises(FalError):
            select_pointer([1, 2], bad)


def test_pages_retain_nulls_and_offsets():
    values = [None, "x"*14500, "y"*7500, *range(200)]
    page = page_value(values)
    assert page["entries"][0] == {"key": 0, "value": None}
    assert page["entries"][1]["key"] == 1 and "inspect" in page["entries"][1]
    assert page["next_offset"] == 100
    assert page_value(values, offset=100)["entries"][0] == {"key": 100, "value": 97}
    for offset in (True, -1, 204):
        with pytest.raises(FalError):
            page_value(values, offset=offset)


def test_history_failure_does_not_lose_paid_request_id(monkeypatch):
    def fail_save(*args):
        raise OSError("cannot save")
    manager = SimpleNamespace(get_fal_api_key=lambda: "test", upsert_fal_job=fail_save)
    run, _, _, _ = setup_tools(monkeypatch, [{"request_id": REQUEST_ID}], manager=manager)
    result = run("fal.submit", model_id=SOURCE, arguments={})
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["request_id"] == REQUEST_ID and payload["history_warning"]


def test_local_recovery_filters_model_and_omits_inputs_and_outputs(monkeypatch):
    jobs = [{"modelId": SOURCE, "requestId": REQUEST_ID, "status": "FAILED", "resultJson": "private"},
            {"modelId": TARGET, "requestId": SECOND_ID, "status": "IN_QUEUE"}]
    manager = SimpleNamespace(get_fal_jobs=lambda: jobs)
    invoke, approvals, session, _ = setup_tools(monkeypatch, [], manager=manager)
    result = invoke("fal.list_jobs", model_id=SOURCE)
    assert not result.is_error
    job, = json.loads(result.content)["data"]
    assert job["requestId"] == REQUEST_ID and "resultJson" not in job
    assert not approvals and not session.calls


def test_workspace_upload_resolves_relative_path_and_rejects_escape(monkeypatch, tmp_path):
    paths = []
    monkeypatch.setattr(FalClient, "upload_file", lambda self, path: paths.append(path) or "https://fal.media/x")
    invoke, _, _, ctx = setup_tools(monkeypatch, [])
    ctx.harness_workspace_dir = tmp_path
    assert not invoke("fal.upload", path="voice.wav").is_error
    assert invoke("fal.upload", path="../other.wav").is_error
    assert paths == [str(tmp_path / "voice.wav")]


@pytest.mark.parametrize("operation", ["fal.status", "fal.wait"])
def test_failed_job_is_recorded_as_failed(monkeypatch, operation):
    updates = []
    manager = SimpleNamespace(get_fal_api_key=lambda: "test", upsert_fal_job=lambda *args: updates.append(args))
    run, _, session, _ = setup_tools(monkeypatch, [{"status": "COMPLETED", "error": "model failed"}], manager=manager)
    outcome = run(operation, model_id=SOURCE, request_id=REQUEST_ID)
    assert outcome.is_error == (operation == "fal.wait")
    assert updates == [(SOURCE, REQUEST_ID, "FAILED")]
    assert len(session.calls) == 1
