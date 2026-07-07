"""Tests for graphite_plugins/quality_gate/scoring.py (extracted from
graphite_plugin_quality_gate.py - see doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.7).

The whole point of this extraction was to make the scoring/rubric engine directly
unit-testable without constructing any Qt widget or QApplication. This file proves
that by deliberately NOT creating a QApplication anywhere and NOT importing
graphite_plugin_quality_gate.py (the Qt-heavy widget file) at all - only the pure
scoring module.
"""

import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.quality_gate.scoring import (
    QUALITY_GATE_ALLOWED_PLUGINS,
    QUALITY_GATE_PLUGIN_ICONS,
    QualityGateAnalyzer,
    _clean_text,
    _collect_branch_nodes,
    _extract_node_text,
    _flatten_content,
    build_quality_gate_payload,
)


def test_module_has_no_qt_dependency():
    import graphite_plugins.quality_gate.scoring as scoring_module

    source = Path(scoring_module.__file__).read_text(encoding="utf-8")
    for banned in ("PySide6", "qtawesome", "QGraphics", "QWidget", "QApplication"):
        assert banned not in source, f"{banned} leaked into the supposedly Qt-free scoring module"


def test_every_icon_entry_is_an_allowed_plugin():
    assert QUALITY_GATE_ALLOWED_PLUGINS == list(QUALITY_GATE_PLUGIN_ICONS.keys())


class TestFlattenContent:
    def test_passes_through_plain_string(self):
        assert _flatten_content("hello") == "hello"

    def test_extracts_text_parts_from_content_list(self):
        content = [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]
        assert _flatten_content(content) == "a\nb"

    def test_stringifies_other_types(self):
        assert _flatten_content(42) == "42"


class TestCleanText:
    def test_collapses_excess_blank_lines(self):
        assert _clean_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_truncates_with_ellipsis_when_over_limit(self):
        result = _clean_text("x" * 100, limit=10)
        assert result.endswith("...")
        assert len(result) == 10


class TestCollectBranchNodes:
    def test_walks_parent_chain_from_root_to_leaf(self):
        root = types.SimpleNamespace(parent_node=None)
        middle = types.SimpleNamespace(parent_node=root)
        leaf = types.SimpleNamespace(parent_node=middle)
        assert _collect_branch_nodes(leaf) == [root, middle, leaf]

    def test_breaks_cycles_instead_of_looping_forever(self):
        a = types.SimpleNamespace()
        b = types.SimpleNamespace()
        a.parent_node = b
        b.parent_node = a
        result = _collect_branch_nodes(a)
        assert len(result) == 2


class TestExtractNodeText:
    def test_pulls_plain_text_attribute(self):
        node = types.SimpleNamespace(text="hello world")
        assert "hello world" in _extract_node_text(node)

    def test_pulls_conversation_history(self):
        node = types.SimpleNamespace(
            conversation_history=[{"role": "user", "content": "what is up"}]
        )
        result = _extract_node_text(node)
        assert "User: what is up" in result

    def test_deduplicates_identical_parts(self):
        node = types.SimpleNamespace(text="same", prompt="same")
        result = _extract_node_text(node)
        assert result.count("same") == 1


class TestBuildQualityGatePayload:
    def test_single_node_without_branch_context(self):
        node = types.SimpleNamespace(text="just this node", parent_node=None)
        payload = build_quality_gate_payload(node, include_branch_context=False)
        assert payload["depth"] == 1
        assert "just this node" in payload["transcript"]

    def test_branch_context_walks_lineage(self):
        root = types.SimpleNamespace(text="root step", parent_node=None)
        leaf = types.SimpleNamespace(text="leaf step", parent_node=root)
        payload = build_quality_gate_payload(leaf, include_branch_context=True)
        assert payload["depth"] == 2
        assert "root step" in payload["transcript"]
        assert "leaf step" in payload["transcript"]


class TestQualityGateAnalyzerFallback:
    def test_fallback_review_runs_without_any_llm_call(self):
        analyzer = QualityGateAnalyzer()
        payload = {"transcript": "def add(a, b):\n    return a + b\n\ntest passed", "label": "branch"}
        result = analyzer._fallback_review("Ship the feature", "Must pass tests", payload)
        assert "verdict" in result
        assert result["verdict"] in {"ready", "needs_work", "blocked"}

    def test_fallback_review_recommends_only_allowed_plugins(self):
        analyzer = QualityGateAnalyzer()
        payload = {"transcript": "", "label": "branch"}
        result = analyzer._fallback_review("Build something", "", payload)
        for item in result["recommended_plugins"]:
            assert item["plugin"] in QUALITY_GATE_ALLOWED_PLUGINS

    def test_get_response_falls_back_when_the_llm_call_raises(self):
        analyzer = QualityGateAnalyzer()
        payload = {"transcript": "print(1)", "label": "branch", "node_labels": []}
        with patch(
            "graphite_plugins.quality_gate.scoring.call_llm_and_parse_json",
            side_effect=RuntimeError("no network in this test"),
        ):
            result = analyzer.get_response("goal", "criteria", payload)
        assert "verdict" in result
        assert "review_markdown" in result
