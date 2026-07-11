"""Tests for the shared LLM-JSON helpers (Phase 3b of doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md).

graphlink_plugin_code_review.py, graphlink_plugin_quality_gate.py,
graphlink_plugin_workflow.py, and graphlink_plugin_gitlink.py each independently
hand-rolled the exact same regex to strip a JSON object out of an LLM response that
might be wrapped in a markdown fence. extract_json_object() replaces all four copies.
call_llm_and_parse_json() replaces the api_provider.chat()-then-json.loads() boilerplate
shared by the three plugins whose get_response() also has that shape (Gitlink's differs
in shape - it doesn't wrap the network call itself in a try/except - so it only uses
extract_json_object(), not this helper; see the module docstring for why).
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_plugins.common.llm_json import call_llm_and_parse_json, extract_json_object


class TestExtractJsonObject:
    def test_plain_json_object_is_returned_as_is(self):
        assert extract_json_object('{"a": 1}') == '{"a": 1}'

    def test_strips_markdown_json_fence(self):
        raw = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
        assert extract_json_object(raw) == '{"a": 1}'

    def test_strips_markdown_fence_without_json_language_tag(self):
        raw = '```\n{"a": 1}\n```'
        assert extract_json_object(raw) == '{"a": 1}'

    def test_extracts_object_from_surrounding_commentary_without_fences(self):
        raw = 'Sure, here is the result: {"a": 1} - let me know if you need changes.'
        assert extract_json_object(raw) == '{"a": 1}'

    def test_falls_back_to_raw_text_when_no_object_shape_found(self):
        assert extract_json_object("no json here") == "no json here"


class TestCallLlmAndParseJson:
    def test_returns_parsed_json_from_chat_response(self):
        fake_response = {"message": {"content": '{"result": 42}'}}
        with patch("graphlink_plugins.common.llm_json.api_provider.chat", return_value=fake_response) as mock_chat:
            result = call_llm_and_parse_json("system prompt", "user prompt", task="chat")
        assert result == {"result": 42}
        mock_chat.assert_called_once_with(
            task="chat",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user prompt"},
            ],
        )

    def test_handles_fenced_json_in_the_response(self):
        fake_response = {"message": {"content": '```json\n{"result": 1}\n```'}}
        with patch("graphlink_plugins.common.llm_json.api_provider.chat", return_value=fake_response):
            result = call_llm_and_parse_json("sys", "user", task="chat")
        assert result == {"result": 1}

    def test_raises_json_decode_error_on_invalid_json(self):
        fake_response = {"message": {"content": "not json at all and no braces"}}
        with patch("graphlink_plugins.common.llm_json.api_provider.chat", return_value=fake_response):
            with pytest.raises(json.JSONDecodeError):
                call_llm_and_parse_json("sys", "user", task="chat")

    def test_propagates_exceptions_from_the_chat_call(self):
        with patch("graphlink_plugins.common.llm_json.api_provider.chat", side_effect=RuntimeError("network down")):
            with pytest.raises(RuntimeError, match="network down"):
                call_llm_and_parse_json("sys", "user", task="chat")
