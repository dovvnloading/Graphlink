"""Qt-removal plan R6.2 prerequisite: ChartDataAgent extracted out of
graphlink_agents_tools.py into a new Qt-free graphlink_chart_agent.py,
mirroring the exact split R5.2 already did for ArtifactAgent/
graphlink_artifact_agent.py (see test_artifact_agent_extraction.py) and R4.2
did for ChatAgent/graphlink_chat_agent.py.

graphlink_agents_tools.py's own `from PySide6.QtCore import QThread, Signal`
(needed only by ChartWorkerThread/ImageGenerationWorkerThread/
ModelPullWorkerThread) pulled Qt into any importer, including ChartDataAgent
despite it containing zero Qt code. graphlink_agents_tools.py re-exports
ChartDataAgent unchanged for backward compatibility (its own ChartWorkerThread
constructs one internally, and graphlink_agents.py's facade still imports it
from there - see graphlink_window_actions.py's own import of ChartWorkerThread
via that facade).

The behavior-parity tests below reconstruct representative call shapes
against the pre-extraction file's own get_response (originally
graphlink_agents_tools.py lines 602-702) plus its repair_chart_data
(originally lines 578-595) and heuristic_chart_data (originally lines
319-378) fallbacks, to confirm the move changed nothing observable - not an
exhaustive re-test of graphlink_app/tests/test_chart_nodes.py's own coverage
of canonicalize_chart_data/validate_chart_data, which is untouched by this
extraction and still imports ChartDataAgent from graphlink_agents_tools.py
directly.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_agents_tools
import graphlink_chart_agent
import api_provider
from graphlink_agents_tools import ChartDataAgent, ChartWorkerThread


class TestModuleBoundary:
    def test_the_old_module_re_exports_the_same_class_not_a_copy(self):
        assert graphlink_agents_tools.ChartDataAgent is ChartDataAgent
        assert graphlink_agents_tools.ChartDataAgent is graphlink_chart_agent.ChartDataAgent

    def test_chart_data_agents_real_home_is_the_qt_free_module(self):
        assert ChartDataAgent.__module__ == "graphlink_chart_agent"

    def test_the_worker_threads_own_agent_resolves_to_the_new_module(self):
        # ChartWorkerThread constructs its own ChartDataAgent internally -
        # confirm that reference resolves to ChartDataAgent's real (Qt-free)
        # home, not a stale/duplicated definition.
        worker = ChartWorkerThread("some source text", "bar")
        assert isinstance(worker.agent, ChartDataAgent)
        assert type(worker.agent).__module__ == "graphlink_chart_agent"
        assert worker.text == "some source text"
        assert worker.chart_type == "bar"


def _fake_chat_response(content: str) -> dict:
    return {"message": {"content": content}}


class TestBehaviorUnchangedByExtraction:
    """Reconstructs the pre-extraction file's own get_response call shape
    (graphlink_agents_tools.py lines 602-702 before this move) against a
    couple of representative cases - well-formed, repaired, and
    fully-degraded-to-heuristic - to confirm the extraction is a pure move,
    not a behavior change."""

    def test_well_formed_response_produces_valid_canonical_chart_data(self, monkeypatch):
        # Mirrors the pre-extraction file's own "happy path" through
        # get_response: json.loads succeeds first try, normalize_chart_data
        # is a no-op on an already-correct shape, and validate_chart_data
        # (which delegates to canonicalize_chart_data - originally lines
        # 516-534) passes without ever touching repair_chart_data or
        # heuristic_chart_data.
        monkeypatch.setattr(
            api_provider,
            "chat",
            lambda task, messages, **kwargs: _fake_chat_response(
                json.dumps({
                    "type": "bar",
                    "title": "Q3 Sales",
                    "labels": ["A", "B"],
                    "values": [10, 20],
                    "xAxis": "Product",
                    "yAxis": "Units",
                })
            ),
        )

        agent = ChartDataAgent()
        result = json.loads(agent.get_response("We sold 10 A and 20 B.", "bar"))

        assert "error" not in result
        assert result["type"] == "bar"
        assert result["labels"] == ["A", "B"]
        assert result["values"] == [10.0, 20.0]

    def test_non_json_first_attempt_recovers_via_repair_round_trip(self, monkeypatch):
        # Mirrors the pre-extraction file's own repair path invoked directly
        # from the json.JSONDecodeError branch (originally lines 636-646):
        # the first response is plain prose with no JSON object at all (both
        # of clean_response's own extraction regexes - originally lines
        # 199-211 - come up empty), so json.loads raises, and get_response
        # falls through to exactly one repair_chart_data round trip
        # (originally lines 578-595), which returns the corrected shape.
        calls = []

        def fake_chat(task, messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                return _fake_chat_response(
                    "Sure, here is a description of the sales you asked about, no JSON included."
                )
            # The repair round trip's own response - a real JSON object.
            return _fake_chat_response(json.dumps({
                "type": "bar",
                "title": "Q3 Sales",
                "labels": ["A", "B"],
                "values": [10, 20],
                "xAxis": "Category",
                "yAxis": "Value",
            }))

        monkeypatch.setattr(api_provider, "chat", fake_chat)

        agent = ChartDataAgent()
        result = json.loads(agent.get_response("We sold 10 A and 20 B.", "bar"))

        assert len(calls) == 2, "expected exactly one repair round trip"
        assert "error" not in result
        assert result["labels"] == ["A", "B"]
        assert result["values"] == [10.0, 20.0]

    def test_total_agent_failure_still_recovers_via_heuristic_fallback(self, monkeypatch):
        # Mirrors the pre-extraction file's own final fallback layer
        # (heuristic_chart_data, originally lines 319-378): both the initial
        # response AND the repair round trip return an explicit {"error":
        # ...} object (the model genuinely found nothing), so get_response's
        # own heuristic_chart_data call (originally lines 648-653, then
        # again at 661-667) is the only thing standing between this and a
        # returned error - pure-regex, zero-LLM, extracting "label value"
        # pairs directly from the source text.
        monkeypatch.setattr(
            api_provider,
            "chat",
            lambda task, messages, **kwargs: _fake_chat_response(
                json.dumps({"error": "Could not find sufficient data to generate a bar chart."})
            ),
        )

        agent = ChartDataAgent()
        source_text = "Product A 10, Product B 20"
        result = json.loads(agent.get_response(source_text, "bar"))

        assert "error" not in result, (
            "heuristic_chart_data should have recovered a real chart from "
            f"clearly label-value-shaped source text, got: {result}"
        )
        assert result["type"] == "bar"
        assert result["labels"] == ["Product A", "Product B"]
        assert result["values"] == [10.0, 20.0]
