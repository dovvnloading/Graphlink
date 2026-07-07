"""Tests that CodeReviewAnalyzer, QualityGateAnalyzer, WorkflowArchitectAgent, and
GitlinkAgent actually delegate to the shared graphite_plugins.common.llm_json helpers
(Phase 3b), rather than each having quietly reverted to (or drifted from) its own
hand-rolled regex/network-call logic.

Each plugin module does `from graphite_plugins.common.llm_json import
call_llm_and_parse_json, extract_json_object`, binding those names into its own module
namespace - so patching e.g. `graphite_plugin_code_review.call_llm_and_parse_json`
(not `graphite_plugins.common.llm_json.call_llm_and_parse_json`) is what proves this
particular call site is the one invoking it.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.graphite_plugin_code_review import CodeReviewAnalyzer
from graphite_plugins.graphite_plugin_gitlink import GitlinkAgent
from graphite_plugins.graphite_plugin_quality_gate import QualityGateAnalyzer
from graphite_plugins.graphite_plugin_workflow import WorkflowArchitectAgent


class TestExtractJsonDelegation:
    def test_code_review_analyzer_extract_json_delegates(self):
        analyzer = CodeReviewAnalyzer()
        with patch("graphite_plugins.graphite_plugin_code_review.extract_json_object", return_value="delegated") as mock_fn:
            result = analyzer._extract_json("raw text")
        mock_fn.assert_called_once_with("raw text")
        assert result == "delegated"

    def test_quality_gate_analyzer_clean_json_response_delegates(self):
        analyzer = QualityGateAnalyzer()
        with patch("graphite_plugins.graphite_plugin_quality_gate.extract_json_object", return_value="delegated") as mock_fn:
            result = analyzer._clean_json_response("raw text")
        mock_fn.assert_called_once_with("raw text")
        assert result == "delegated"

    def test_workflow_architect_agent_clean_json_response_delegates(self):
        agent = WorkflowArchitectAgent()
        with patch("graphite_plugins.graphite_plugin_workflow.extract_json_object", return_value="delegated") as mock_fn:
            result = agent._clean_json_response("raw text")
        mock_fn.assert_called_once_with("raw text")
        assert result == "delegated"

    def test_gitlink_module_extract_json_object_delegates(self):
        import graphite_plugins.graphite_plugin_gitlink as gitlink_module

        with patch("graphite_plugins.graphite_plugin_gitlink.extract_json_object", return_value="delegated") as mock_fn:
            result = gitlink_module._extract_json_object("raw text")
        mock_fn.assert_called_once_with("raw text")
        assert result == "delegated"


class TestCallLlmAndParseJsonDelegation:
    def test_code_review_analyzer_get_response_calls_through_on_success(self):
        analyzer = CodeReviewAnalyzer()
        analyzer._normalize_response = lambda parsed, payload: {"parsed": parsed, "payload": payload}
        payload = {"source_for_model": "print(1)", "source_state": {}, "source_truncated": False}

        with patch(
            "graphite_plugins.graphite_plugin_code_review.call_llm_and_parse_json",
            return_value={"quality_summary": "ok"},
        ) as mock_call:
            result = analyzer.get_response(payload)

        assert mock_call.call_args.kwargs.get("task") is not None or len(mock_call.call_args.args) >= 2
        assert result["parsed"] == {"quality_summary": "ok"}

    def test_code_review_analyzer_get_response_falls_back_on_exception(self):
        analyzer = CodeReviewAnalyzer()
        analyzer._normalize_response = lambda parsed, payload: {"parsed": parsed}
        analyzer._fallback_review = lambda payload, exc_text: {"fallback": True, "why": exc_text}
        payload = {"source_for_model": "print(1)", "source_state": {}, "source_truncated": False}

        with patch(
            "graphite_plugins.graphite_plugin_code_review.call_llm_and_parse_json",
            side_effect=RuntimeError("boom"),
        ):
            result = analyzer.get_response(payload)

        assert result["parsed"] == {"fallback": True, "why": "boom"}

    def test_quality_gate_analyzer_get_response_calls_through_on_success(self):
        analyzer = QualityGateAnalyzer()
        analyzer._normalize_review = lambda parsed, goal, criteria, payload: {"parsed": parsed}
        analyzer._build_markdown = lambda normalized, payload: "markdown"

        with patch(
            "graphite_plugins.graphite_plugin_quality_gate.call_llm_and_parse_json",
            return_value={"verdict": "ready"},
        ) as mock_call:
            result = analyzer.get_response("goal", "criteria", {"label": "branch", "node_labels": [], "transcript": ""})

        mock_call.assert_called_once()
        assert result["parsed"] == {"verdict": "ready"}
        assert result["review_markdown"] == "markdown"

    def test_quality_gate_analyzer_get_response_falls_back_on_exception(self):
        analyzer = QualityGateAnalyzer()
        analyzer._fallback_review = lambda goal, criteria, payload: {"fallback": True}
        analyzer._build_markdown = lambda normalized, payload: "markdown"

        with patch(
            "graphite_plugins.graphite_plugin_quality_gate.call_llm_and_parse_json",
            side_effect=RuntimeError("boom"),
        ):
            result = analyzer.get_response("goal", "criteria", {"label": "branch", "node_labels": [], "transcript": ""})

        assert result["fallback"] is True

    def test_workflow_architect_agent_get_response_falls_back_on_exception(self):
        agent = WorkflowArchitectAgent()
        agent._fallback_plan = lambda goal, constraints, history: {"fallback": True}
        agent._build_markdown = lambda normalized: "markdown"

        with patch(
            "graphite_plugins.graphite_plugin_workflow.call_llm_and_parse_json",
            side_effect=RuntimeError("boom"),
        ):
            result = agent.get_response("goal", "constraints", [])

        assert result["fallback"] is True
