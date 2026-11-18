"""Model-driven distribution/matrix chart node creation and failure paths."""

import asyncio

import pytest

from backend.api.intents_chart import register_chart_intents
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState


class FakeChartAgent:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def start_chart_generation(self, **kwargs):
        self.calls.append(kwargs)
        await kwargs["on_success"](self.response)


def _setup(response):
    bus = SessionBus("boxplot-heatmap-intent")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    agent = FakeChartAgent(response)
    register_chart_intents(bus, document, notifications, agent)
    parent = document.add_chat_node(0, 0, "Source measurements: control 1, 2, 3; response matrix 1, -2.",
                                    is_user=True)
    return bus, document, notifications, agent, parent.id


@pytest.mark.parametrize("chart_type,model_data,field", [
    ("boxplot", {"type": "boxplot", "groups": [
        {"label": "Control", "values": [1, 2, 3]},
    ]}, "groups"),
    ("heatmap", {"type": "heatmap", "rowLabels": ["A"], "columnLabels": ["B"],
                 "matrix": [[-2]]}, "matrix"),
])
def test_generated_chart_node_stores_new_type_without_out_of_scope_wire_change(chart_type, model_data, field):
    async def run():
        bus, document, _, agent, root = _setup(model_data)
        node_id = await bus.dispatch_intent("scene", "generateChart", [root, chart_type])
        state = document.nodes[node_id].state
        assert state.chart_type == chart_type
        assert "type" not in state.chart_data
        assert state.chart_data[field]
        assert state.chart_error == ""
        assert len(agent.calls) == 1
        assert agent.calls[0]["chart_type"] == chart_type
        assert "Source measurements" in agent.calls[0]["source_text"]

    asyncio.run(run())


@pytest.mark.parametrize("chart_type,invalid,placeholder", [
    ("boxplot", {"type": "boxplot", "groups": []}, "groups"),
    ("heatmap", {"type": "heatmap", "rowLabels": ["A"],
                 "columnLabels": ["B", "C"], "matrix": [[1]]}, "matrix"),
])
def test_failed_generated_data_creates_revalidatable_placeholder(chart_type, invalid, placeholder):
    async def run():
        bus, document, _, _, root = _setup(invalid)
        node_id = await bus.dispatch_intent("scene", "generateChart", [root, chart_type])
        state = document.nodes[node_id].state
        assert state.chart_type == chart_type
        assert state.chart_error.startswith("The generated chart data could not be validated:")
        assert "type" not in state.chart_data
        assert state.chart_data[placeholder]

    asyncio.run(run())


def test_new_types_reject_scientific_options_without_dispatching():
    async def run():
        bus, _, notification, agent, root = _setup({})
        result = await bus.dispatch_intent("scene", "generateChart", [
            root, "heatmap", {"xScale": "log10"},
        ])
        assert result is None
        assert "require line or scatter" in notification.payload()["message"]
        assert agent.calls == []

    asyncio.run(run())
