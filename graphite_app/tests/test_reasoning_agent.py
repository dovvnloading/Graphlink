"""Tests for graphite_plugins/reasoning/agent.py (see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.10 - the Graphlink-Reasoning redesign).

This is the redesign of graphite_agents_reasoning.py's ReasoningAgent: collapses a
1 + budget*2 + 1 raw-text-call pipeline (up to 22 calls) into 1 + budget*1 + 1
structured-JSON calls, fixing two crash/corruption bugs as a structural side effect:

- The old plan parser (re.split(r'\\d+\\.\\s*', plan_str)) could produce an empty
  plan_steps list, causing a ZeroDivisionError at `i % len(plan_steps)`. The new
  _normalize_plan/_fallback_plan structurally guarantee `steps` is never empty.
- The old critique parser hunted for the literal substring "Refined Thought:" and, if
  missing, used the ENTIRE raw critique response (including a "**Critique:**" preamble)
  as the refined thought. The new _normalize_step_result falls back to initial_thought
  specifically, never the raw response.

Deliberately does not create a QApplication or import the widget file - this proves the
agent logic is genuinely Qt-free, matching tests/test_quality_gate_scoring.py's pattern.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.reasoning.agent import ReasoningAgent, _truncate_thought_history


def test_module_has_no_qt_dependency():
    import graphite_plugins.reasoning.agent as agent_module

    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    for banned in ("PySide6", "qtawesome", "QGraphics", "QWidget", "QApplication"):
        assert banned not in source, f"{banned} leaked into the supposedly Qt-free reasoning agent module"


def _chat_response(content):
    return {"message": {"content": content, "role": "assistant"}}


class TestPlan:
    def test_well_formed_plan_is_parsed(self):
        agent = ReasoningAgent()
        raw = '{"steps": [{"title": "Step one", "goal": "Do the first thing"}, {"title": "Step two", "goal": "Do the second thing"}]}'
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response(raw)):
            result = agent.plan("solve the problem", "")
        assert len(result["steps"]) == 2
        assert result["steps"][0]["title"] == "Step one"

    def test_empty_response_falls_back_to_a_single_non_empty_step(self):
        # This is the regression guard for the ZeroDivisionError: the old regex-based
        # parser could yield an empty plan_steps list from a blank/malformed response.
        agent = ReasoningAgent()
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response("")):
            result = agent.plan("solve the problem", "")
        assert len(result["steps"]) >= 1

    def test_non_json_response_falls_back_to_a_single_non_empty_step(self):
        agent = ReasoningAgent()
        with patch(
            "graphite_plugins.reasoning.agent.api_provider.chat",
            return_value=_chat_response("Sorry, I can't produce a plan for that."),
        ):
            result = agent.plan("solve the problem", "")
        assert len(result["steps"]) >= 1

    def test_plan_with_only_non_dict_steps_falls_back(self):
        agent = ReasoningAgent()
        raw = '{"steps": ["just a string", 42]}'
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response(raw)):
            result = agent.plan("solve the problem", "")
        assert len(result["steps"]) >= 1

    def test_plan_is_capped_at_max_steps(self):
        agent = ReasoningAgent()
        steps = [{"title": f"Step {i}", "goal": f"Goal {i}"} for i in range(30)]
        import json as _json
        raw = _json.dumps({"steps": steps})
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response(raw)):
            result = agent.plan("solve the problem", "")
        assert len(result["steps"]) <= 12

    def test_api_failure_propagates_instead_of_falling_back(self):
        # A genuine transport/model failure should abort the run, not silently degrade
        # to a fallback plan - see the module docstring for why this differs from
        # Quality Gate/Workflow's "fall back on any exception" convention.
        agent = ReasoningAgent()
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", side_effect=ConnectionError("no network")):
            try:
                agent.plan("solve the problem", "")
                assert False, "expected a RuntimeError to propagate"
            except RuntimeError as exc:
                assert "planning" in str(exc)


class TestReasonAndCritique:
    def test_well_formed_result_is_parsed(self):
        agent = ReasoningAgent()
        raw = '{"initial_thought": "first pass", "critique": "some issues", "refined_thought": "better answer"}'
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response(raw)):
            result = agent.reason_and_critique("query", "", {"title": "Step", "goal": "Goal"}, [])
        assert result["refined_thought"] == "better answer"

    def test_missing_refined_thought_falls_back_to_initial_thought_not_raw_response(self):
        # Regression guard for the critique-pollution bug: the old code would use the
        # ENTIRE raw response (including a "**Critique:**" preamble) when the
        # "Refined Thought:" marker was missing. The new behavior falls back to
        # initial_thought specifically.
        agent = ReasoningAgent()
        raw = '{"initial_thought": "first pass answer", "critique": "some issues found"}'
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response(raw)):
            result = agent.reason_and_critique("query", "", {"title": "Step", "goal": "Goal"}, [])
        assert result["refined_thought"] == "first pass answer"
        assert "**Critique:**" not in result["refined_thought"]

    def test_completely_unusable_response_falls_back_to_the_step_goal(self):
        agent = ReasoningAgent()
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response("not json")):
            result = agent.reason_and_critique(
                "query", "", {"title": "Step", "goal": "carry this goal forward"}, []
            )
        assert "carry this goal forward" in result["refined_thought"]

    def test_api_failure_propagates_instead_of_falling_back(self):
        agent = ReasoningAgent()
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", side_effect=TimeoutError("timed out")):
            try:
                agent.reason_and_critique("query", "", {"title": "Step", "goal": "Goal"}, [])
                assert False, "expected a RuntimeError to propagate"
            except RuntimeError as exc:
                assert "Step" in str(exc)


class TestSynthesize:
    def test_returns_the_raw_message_content(self):
        agent = ReasoningAgent()
        with patch(
            "graphite_plugins.reasoning.agent.api_provider.chat",
            return_value=_chat_response("# Final Answer\n\nHere it is."),
        ):
            result = agent.synthesize("query", "", ["thought one"])
        assert result == "# Final Answer\n\nHere it is."

    def test_api_failure_raises(self):
        agent = ReasoningAgent()
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", side_effect=RuntimeError("boom")):
            try:
                agent.synthesize("query", "", [])
                assert False, "expected a RuntimeError to propagate"
            except RuntimeError as exc:
                assert "synthesis" in str(exc)


class TestTruncateThoughtHistory:
    def test_empty_history_has_a_placeholder(self):
        assert _truncate_thought_history([]) == "No thoughts yet."

    def test_only_the_last_n_entries_are_kept(self):
        history = [f"thought {i}" for i in range(20)]
        result = _truncate_thought_history(history)
        assert "thought 19" in result
        assert "thought 0" not in result

    def test_each_entry_is_length_capped(self):
        history = ["x" * 5000]
        result = _truncate_thought_history(history)
        assert len(result) < 5000
        assert result.endswith("...")


class TestPlanBudgetModuloNeverDividesByZero:
    def test_budget_larger_than_plan_step_count_loops_safely(self):
        # Direct regression test for the exact crash being fixed: a budget of 10
        # against a 1-step plan must be able to loop via modulo without ever hitting
        # ZeroDivisionError, because _fallback_plan guarantees at least one step.
        agent = ReasoningAgent()
        with patch("graphite_plugins.reasoning.agent.api_provider.chat", return_value=_chat_response("")):
            plan_result = agent.plan("query", "")
        steps = plan_result["steps"]
        assert len(steps) > 0
        for i in range(10):
            step = steps[i % len(steps)]
            assert step["title"]
