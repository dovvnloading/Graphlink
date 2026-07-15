"""Regression coverage for chart data, rendering, and scene lifecycle behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QPointF

from graphlink_agents_tools import ChartDataAgent
from graphlink_canvas.graphlink_canvas_chart_item import ChartItem
from graphlink_chart_data import ChartDataError, canonicalize_chart_data
from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.serializers import SceneSerializer


def _bar_data(**overrides):
    data = {
        "type": "bar",
        "title": "Sales",
        "labels": ["A", "B", "C"],
        "values": [1, 2, 3],
    }
    data.update(overrides)
    return data


def _scene():
    return ChatScene(MagicMock())


def test_canonical_chart_data_rejects_wrong_containers_and_non_finite_numbers():
    with pytest.raises(ChartDataError, match="both be lists"):
        canonicalize_chart_data(_bar_data(values={"A": 1}), "bar")
    with pytest.raises(ChartDataError, match="finite"):
        canonicalize_chart_data(_bar_data(values=[1, float("nan"), 3]), "bar")


def test_canonical_sankey_data_aggregates_duplicates_and_rejects_cycles():
    canonical = canonicalize_chart_data(
        {
            "type": "sankey",
            "flows": [
                {"source": "A", "target": "B", "value": 1},
                {"source": "A", "target": "B", "value": 2},
            ],
        }
    )
    assert canonical["flows"] == [{"source": "A", "target": "B", "value": 3.0}]

    with pytest.raises(ChartDataError, match="cannot contain cycles"):
        canonicalize_chart_data(
            {
                "type": "sankey",
                "flows": [
                    {"source": "A", "target": "B", "value": 1},
                    {"source": "B", "target": "A", "value": 1},
                ],
            }
        )


def test_agent_validator_uses_canonical_contract():
    agent = ChartDataAgent()
    valid, error = agent.validate_chart_data(_bar_data(values="123"), "bar")
    assert not valid
    assert "both be lists" in error

    valid, error = agent.validate_chart_data(
        {"type": "sankey", "flows": [{"source": "A", "target": "B", "value": float("nan")}]},
        "sankey",
    )
    assert not valid
    assert "finite" in error


def test_chart_scene_adds_provenance_connection_and_cascades_delete():
    scene = _scene()
    parent = scene.add_chat_node("source", preferred_pos=QPointF(0, 0))
    chart = scene.add_chart(_bar_data(), QPointF(450, 0), parent_content_node=parent)

    assert chart.parent_content_node is parent
    assert chart.source_node is parent
    assert len(scene.chart_connections) == 1
    assert scene.chart_connections[0].start_node is parent
    assert scene.chart_connections[0].end_node is chart

    scene.delete_chat_node(parent)
    assert chart not in scene.chart_nodes
    assert not scene.chart_connections


def test_chart_render_is_bounded_for_dense_labels_and_invalid_data_gets_placeholder():
    dense = _bar_data(
        labels=[f"Category {index} with a long label" for index in range(50)],
        values=list(range(1, 51)),
    )
    chart = ChartItem(dense, QPointF(0, 0))
    assert not chart.chart_image.isNull()

    invalid = ChartItem(_bar_data(values="12"), QPointF(0, 0))
    assert invalid.data_error
    assert not invalid.chart_image.isNull()


def test_chart_resize_uses_resize_start_ratio_and_has_upper_bound():
    chart = ChartItem(_bar_data(), QPointF(0, 0))
    chart.resizing = True
    chart.resize_start_aspect_ratio = 2.0
    chart.set_chart_size(10000, 10000, preserve_aspect=True, rerender=False)
    chart.resizing = False

    assert chart.width <= chart.MAX_WIDTH
    assert chart.height <= chart.MAX_HEIGHT
    assert chart.width / chart.height == pytest.approx(2.0, rel=0.01)


def test_chart_serialization_persists_stable_identity_and_parent_reference():
    window = MagicMock()
    scene = ChatScene(window)
    window.chat_view.scene.return_value = scene
    parent = scene.add_chat_node("source", preferred_pos=QPointF(0, 0))
    chart = scene.add_chart(_bar_data(), QPointF(450, 0), parent_content_node=parent)

    serializer = SceneSerializer(window)
    payload = serializer.serialize_chart(chart, [parent])

    assert payload["id"] == chart.persistent_id
    assert payload["parent_node_id"] == parent.persistent_id
    assert payload["data"]["values"] == [1.0, 2.0, 3.0]

    restored_window = MagicMock()
    restored_scene = ChatScene(restored_window)
    restored_window.chat_view.scene.return_value = restored_scene
    deserializer = SceneDeserializer(restored_window)
    restored = deserializer.deserialize_chart(payload, restored_scene, {0: parent})
    assert restored.persistent_id == chart.persistent_id
    assert restored.parent_content_node is None or restored.parent_content_node is parent

