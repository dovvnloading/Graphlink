"""Tests for WebSearchAgent.validate_content()'s SAFE/UNSAFE decision parsing.

Regression coverage for a bug where the safety gate checked `"SAFE" in decision`
without first checking for "UNSAFE" - since "SAFE" is a substring of "UNSAFE", an
explicit UNSAFE verdict from the model was treated as safe, making the gate a no-op.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_agents_web import WebSearchAgent


def _chat_response(text):
    return {"message": {"content": text, "role": "assistant"}}


class TestValidateContent:
    def test_explicit_unsafe_is_rejected(self):
        agent = WebSearchAgent.__new__(WebSearchAgent)
        agent.validation_prompt = "system prompt"
        with patch("graphlink_agents_web.api_provider.chat", return_value=_chat_response("UNSAFE")):
            assert agent.validate_content("query", "content") is False

    def test_explicit_safe_is_accepted(self):
        agent = WebSearchAgent.__new__(WebSearchAgent)
        agent.validation_prompt = "system prompt"
        with patch("graphlink_agents_web.api_provider.chat", return_value=_chat_response("SAFE")):
            assert agent.validate_content("query", "content") is True

    def test_unsafe_with_surrounding_text_is_rejected(self):
        agent = WebSearchAgent.__new__(WebSearchAgent)
        agent.validation_prompt = "system prompt"
        with patch("graphlink_agents_web.api_provider.chat", return_value=_chat_response("Verdict: UNSAFE.")):
            assert agent.validate_content("query", "content") is False

    def test_safe_with_surrounding_text_is_accepted(self):
        agent = WebSearchAgent.__new__(WebSearchAgent)
        agent.validation_prompt = "system prompt"
        with patch("graphlink_agents_web.api_provider.chat", return_value=_chat_response("This is SAFE.")):
            assert agent.validate_content("query", "content") is True
