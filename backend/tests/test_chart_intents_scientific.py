"""Model-driven scatter request options and failure-state regression coverage."""

import asyncio

from backend.api.intents_chart import register_chart_intents
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState


class FakeChartAgent:
    def __init__(self, response=None):
        self.calls = []
        self.response = response

    async def start_chart_generation(self, **kwargs):
        self.calls.append(kwargs)
        if self.response is not None:
            await kwargs["on_success"](self.response)


def _setup(response=None):
    bus = SessionBus("scientific-chart-intent")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    agent = FakeChartAgent(response)
    register_chart_intents(bus, document, notifications, agent)
    root = document.add_chat_node(0, 0, "Concentrations 1, 10; response 2, 3.", is_user=True)
    return bus, document, notifications, agent, root.id


def test_model_driven_scatter_requests_advanced_options_and_creates_a_chart():
    response = {
        "type": "scatter", "xValues": [1, 10],
        "series": [{"name": "Control", "values": [2, 3], "yError": [0.1, [0.2, 0.3]]}],
        "xScale": "log10", "xUnit": "mg/L", "yUnit": "mV",
    }

    async def run():
        bus, document, _, agent, root = _setup(response)
        node_id = await bus.dispatch_intent("scene", "generateChart", [
            root, "scatter", {
                "multipleSeries": True, "errorBars": True, "xScale": "log10",
                "xUnit": "mg/L", "yUnit": "mV",
            },
        ])
        assert node_id in document.nodes
        state = document.nodes[node_id].state
        assert state.chart_type == "scatter"
        assert "type" not in state.chart_data  # v1 generated scene validator
        assert state.chart_data["series"][0]["yError"] == [[0.1, 0.1], [0.2, 0.3]]
        assert len(agent.calls) == 1
        prompt = agent.calls[0]["source_text"]
        assert "multiple named series" in prompt
        assert "per-point error magnitudes" in prompt
        assert "xScale=log10" in prompt
        assert "xUnit='mg/L'" in prompt
        assert "yUnit='mV'" in prompt
        assert "Concentrations 1, 10" in prompt

    asyncio.run(run())


def test_chart_options_are_strict_and_do_not_dispatch_invalid_requests():
    async def run():
        bus, _, notifications, agent, root = _setup()
        for options, message in [
            ({"xScale": "ln"}, "xScale must be linear or log10"),
            ({"errorBars": "yes"}, "errorBars must be a boolean"),
            ({"newFeature": True}, "Unsupported chartOptions"),
            ({"xUnit": ""}, "xUnit must be a nonempty unit"),
        ]:
            result = await bus.dispatch_intent("scene", "generateChart", [root, "scatter", options])
            assert result is None
            assert notifications.payload()["message"] == message or message in notifications.payload()["message"]
        assert agent.calls == []

        result = await bus.dispatch_intent("scene", "generateChart", [root, "bar", {"yScale": "log10"}])
        assert result is None
        assert "require line or scatter" in notifications.payload()["message"]
        assert agent.calls == []

    asyncio.run(run())


def test_invalid_model_scatter_result_gets_a_valid_numeric_placeholder():
    async def run():
        bus, document, _, _, root = _setup({
            "type": "scatter", "xScale": "log10", "xValues": [0, 1], "values": [1, 2],
        })
        node_id = await bus.dispatch_intent("scene", "generateChart", [root, "scatter"])
        assert node_id in document.nodes
        state = document.nodes[node_id].state
        assert state.chart_type == "scatter"
        assert "log10 axis" in state.chart_error
        assert state.chart_data["xValues"] == [1.0, 2.0]
        assert "type" not in state.chart_data

    asyncio.run(run())
