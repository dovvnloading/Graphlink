"""Workspace-agent Scientific Workbench tools: grants, discovery and verified output."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from api_provider import RequestCancelledError
from backend.domain.graph import SceneDocument
from backend.domain.node_states import PluginStateSnapshot
from backend.harness.loop import HarnessRunContext, _approval_summary
from backend.harness.tools_scientific import register_workspace_tools
from backend.plugin_sdk import HostContext
from backend.providers.base import CancelToken, ToolCall
from backend.tools import CODE_EXECUTE, FS_READ, GRAPH_MUTATE, GRAPH_READ, ToolRegistry
from graphlink_plugins.scientific.catalog import operations
from graphlink_plugins.scientific.core import calculate_result, readable_result
from graphlink_plugins.scientific.execution import ExecutionFailure
from graphlink_plugins.scientific.workbench import register_pack


class Settings:
    def __init__(self):
        self.grants: dict[str, bool] = {}

    def get_plugin_grants(self):
        return dict(self.grants)


@pytest.fixture
def setup():
    document = SceneDocument()
    agent = document.add_harness_node(50, 70, "Run a scientific calculation")
    agent.state.harness_run_id = "harness-42"
    agent.pending_request_id = "harness-42"
    settings = Settings()
    registry = ToolRegistry()
    register_workspace_tools(registry, None, document)

    approvals: list[str] = []

    async def approve(call):
        approvals.append(call.name)
        return True

    ctx = HarnessRunContext(
        granted_scopes=frozenset({GRAPH_READ, GRAPH_MUTATE, CODE_EXECUTE, FS_READ}),
        request_approval=approve,
        settings_manager=settings,
        harness_node_id=agent.id,
        run_id="harness-42",
        cancel=CancelToken(threading.Event()),
    )

    def invoke(name, **kwargs):
        return asyncio.run(registry.invoke(
            ToolCall(id="call-1", name=name, arguments=kwargs), ctx,
        ))

    def activate(pack):
        # Use the REAL Workbench serializer, not a hand-built UI envelope.
        host = HostContext(f"science_{pack}")
        register_pack(host, pack)
        kind = f"science_{pack}.workbench"
        document.plugin_node_serializers[kind] = host._node_kinds[kind].serialize
        settings.grants[f"science_{pack}"] = True

    return document, agent, settings, registry, ctx, approvals, activate, invoke


def test_catalog_is_file_discovered_and_approval_scoped(setup):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    assert registry.scopes_for("science.search") == frozenset({FS_READ, GRAPH_READ})
    assert registry.approval_for("science.search") == "auto"
    assert registry.parallel_safe_for("science.search")
    assert "uncertainty_inputs" in next(
        spec for spec in registry.specs() if spec.name == "science.run"
    ).input_schema["properties"]
    assert registry.scopes_for("science.run") == frozenset({FS_READ, CODE_EXECUTE, GRAPH_MUTATE})
    assert registry.approval_for("science.run") == "once"

    assert json.loads(invoke("science.search").content)["packs"] == []
    activate("mathematics")
    activate("core")
    catalog = json.loads(invoke("science.search").content)
    assert {p["pack_id"] for p in catalog["packs"]} == {"core", "mathematics"}
    assert not catalog["operations"]
    assert all(p["ready"] for p in catalog["packs"])
    assert not approvals  # Discovery is read-only and never prompts.

    result = json.loads(invoke("science.search", query="trapezoid").content)
    assert result["total"] == 1
    assert result["operations"][0]["id"] == "trapezoid"
    assert result["operations"][0]["example"] == operations("mathematics")[1].example
    assert result["operations"][0]["fields"][0]["key"] == "x"

    page = json.loads(invoke("science.search", pack_id="mathematics", limit=1).content)
    assert page["total"] == len(operations("mathematics"))
    assert len(page["operations"]) == 1
    assert page["next_offset"] == 1
    second = json.loads(invoke("science.search", pack_id="mathematics", limit=1, offset=1).content)
    assert second["operations"][0]["id"] != page["operations"][0]["id"]


def test_disabled_packs_are_never_imported_or_run(setup, monkeypatch):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    import backend.harness.tools_scientific as tools

    def unexpected(_pack):
        raise AssertionError("ungranted operation module was imported")

    monkeypatch.setattr(tools, "operations", unexpected)
    assert json.loads(invoke("science.search", query="statistics").content)["operations"] == []
    result = invoke("science.run", pack_id="mathematics", operation_id="trapezoid", inputs={})
    assert result.is_error
    assert "not enabled" in result.content
    assert set(document.nodes) == {agent.id}

    # Granted but not activated: do not create an opaque generic plugin node.
    settings.grants["science_mathematics"] = True
    result = invoke("science.run", pack_id="mathematics", operation_id="trapezoid", inputs={})
    assert result.is_error and "not activated" in result.content
    assert set(document.nodes) == {agent.id}


def test_real_worker_creates_linked_verified_workbench_and_returns_receipt(setup):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    activate("mathematics")
    op = next(o for o in operations("mathematics") if o.id == "trapezoid")
    response = invoke("science.run", pack_id="mathematics", operation_id=op.id, inputs=op.example)

    assert not response.is_error, response.content
    body = json.loads(response.content)
    assert body["ok"] and body["verified"]
    assert body["values"]["integral"] == 6.0
    assert body["summary"]
    assert body["receipt"]["operationId"] == op.id
    assert len(body["receipt"]["resultHash"]) == 64
    assert len(body["receipt"]["receiptHash"]) == 64

    node = document.nodes[body["node_id"]]
    assert node.kind == "science_mathematics.workbench"
    assert type(node.state) is PluginStateSnapshot  # manual Workbench inline-table parity
    assert node.content == body["summary"]
    assert node.x > agent.x
    assert any(edge.source == agent.id and edge.target == node.id
               for edge in document.edges.values())
    assert readable_result(node.state.data, "mathematics")
    assert node.state.data["latest"]["values"] == body["values"]
    assert node.state.data["latest"]["provenance"] == body["receipt"]
    state_wire = json.loads(document._plugin_state_wire(node)["science"])
    assert state_wire["latest"]["operationId"] == op.id
    assert state_wire["latest"]["provenance"]["receiptHash"] == body["receipt"]["receiptHash"]
    assert approvals == ["science.run"]
    assert document.command_log[-1].run_id == "harness-42"
    assert document.command_log[-1].provenance == "agent"

    agent.pending_request_id = None
    assert document.undo_run("harness-42") == 1
    assert set(document.nodes) == {agent.id}
    assert not document.edges
    document.redo()
    assert body["node_id"] in document.nodes
    assert readable_result(document.nodes[body["node_id"]].state.data, "mathematics")



def test_supplied_uncertainty_runs_gum_and_returns_verified_sidecar(setup):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    activate("core")
    response = invoke(
        "science.run", pack_id="core", operation_id="convert_unit",
        inputs={"value": 5, "from_unit": "m", "to_unit": "cm"},
        uncertainty_inputs={"value": 0.2}, uncertainty_method="gum",
    )
    assert not response.is_error, response.content
    body = json.loads(response.content)
    assert body["verified"] and body["uncertainty"]["method"] == "gum"
    inputs = body["uncertainty"]["inputs"]
    assert inputs["value"]["standardUncertainty"] == pytest.approx(0.2)
    assert inputs["value"]["unit"] == "m"
    measured = body["uncertainty"]["outputs"]["value (cm)"]
    assert measured["standardUncertainty"] == pytest.approx(20, rel=1e-6)
    assert measured["value"] == pytest.approx(500)
    node = document.nodes[body["node_id"]]
    assert node.state.data["latest"]["uncertainty"] == body["uncertainty"]
    assert readable_result(node.state.data, "core")


@pytest.mark.parametrize("extra, error", [
    ({"uncertainty_method": "gum"}, "requires uncertainty_inputs"),
    ({"uncertainty_method": "monte_carlo"}, "requires uncertainty_inputs"),
    ({"uncertainty_inputs": {}}, "1–16"),
    ({"uncertainty_inputs": {"value": -0.1}}, "nonnegative"),
    ({"uncertainty_inputs": {"from_unit": 0.1}}, "continuous numeric"),
    ({"uncertainty_inputs": {"value": True}}, "finite number"),
])
def test_invalid_or_absent_uncertainties_never_create_nodes(setup, extra, error):
    document, agent, _settings, _registry, _ctx, _approvals, activate, invoke = setup
    activate("core")
    response = invoke(
        "science.run", pack_id="core", operation_id="convert_unit",
        inputs={"value": 5, "from_unit": "m", "to_unit": "cm"}, **extra,
    )
    assert response.is_error and error in response.content
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_worker_cannot_silently_ignore_requested_propagation_method(setup, monkeypatch):
    document, agent, _settings, _registry, _ctx, _approvals, activate, invoke = setup
    activate("core")
    import backend.harness.tools_scientific as tools

    async def ignoring_method(pack, operation, inputs, sources, **kwargs):
        assert kwargs["uncertainty_method"] == "monte_carlo"
        assert kwargs["uncertainty_inputs"]["value"]["standardUncertainty"] == 0.2
        return calculate_result(
            pack, operation, inputs, sources,
            input_units=kwargs["input_units"],
            uncertainty_inputs=kwargs["uncertainty_inputs"],
            uncertainty_method="gum",
        )

    monkeypatch.setattr(tools, "execute_in_process", ignoring_method)
    response = invoke(
        "science.run", pack_id="core", operation_id="convert_unit",
        inputs={"value": 5, "from_unit": "m", "to_unit": "cm"},
        uncertainty_inputs={"value": 0.2}, uncertainty_method="monte_carlo",
    )
    assert response.is_error and "unverified receipt" in response.content
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_invalid_inputs_and_worker_errors_leave_no_artifacts(setup, monkeypatch):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    activate("mathematics")
    missing = invoke("science.run", pack_id="mathematics", operation_id="trapezoid", inputs={})
    assert missing.is_error
    assert set(document.nodes) == {agent.id}
    assert not document.command_log

    import backend.harness.tools_scientific as tools

    async def timeout(*_args, **_kwargs):
        raise ExecutionFailure("timeout", "Scientific calculation timed out", 501)

    monkeypatch.setattr(tools, "execute_in_process", timeout)
    op = operations("mathematics")[1]
    failed = invoke("science.run", pack_id="mathematics", operation_id=op.id, inputs=op.example)
    assert failed.is_error and json.loads(failed.content)["errorType"] == "timeout"
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_forged_result_is_rejected_and_grant_revoke_discards_result(setup, monkeypatch):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    activate("core")
    op = operations("core")[1]
    import backend.harness.tools_scientific as tools

    async def forged(*_args, **_kwargs):
        return {"operationId": op.id, "inputs": {}, "values": {"answer": 999},
                "summary": "This is not receipt-verified.", "provenance": {"sources": {}}}

    monkeypatch.setattr(tools, "execute_in_process", forged)
    rejected = invoke("science.run", pack_id="core", operation_id=op.id, inputs=op.example)
    assert rejected.is_error and "unverified receipt" in rejected.content
    assert set(document.nodes) == {agent.id}

    async def revoke(pack, operation, inputs, sources, **kwargs):
        answer = calculate_result(pack, operation, inputs, sources, input_units=kwargs["input_units"])
        settings.grants["science_core"] = False
        return answer

    monkeypatch.setattr(tools, "execute_in_process", revoke)
    rejected = invoke("science.run", pack_id="core", operation_id=op.id, inputs=op.example)
    assert rejected.is_error and "grant changed" in rejected.content
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_agent_or_workspace_change_during_worker_discard_result(setup, monkeypatch):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    activate("core")
    op = operations("core")[1]
    import backend.harness.tools_scientific as tools

    async def switch_workspace(pack, operation, inputs, sources, **kwargs):
        result = calculate_result(pack, operation, inputs, sources, input_units=kwargs["input_units"])
        document.current_workspace_id = "different-workspace"
        return result

    monkeypatch.setattr(tools, "execute_in_process", switch_workspace)
    rejected = invoke("science.run", pack_id="core", operation_id=op.id, inputs=op.example)
    assert rejected.is_error and "workspace" in rejected.content
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_stop_interrupts_worker_without_creating_node(setup, monkeypatch):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    activate("mathematics")
    import backend.harness.tools_scientific as tools

    async def block_until_cancel(*_args, cancel_event, **_kwargs):
        await asyncio.wait_for(cancel_event.wait(), 1)
        raise ExecutionFailure("cancelled", "Scientific calculation cancelled", 10)

    monkeypatch.setattr(tools, "execute_in_process", block_until_cancel)
    op = operations("mathematics")[1]

    async def drive():
        task = asyncio.create_task(registry.invoke(
            ToolCall(id="run", name="science.run",
                     arguments={"pack_id": "mathematics", "operation_id": op.id,
                                "inputs": op.example}), ctx,
        ))
        await asyncio.sleep(0.1)
        ctx.cancel.event.set()
        with pytest.raises(RequestCancelledError):
            await task

    asyncio.run(drive())
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_workspace_science_tools_do_not_leak_into_builder_and_approvals_are_complete(setup):
    document, agent, settings, registry, ctx, approvals, activate, invoke = setup
    from backend.builder import BUILDER_GRANTED_SCOPES

    assert not registry.scopes_for("science.search") <= BUILDER_GRANTED_SCOPES
    assert not registry.scopes_for("science.run") <= BUILDER_GRANTED_SCOPES
    assert registry.scopes_for("science.search") <= ctx.granted_scopes
    assert registry.scopes_for("science.run") <= ctx.granted_scopes

    # Scientific inputs can be much larger than the default 400-character
    # approval-summary cap. The person approving must see all of them.
    last = "end-of-approved-input-array"
    call = ToolCall(
        id="full-preview", name="science.run",
        arguments={"pack_id": "core", "operation_id": "plot_xy",
                   "inputs": {"x": list(range(200)), "y": list(range(200)),
                              "x_label": "time", "y_label": last}},
    )
    preview = _approval_summary(call)
    assert preview.startswith("science.run ")
    assert last in preview and len(preview) > 400
    assert json.loads(preview.removeprefix("science.run "))["inputs"]["x"][-1] == 199
