"""Exercise the same grants, picker, typed intent and saved graph that the SPA uses."""

import asyncio
import json
import shutil
import tomllib

import pytest

from backend.canvas import SceneDocument
from backend.domain.graph_runner import plan_graph_run
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import DEFAULT_PLUGINS_ROOT, discover_plugins
from backend.plugins import plugins_payload, register_plugin_tools, register_plugins
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.tools import ToolRegistry
from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import MAX_POINTS, VERSION, run_operation
from graphlink_settings_store import SettingsManager


@pytest.fixture
def session(tmp_path):
    # An isolated registry keeps grants and discovery out of the process-wide
    # bundled-plugin registry used by unrelated settings and picker tests.
    root = tmp_path / "plugins"
    for pack in PACKS:
        directory = "science_" + pack
        shutil.copytree(DEFAULT_PLUGINS_ROOT / directory, root / directory)
    registry = discover_plugins(root)
    settings = SettingsManager(tmp_path / "session.dat")
    document = SceneDocument()
    bus = SessionBus("science-tests")
    notice = NotificationState()
    bus.register_topic("notification", notice.payload)
    bus.register_topic("scene", document.scene_payload)
    register_plugins(bus, notice, document, settings, plugin_registry=registry)
    return document, bus, registry, settings


def dispatch(bus, name, *args):
    return asyncio.run(bus.dispatch_intent("app-plugins", name, list(args)))


def grant(bus, pack):
    dispatch(bus, "setPluginGrant", "science_" + pack, True)


def run(bus, pack, node_id, op, inputs, **kwargs):
    return dispatch(bus, "invokePluginIntent", "science_" + pack, "run",
                    {"node_id": node_id, "operation_id": op, "inputs": inputs, **kwargs})


def envelope(document, node_id):
    return json.loads(document._plugin_state_wire(document.nodes[node_id])["science"])


def test_manifest_to_canvas_and_run_for_every_example(session):
    document, bus, registry, settings = session
    assert not registry.node_kinds
    assert not registry.intents
    grants = plugins_payload(registry, settings)["grants"]
    assert {row["pluginId"] for row in grants} == {"science_" + pack for pack in PACKS}
    assert all(not row["granted"] for row in grants)
    for pack, (title, _) in PACKS.items():
        with (DEFAULT_PLUGINS_ROOT / ("science_" + pack) / "plugin.toml").open("rb") as f:
            manifest = tomllib.load(f)
        assert manifest["plugin"]["version"] == VERSION
        grant(bus, pack)
        node_id = dispatch(bus, "executePlugin", title, None, 110, 90)
        node = document.nodes[node_id]
        assert (node.x, node.y) == (110, 90)
        state = envelope(document, node_id)
        assert state["latest"] is None
        assert [o["id"] for o in state["operations"]] == [o.id for o in operations(pack)]
        for op in state["operations"]:
            assert run(bus, pack, node_id, op["id"], op["example"]) == {"ok": True}
            result = envelope(document, node_id)["latest"]
            assert result["operationId"] == op["id"] and result["provenance"]["packVersion"] == VERSION
            assert result["summary"] == node.content
            assert len(result["provenance"]["resultHash"]) == 64
    assert plan_graph_run(document, list(document.nodes)) == []
    assert register_plugin_tools(ToolRegistry(), registry, settings, document) == ()
    categories = plugins_payload(registry, settings)["categories"]
    assert len(categories) == 1 and categories[0]["name"] == "Scientific Workbench"
    assert len(categories[0]["plugins"]) == len(PACKS)


def test_csv_to_statistics_provenance_undo_grant_and_reopen(session):
    document, bus, registry, settings = session
    grant(bus, "core")
    grant(bus, "statistics")
    # Leave a gap so IDs are genuinely reminted (n2 -> n1) on reopen.
    document.add_note(0, 0)
    document.remove_nodes(list(document.nodes))
    source = dispatch(bus, "executePlugin", "Scientific Core")
    text = 'Station,"Temperature, °C"\n1,8\n2,10\n3,18'
    assert run(bus, "core", source, "numeric_csv", {"csv": text})["ok"]
    snapshot = envelope(document, source)
    ref = {"nodeId": source, "sourceKey": snapshot["nodeKey"],
           "resultHash": snapshot["latest"]["provenance"]["resultHash"], "selector": ["column", 1]}
    node_id = dispatch(bus, "executePlugin", "Statistics", source)
    def calculate(inputs, **kwargs):
        return run(bus, "statistics", node_id, "describe", {"observations": inputs}, **kwargs)
    assert calculate([8, 10, 18], sources={"observations": ref})["ok"]
    initial = envelope(document, node_id)["latest"]
    assert initial["values"]["mean"] == 12
    assert initial["provenance"]["sources"]["observations"]["sourceKey"] == snapshot["nodeKey"]
    commands = len(document.command_log)
    assert calculate([8, 10, 18], sources={"observations": ref})["ok"]
    assert not calculate([8, 10, 19], sources={"observations": ref})["ok"]
    assert not calculate([8])["ok"]
    assert not calculate([8, 10, 18], sources={"observations": {**ref, "resultHash": "old"}})["ok"]
    assert not run(bus, "core", node_id, "numeric_csv", {"csv": text})["ok"]
    assert envelope(document, node_id)["latest"] == initial
    assert len(document.command_log) == commands
    document.undo()
    assert envelope(document, node_id)["latest"] is None
    document.redo()
    assert envelope(document, node_id)["latest"] == initial
    saved = build_chat_data(document, plugin_registry=registry, settings_manager=settings)
    restore_chat_into_document(document, {"data": saved}, [], [], plugin_registry=registry, settings_manager=settings)
    source, node_id = document.nodes
    assert source != ref["nodeId"]
    assert envelope(document, node_id)["latest"] == initial
    assert calculate([8, 10, 18], sources={"observations": ref})["ok"]
    assert envelope(document, node_id)["latest"] == initial
    dispatch(bus, "setPluginGrant", "science_core", False)
    assert not calculate([8, 10, 18], sources={"observations": ref})["ok"]
    dispatch(bus, "setPluginGrant", "science_statistics", False)
    assert calculate([8, 10, 18]) is None


@pytest.mark.parametrize("op,inputs", [
    ("convert_unit", {"value": -1, "from_unit": "K", "to_unit": "°C"}),
    ("convert_unit", {"value": 1, "from_unit": "kg", "to_unit": "m"}),
    ("convert_unit", {"value": True, "from_unit": "s", "to_unit": "min"}),
    ("convert_unit", {"value": float("inf"), "from_unit": "s", "to_unit": "min"}),
    ("numeric_csv", {"csv": "a,a\n1,2"}),
    ("numeric_csv", {"csv": "a,b\n1"}),
    ("numeric_csv", {"csv": "a,b\nNaN,1"}),
    ("numeric_csv", {"csv": 'a\n"unterminated'}),
    ("numeric_csv", {"csv": "a\n" + "1\n" * (MAX_POINTS + 1)}),
    ("plot_xy", {"x": [0, 1], "y": [1], "x_label": "X", "y_label": "Y"}),
    ("plot_xy", {"x": [0], "y": [1], "x_label": "x", "y_label": "y", "extra": 1}),
])
def test_core_rejects_invalid_measurements(op, inputs):
    operation = next(o for o in operations("core") if o.id == op)
    with pytest.raises(ValueError):
        run_operation("core", operation, inputs)


def test_unit_conversion_and_data_bounds():
    convert = next(o for o in operations("core") if o.id == "convert_unit")
    assert run_operation("core", convert, convert.example)["values"]["value (K)"] == 298.15
    solve = next(o for o in operations("mathematics") if o.id == "linear_system")
    for invalid_matrix in ([[1], [1, 2]], [[True]], [[1] * 33], [1]):
        with pytest.raises(ValueError):
            run_operation("mathematics", solve, {"matrix": invalid_matrix, "rhs": [1]})


def test_damaged_receipt_is_visible_and_preserved(session):
    document, bus, _, _ = session
    grant(bus, "core")
    node = dispatch(bus, "executePlugin", "Scientific Core")
    assert run(bus, "core", node, "convert_unit", operations("core")[1].example)["ok"]
    result = document.nodes[node].state.data["latest"]
    result["values"]["value (K)"] = 9001
    assert "damaged" in envelope(document, node)["error"]
    assert not run(bus, "core", node, "convert_unit", operations("core")[1].example)["ok"]
    assert document.nodes[node].state.data["latest"] is result
