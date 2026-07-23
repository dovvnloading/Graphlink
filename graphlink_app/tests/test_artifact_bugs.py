"""Tests for three real Artifact plugin bugs fixed together:

1. The stop button was visibly a "stop" icon while running but stayed disabled the
   whole time, so clicking it did nothing - stop_artifact_node was never reachable
   from the UI.
2. ArtifactAgent.get_response silently treated the ENTIRE raw LLM response as the new
   document body whenever the model forgot the <artifact> tags, corrupting the
   document with whatever conversational text the model wrote instead.
3. Document/chat content was markdown-rendered without escaping literal HTML first, so
   AI/user-controlled text containing e.g. "<img src=... />" was handed to
   QTextEdit.setHtml as real markup instead of literal text.

Uses real, headlessly-constructed ArtifactNode instances (only a QApplication, no full
GUI event loop), matching this test suite's existing convention (see
tests/test_seed_prompt_protocol.py).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_artifact_agent import ArtifactAgent
from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode


class TestArtifactAgentTagParsing:
    def test_well_formed_tags_are_parsed_normally(self):
        agent = ArtifactAgent()
        raw_text = "<artifact>\n# Title\n\nBody text.\n</artifact>\nHere's the update."

        with patch(
            "graphlink_artifact_agent.api_provider.chat",
            return_value={"message": {"content": raw_text}},
        ):
            new_doc, ai_message = agent.get_response("old doc", [])

        assert new_doc == "# Title\n\nBody text."
        assert ai_message == "Here's the update."

    def test_missing_tags_raise_instead_of_silently_becoming_the_document(self):
        agent = ArtifactAgent()
        raw_text = "Sorry, I can't help with that right now."

        with patch(
            "graphlink_artifact_agent.api_provider.chat",
            return_value={"message": {"content": raw_text}},
        ):
            with pytest.raises(RuntimeError):
                agent.get_response("old doc", [])


class TestArtifactNodeStopButton:
    def test_button_stays_enabled_while_running(self):
        node = ArtifactNode(parent_node=None)
        node.set_running_state(True)
        assert node.update_button.isEnabled() is True

    def test_clicking_while_running_emits_stop_requested_not_artifact_requested(self):
        node = ArtifactNode(parent_node=None)
        node.set_running_state(True)

        stop_spy = MagicMock()
        artifact_spy = MagicMock()
        node.stop_requested.connect(stop_spy)
        node.artifact_requested.connect(artifact_spy)

        node._handle_action_button()

        stop_spy.assert_called_once_with(node)
        artifact_spy.assert_not_called()

    def test_clicking_while_idle_emits_artifact_requested_not_stop_requested(self):
        node = ArtifactNode(parent_node=None)
        node.set_running_state(False)

        stop_spy = MagicMock()
        artifact_spy = MagicMock()
        node.stop_requested.connect(stop_spy)
        node.artifact_requested.connect(artifact_spy)

        node._handle_action_button()

        artifact_spy.assert_called_once_with(node)
        stop_spy.assert_not_called()


class TestArtifactHtmlEscaping:
    def test_literal_html_tag_in_document_is_escaped_in_preview(self):
        node = ArtifactNode(parent_node=None)
        node.tabs.setCurrentIndex(1)  # Preview tab, so set_artifact_content renders it
        node.set_artifact_content("<img src=x onerror=alert(1)>")
        rendered = node.preview_display.toHtml()
        assert "<img src=x onerror=alert(1)>" not in rendered
        assert "&lt;img" in rendered

    def test_literal_html_tag_in_chat_message_is_escaped(self):
        node = ArtifactNode(parent_node=None)
        node.add_chat_message("<script>alert(1)</script>", is_user=True)
        assert "<script>alert(1)</script>" not in node.chat_html_cache
        assert "&lt;script&gt;" in node.chat_html_cache

    def test_normal_markdown_formatting_still_renders(self):
        node = ArtifactNode(parent_node=None)
        node.add_chat_message("**bold text**", is_user=True)
        assert "<strong>bold text</strong>" in node.chat_html_cache
