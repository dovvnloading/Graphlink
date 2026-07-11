"""Tests for clean_agent_markdown_response() and the three agents that delegate to it.

doc/ARCHITECTURE_REVIEW_FINDINGS.md #58: ExplainerAgent, KeyTakeawayAgent, and
GroupSummaryAgent each carried a near-identical ~40-line clean_text() method. Extracted
to one shared helper, verified behavior-preserving by capturing golden outputs from the
three *original* clean_text() implementations against a battery of inputs (plain text,
markdown noise, bullet lists, extra blank lines, an already-present title, unicode
arrows/bullets, empty/whitespace-only input) and confirming the refactored delegating
methods produce byte-identical output for every one of them.

That comparison surfaced one genuine (if narrow) behavioral difference between the three
original implementations: GroupSummaryAgent explicitly reset its "currently inside a
bullet list" tracking state when it hit a section-marker line (e.g. "Key Connected
Points:"), while ExplainerAgent/KeyTakeawayAgent did not. This only produces a visible
difference (one extra blank line) when a bullet list is immediately followed by a
section-marker line that is itself immediately followed by another bullet - the
`reset_bullet_state_on_section_header` parameter preserves that per-agent difference
exactly rather than silently unifying it. The tests below pin that specific case down.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_agents_core import (
    ExplainerAgent,
    GroupSummaryAgent,
    KeyTakeawayAgent,
    clean_agent_markdown_response,
)


class TestCleanAgentMarkdownResponseBasics:
    def test_strips_markdown_noise(self):
        result = clean_agent_markdown_response(
            "Text with **bold** and `code` and __underline__ and *italic* and _emphasis_.",
            required_title="Title",
            section_markers=[],
        )
        assert result == "Title\nText with bold and code and underline and italic and emphasis."

    def test_collapses_triple_newlines(self):
        result = clean_agent_markdown_response(
            "Line one\n\n\n\nLine two with extra blank lines.",
            required_title="Title",
            section_markers=[],
        )
        assert result == "Title\nLine one\nLine two with extra blank lines."

    def test_normalizes_dash_bullets_to_dot_bullets(self):
        result = clean_agent_markdown_response(
            "- bullet one\n- bullet two\n- bullet three",
            required_title="Title",
            section_markers=[],
        )
        assert result == "Title\n\n• bullet one\n• bullet two\n• bullet three"

    def test_does_not_duplicate_an_already_present_title(self):
        result = clean_agent_markdown_response(
            "Title\nAlready has the title on line one.\n- point one",
            required_title="Title",
            section_markers=[],
        )
        assert result.count("Title") == 1

    def test_empty_and_whitespace_only_input_returns_empty_string(self):
        assert clean_agent_markdown_response("", required_title="Title", section_markers=[]) == ""
        assert clean_agent_markdown_response("   \n\n  ", required_title="Title", section_markers=[]) == ""

    def test_section_marker_gets_a_blank_line_before_it(self):
        result = clean_agent_markdown_response(
            "Intro.\nMy Marker:\nMore text.",
            required_title="Title",
            section_markers=["My Marker:"],
        )
        assert result == "Title\nIntro.\n\nMy Marker:\nMore text."


class TestBulletStateResetAfterSectionHeaderIsPerAgent:
    def test_reset_true_adds_an_extra_blank_line_before_the_next_bullet(self):
        text = "- bullet one\n- bullet two\nMy Marker:\n- bullet three"
        result = clean_agent_markdown_response(
            text, required_title="Title", section_markers=["My Marker:"], reset_bullet_state_on_section_header=True
        )
        assert result == "Title\n\n• bullet one\n• bullet two\n\nMy Marker:\n\n• bullet three"

    def test_reset_false_does_not_add_the_extra_blank_line(self):
        text = "- bullet one\n- bullet two\nMy Marker:\n- bullet three"
        result = clean_agent_markdown_response(
            text, required_title="Title", section_markers=["My Marker:"], reset_bullet_state_on_section_header=False
        )
        assert result == "Title\n\n• bullet one\n• bullet two\n\nMy Marker:\n• bullet three"


class TestThreeAgentsDelegateWithTheirOriginalPerAgentBehavior:
    def test_explainer_uses_its_own_title_and_markers_without_bullet_reset(self):
        text = "- bullet one\n- bullet two\nThink of it Like This:\n- bullet three"
        result = ExplainerAgent().clean_text(text)
        assert result == (
            "Simple Explanation\n\n• bullet one\n• bullet two\n\n"
            "Think of it Like This:\n• bullet three"
        )

    def test_key_takeaway_uses_its_own_title_and_markers_without_bullet_reset(self):
        text = "- bullet one\n- bullet two\nMain Points:\n- bullet three"
        result = KeyTakeawayAgent().clean_text(text)
        assert result == (
            "Key Takeaway\n\n• bullet one\n• bullet two\n\nMain Points:\n• bullet three"
        )

    def test_group_summary_uses_its_own_title_and_markers_with_bullet_reset(self):
        text = "- bullet one\n- bullet two\nKey Connected Points:\n- bullet three"
        result = GroupSummaryAgent().clean_text(text)
        assert result == (
            "Synthesized Summary\n\n• bullet one\n• bullet two\n\n"
            "Key Connected Points:\n\n• bullet three"
        )

    def test_all_three_agents_produce_their_own_distinct_title_for_plain_text(self):
        plain = "Simple text with no markdown."
        assert ExplainerAgent().clean_text(plain) == "Simple Explanation\nSimple text with no markdown."
        assert KeyTakeawayAgent().clean_text(plain) == "Key Takeaway\nSimple text with no markdown."
        assert GroupSummaryAgent().clean_text(plain) == "Synthesized Summary\nSimple text with no markdown."
