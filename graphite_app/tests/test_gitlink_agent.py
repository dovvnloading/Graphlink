"""Tests for graphite_plugins/gitlink/agent.py (extracted from
graphite_plugin_gitlink.py - see doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 4.4).

Path-safety helpers (_normalize_repo_path, _safe_local_target, _fingerprint_changes)
already have dedicated coverage in tests/test_gitlink_path_safety.py - this file covers
the rest of the module (text cleanup, XML context formatting, and GitlinkAgent) plus a
direct assertion that the module has zero Qt dependencies, same as the Code Review and
Quality Gate scoring-module tests.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.gitlink.agent import (
    GitlinkAgent,
    _clean_text,
    _compact_label_text,
    _decode_text_bytes,
    _is_repo_text_path,
    _truncate_for_context,
    _wrap_cdata,
    _xml_file_block,
)


def test_module_has_no_qt_dependency():
    import graphite_plugins.gitlink.agent as agent_module

    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    for banned in ("PySide6", "qtawesome", "QGraphics", "QWidget", "QApplication"):
        assert banned not in source, f"{banned} leaked into the supposedly Qt-free gitlink agent module"


class TestCleanText:
    def test_collapses_excess_blank_lines(self):
        assert _clean_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_truncates_with_ellipsis_when_over_limit(self):
        result = _clean_text("x" * 100, limit=10)
        assert result.endswith("...")
        assert len(result) == 10

    def test_none_input_becomes_empty_string(self):
        assert _clean_text(None) == ""


class TestCompactLabelText:
    def test_short_text_passes_through(self):
        assert _compact_label_text("short") == "short"

    def test_long_text_truncates_with_ellipsis(self):
        result = _compact_label_text("a" * 50, limit=10)
        assert result.endswith("...")
        assert len(result) == 10


class TestDecodeTextBytes:
    def test_decodes_valid_utf8(self):
        assert _decode_text_bytes("hello".encode("utf-8")) == "hello"

    def test_falls_back_without_raising_on_invalid_bytes(self):
        result = _decode_text_bytes(b"\xff\xfe\x00\x01")
        assert isinstance(result, str)


class TestIsRepoTextPath:
    def test_python_file_is_text(self):
        assert _is_repo_text_path("src/main.py") is True

    def test_png_is_excluded(self):
        assert _is_repo_text_path("assets/logo.png") is False

    def test_case_insensitive_exclusion(self):
        assert _is_repo_text_path("assets/LOGO.PNG") is False


class TestXmlFileBlock:
    def test_escapes_special_characters_in_path(self):
        block = _xml_file_block('a"b.py', "content")
        assert "&quot;" in block

    def test_wraps_content_in_cdata(self):
        block = _xml_file_block("a.py", "print(1)")
        assert "<![CDATA[" in block
        assert "print(1)" in block

    def test_cdata_terminator_inside_content_is_escaped(self):
        block = _wrap_cdata("]]>")
        assert "]]]]><![CDATA[>" in block


class TestTruncateForContext:
    def test_short_text_is_not_truncated(self):
        text, truncated = _truncate_for_context("short", max_chars=100)
        assert text == "short"
        assert truncated is False

    def test_long_text_is_truncated_with_flag_set(self):
        text, truncated = _truncate_for_context("x" * 200, max_chars=50)
        assert truncated is True
        assert len(text) == 50


class TestGitlinkAgent:
    def test_normalize_files_deduplicates_by_path_and_sorts(self):
        agent = GitlinkAgent()
        raw_items = [
            {"path": "z.py", "operation": "update", "content": "z"},
            {"path": "a.py", "operation": "create", "content": "a"},
            {"path": "a.py", "operation": "update", "content": "a2"},
        ]
        result = agent._normalize_files(raw_items)
        assert [item["path"] for item in result] == ["a.py", "z.py"]
        assert result[0]["content"] == "a2"

    def test_normalize_files_rejects_paths_that_escape_the_repo(self):
        agent = GitlinkAgent()
        raw_items = [{"path": "../outside.py", "operation": "update", "content": "x"}]
        assert agent._normalize_files(raw_items) == []

    def test_normalize_files_drops_update_without_content(self):
        agent = GitlinkAgent()
        raw_items = [{"path": "a.py", "operation": "update"}]
        assert agent._normalize_files(raw_items) == []

    def test_normalize_files_keeps_delete_without_content(self):
        agent = GitlinkAgent()
        raw_items = [{"path": "a.py", "operation": "delete"}]
        result = agent._normalize_files(raw_items)
        assert result[0]["operation"] == "delete"

    def test_get_response_parses_valid_json_and_reports_change_count(self):
        agent = GitlinkAgent()
        fake_response = {
            "message": {
                "content": '{"summary": "did a thing", "write_intent": "changes_ready", "rationale": "why", "notes": [], "files": [{"path": "a.py", "operation": "update", "content": "x = 1"}]}'
            }
        }
        with patch("graphite_plugins.gitlink.agent.api_provider.chat", return_value=fake_response):
            result = agent.get_response({"task_prompt": "do something", "context_xml": "<x/>"})
        assert result["write_intent"] == "changes_ready"
        assert result["change_count"] == 1

    def test_get_response_blocks_when_json_parsing_fails(self):
        agent = GitlinkAgent()
        fake_response = {"message": {"content": "not json at all"}}
        with patch("graphite_plugins.gitlink.agent.api_provider.chat", return_value=fake_response):
            result = agent.get_response({"task_prompt": "do something", "context_xml": "<x/>"})
        assert result["write_intent"] == "blocked"
        assert result["files"] == []

    def test_get_response_blocks_when_model_claims_ready_but_returns_no_files(self):
        # get_response first downgrades an empty-files "changes_ready" claim to
        # "no_changes" and appends a note about it, but the trailing rule ("any
        # note plus zero files forces blocked") then escalates it to "blocked" -
        # this test locks in that two-step interaction rather than assuming the
        # first downgrade is the final answer.
        agent = GitlinkAgent()
        fake_response = {
            "message": {
                "content": '{"summary": "s", "write_intent": "changes_ready", "rationale": "r", "notes": [], "files": []}'
            }
        }
        with patch("graphite_plugins.gitlink.agent.api_provider.chat", return_value=fake_response):
            result = agent.get_response({"task_prompt": "do something", "context_xml": "<x/>"})
        assert result["write_intent"] == "blocked"
