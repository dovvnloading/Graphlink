"""Tests for graphite_plugins/code_review/scoring.py (extracted from
graphite_plugin_code_review.py - see doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.2).

The whole point of this extraction was to make the scoring/rubric engine directly
unit-testable without constructing any Qt widget or QApplication. This file proves
that by deliberately NOT creating a QApplication anywhere and NOT importing
graphite_plugin_code_review.py (the Qt-heavy widget file) at all - only the pure
scoring module.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.code_review.scoring import (
    CODE_REVIEW_METRIC_MARKDOWN,
    REVIEW_CATEGORY_LABELS,
    REVIEW_CATEGORY_WEIGHTS,
    SEVERITY_ORDER,
    CodeReviewAnalyzer,
    _clamp_score,
    _clean_text,
    _looks_like_python,
    _severity_key,
    _titleize_key,
)


def test_module_has_no_qt_dependency():
    import graphite_plugins.code_review.scoring as scoring_module

    source = Path(scoring_module.__file__).read_text(encoding="utf-8")
    for banned in ("PySide6", "qtawesome", "QGraphics", "QWidget", "QApplication"):
        assert banned not in source, f"{banned} leaked into the supposedly Qt-free scoring module"


def test_review_category_weights_sum_to_one_hundred():
    assert sum(REVIEW_CATEGORY_WEIGHTS.values()) == 100


def test_every_weighted_category_has_a_label():
    assert set(REVIEW_CATEGORY_WEIGHTS.keys()) == set(REVIEW_CATEGORY_LABELS.keys())


class TestClampScore:
    def test_clamps_above_100(self):
        assert _clamp_score(150) == 100

    def test_clamps_below_0(self):
        assert _clamp_score(-20) == 0

    def test_passes_through_valid_value(self):
        assert _clamp_score(73) == 73

    def test_falls_back_to_default_on_invalid_input(self):
        assert _clamp_score("not a number", default=42) == 42


class TestSeverityKey:
    def test_recognized_severity_passes_through(self):
        assert _severity_key("high") == "high"

    def test_unrecognized_value_falls_back_to_medium(self):
        assert _severity_key("catastrophic") == "medium"

    def test_is_case_insensitive(self):
        assert _severity_key("CRITICAL") == "critical"


def test_severity_order_is_a_total_ordering_of_five_levels():
    assert set(SEVERITY_ORDER.keys()) == {"critical", "high", "medium", "low", "info"}
    assert SEVERITY_ORDER["critical"] < SEVERITY_ORDER["high"] < SEVERITY_ORDER["medium"]


class TestTitleizeKey:
    def test_converts_snake_case_to_title_case(self):
        assert _titleize_key("maintainability_score") == "Maintainability Score"

    def test_converts_dashes_too(self):
        assert _titleize_key("high-risk") == "High Risk"

    def test_empty_input_falls_back_to_general(self):
        assert _titleize_key("") == "General"


class TestCleanText:
    def test_collapses_excess_blank_lines(self):
        assert _clean_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_truncates_with_ellipsis_when_over_limit(self):
        result = _clean_text("x" * 100, limit=10)
        assert result.endswith("...")
        assert len(result) == 10


class TestLooksLikePython:
    def test_py_extension_is_python(self):
        assert _looks_like_python({"path": "app/main.py"}, "") is True

    def test_recognizes_python_keywords_without_a_py_path(self):
        source = "import os\n\ndef main():\n    class Foo:\n        pass\n"
        assert _looks_like_python({}, source) is True

    def test_non_python_content_is_not_python(self):
        assert _looks_like_python({}, "<html><body>hi</body></html>") is False


class TestCodeReviewAnalyzerFallback:
    def test_fallback_review_runs_without_any_llm_call(self):
        analyzer = CodeReviewAnalyzer()
        payload = {
            "source_for_model": "def add(a, b):\n    return a + b\n",
            "source_text": "def add(a, b):\n    return a + b\n",
            "source_state": {"label": "math.py"},
            "source_truncated": False,
            "visible_lines": 2,
        }
        result = analyzer._fallback_review(payload, "network unavailable")
        assert "category_scores" in result

    def test_system_prompt_embeds_the_metric_markdown(self):
        analyzer = CodeReviewAnalyzer()
        assert CODE_REVIEW_METRIC_MARKDOWN in analyzer.SYSTEM_PROMPT

    def test_get_response_falls_back_when_the_llm_call_raises(self):
        analyzer = CodeReviewAnalyzer()
        payload = {
            "source_for_model": "print(1)",
            "source_text": "print(1)",
            "source_state": {},
            "source_truncated": False,
            "visible_lines": 1,
        }
        with patch(
            "graphite_plugins.code_review.scoring.call_llm_and_parse_json",
            side_effect=RuntimeError("no network in this test"),
        ):
            result = analyzer.get_response(payload)
        assert "category_scores" in result
        assert "quality_summary" in result
