"""Regression checks for the compact composer context controls."""

from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2] / "composer_ui" / "src"


def test_context_is_not_rendered_as_a_title_or_summary_in_the_composer():
    source = (SOURCE_ROOT / "ComposerApp.tsx").read_text(encoding="utf-8")

    assert 'className="context-bar"' not in source
    assert "contextLabel" not in source
    assert "contextSummary" not in source
    assert "state.context.anchor" not in source
    assert "state.context.totalTokens" not in source
    assert "const attachmentCount = state.context.items.length" in source


def test_attachment_actions_use_fixed_compact_controls():
    source = (SOURCE_ROOT / "ComposerApp.tsx").read_text(encoding="utf-8")
    styles = (SOURCE_ROOT / "styles.css").read_text(encoding="utf-8")

    assert 'className="attachment-control"' in source
    assert 'className="attachment-count"' in source
    assert "bridgeRef.current?.requestAttachment()" in source
    assert "bridgeRef.current?.reviewContext()" in source
    assert ".attachment-control" in styles
    assert ".attachment-count" in styles
    assert "width: 30px;" in styles


def test_graph_anchor_is_not_part_of_attachment_badge_logic():
    source = (SOURCE_ROOT / "ComposerApp.tsx").read_text(encoding="utf-8")

    assert "attachmentCount > 0" in source
    assert "state.context.anchor" not in source
