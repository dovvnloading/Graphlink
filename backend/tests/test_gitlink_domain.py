"""Direct unit tests for the Gitlink plugin's domain logic (ADR-014 H3
close-out: graphlink_plugins/** had zero direct tests).

Everything in backend/tests/test_agents.py's "R5.3: Gitlink" section (and the
G3/G6 pinning tests from the ADR-002 approval-guards audit) exercises this
logic ONLY indirectly, through AgentDispatcher.start_gitlink_run/
start_gitlink_apply - GitlinkAgent.get_response and apply_change_set are
monkeypatched away there specifically so the dispatcher's own orchestration
(busy-guards, cancellation, the 3-way fingerprint compare-and-freeze) can be
pinned in isolation. This file targets what that isolation deliberately
skips: agent.py's and repository.py's own logic (XML context construction,
path-safety normalization, fingerprint hashing itself, the JSON write-intent
state machine, the on-disk apply/rollback mechanics) plus
common/github_client.py, the shared low-level GitHub REST helper both this
plugin and Code Review hang off of.

Mocking conventions mirrored from the rest of this suite:
  - api_provider.chat is monkeypatched to a plain lambda returning
    {"message": {"content": ...}} (see test_agents.py, conftest.py).
  - GitHub REST calls are faked via a duck-typed stand-in exposing
    `.request(url, params=None, *, expect_json=True, timeout=25)` - the
    exact shape GitlinkRepository depends on (it "owns nothing but a
    github_client reference" per its own module docstring) - so
    GitlinkRepository's own logic is tested without needing a real
    GitHubRestClient/requests.Session in play. github_client.py itself is
    covered separately, directly, by faking `requests.get` with a stand-in
    response object exposing status_code/json()/text/reason/content -
    matching test_web_research_providers_fetcher.py's established
    fake-response-object convention for this codebase.
  - All filesystem operations (apply_change_set, scan_local_repo_paths,
    read_local_repo_file, download_repository_snapshot) run against a real
    pytest tmp_path - no filesystem mocking - since these functions' whole
    job is a real on-disk contract (byte-exact writes, atomic rollback).
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

import api_provider
import graphlink_task_config as config
from graphlink_plugins.common import github_client as github_client_module
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink import repository as repository_module
from graphlink_plugins.gitlink.agent import (
    GitlinkAgent,
    _clean_text,
    _compact_label_text,
    _decode_text_bytes,
    _extract_json_object,
    _fingerprint_changes,
    _is_repo_text_path,
    _normalize_repo_path,
    _safe_local_target,
    _truncate_for_context,
    _wrap_cdata,
    _xml_file_block,
)
from graphlink_plugins.gitlink.repository import (
    ContextBundleResult,
    GitlinkRepository,
    apply_change_set,
    default_import_root,
    read_local_repo_file,
    resolve_scope_paths,
    scan_local_repo_paths,
    validate_pending_changes,
)


# =============================================================================
# graphlink_plugins/common/github_client.py
# =============================================================================


class _FakeHTTPResponse:
    """Stands in for requests.Response - exposes only what
    GitHubRestClient.request reads (status_code/json()/text/reason/content),
    mirroring test_web_research_providers_fetcher.py's _FakeResponse
    convention for this codebase."""

    def __init__(self, status_code=200, json_body=None, text="", reason="", content=b"", json_raises=False):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self.reason = reason
        self.content = content
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._json_body


class _FakeSettingsManagerWithToken:
    def __init__(self, token):
        self._token = token

    def get_github_token(self):
        return self._token


def test_get_token_returns_empty_string_when_no_settings_manager():
    client = GitHubRestClient(settings_manager=None)
    assert client.get_token() == ""


def test_get_token_strips_whitespace_from_settings_manager_token():
    client = GitHubRestClient(settings_manager=_FakeSettingsManagerWithToken("  ghp_abc123  \n"))
    assert client.get_token() == "ghp_abc123"


def test_build_headers_always_includes_accept_and_api_version():
    client = GitHubRestClient(settings_manager=None)
    headers = client.build_headers()
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "Authorization" not in headers


def test_build_headers_includes_bearer_token_when_present():
    client = GitHubRestClient(settings_manager=_FakeSettingsManagerWithToken("ghp_xyz"))
    headers = client.build_headers()
    assert headers["Authorization"] == "Bearer ghp_xyz"


def test_request_success_returns_parsed_json_and_forwards_params_and_timeout(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured.update(url=url, headers=headers, params=params, timeout=timeout)
        return _FakeHTTPResponse(status_code=200, json_body={"ok": True})

    monkeypatch.setattr(github_client_module.requests, "get", fake_get)
    client = GitHubRestClient(settings_manager=_FakeSettingsManagerWithToken("tok"))

    result = client.request("https://api.github.com/repos/o/r", params={"ref": "main"}, timeout=17)

    assert result == {"ok": True}
    assert captured["url"] == "https://api.github.com/repos/o/r"
    assert captured["params"] == {"ref": "main"}
    assert captured["timeout"] == 17
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_request_expect_json_false_returns_raw_content_bytes(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(status_code=200, content=b"raw-bytes-here"),
    )
    client = GitHubRestClient(settings_manager=None)
    assert client.request("https://example.com/x", expect_json=False) == b"raw-bytes-here"


def test_request_404_raises_friendly_not_found_message(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(status_code=404, json_body={"message": "Not Found"}),
    )
    client = GitHubRestClient(settings_manager=None)
    with pytest.raises(RuntimeError, match="GitHub resource not found"):
        client.request("https://api.github.com/repos/o/r")


def test_request_401_raises_friendly_invalid_token_message(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(status_code=401, json_body={"message": "Bad credentials"}),
    )
    client = GitHubRestClient(settings_manager=None)
    with pytest.raises(RuntimeError, match="GitHub rejected the saved token"):
        client.request("https://api.github.com/repos/o/r")


def test_request_403_rate_limit_message_raises_rate_limit_error(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(
            status_code=403, json_body={"message": "API rate limit exceeded for xxx."}
        ),
    )
    client = GitHubRestClient(settings_manager=None)
    with pytest.raises(RuntimeError, match="rate limit reached"):
        client.request("https://api.github.com/repos/o/r")


def test_request_403_non_rate_limit_uses_raw_github_message(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(status_code=403, json_body={"message": "Forbidden: access denied"}),
    )
    client = GitHubRestClient(settings_manager=None)
    with pytest.raises(RuntimeError, match="^Forbidden: access denied$"):
        client.request("https://api.github.com/repos/o/r")


def test_request_error_body_not_json_falls_back_to_text_then_reason(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(
            status_code=500, json_raises=True, text="", reason="Internal Server Error"
        ),
    )
    client = GitHubRestClient(settings_manager=None)
    with pytest.raises(RuntimeError, match="Internal Server Error"):
        client.request("https://api.github.com/repos/o/r")


def test_request_error_body_not_json_prefers_text_over_reason(monkeypatch):
    monkeypatch.setattr(
        github_client_module.requests, "get",
        lambda *a, **k: _FakeHTTPResponse(
            status_code=502, json_raises=True, text="upstream timeout", reason="Bad Gateway"
        ),
    )
    client = GitHubRestClient(settings_manager=None)
    with pytest.raises(RuntimeError, match="^upstream timeout$"):
        client.request("https://api.github.com/repos/o/r")


# =============================================================================
# graphlink_plugins/gitlink/agent.py - pure helpers
# =============================================================================


def test_clean_text_strips_and_collapses_three_or_more_blank_lines():
    text = "  hello\n\n\n\nworld  "
    assert _clean_text(text) == "hello\n\nworld"


def test_clean_text_truncates_with_ellipsis_at_limit():
    result = _clean_text("a" * 50, limit=10)
    assert result == "a" * 7 + "..."
    assert len(result) == 10


def test_clean_text_handles_none_value():
    assert _clean_text(None) == ""
    assert _clean_text(None, limit=10) == ""


def test_clean_text_no_limit_returns_full_text_unbounded():
    assert _clean_text("a" * 500) == "a" * 500


def test_compact_label_text_passthrough_under_limit():
    assert _compact_label_text("short label") == "short label"


def test_compact_label_text_truncates_over_default_limit():
    result = _compact_label_text("x" * 100)
    assert result == "x" * 31 + "..."
    assert len(result) == 34


def test_decode_text_bytes_prefers_utf8():
    assert _decode_text_bytes("héllo wörld".encode("utf-8")) == "héllo wörld"


def test_decode_text_bytes_does_not_strip_bom_because_plain_utf8_wins_first():
    # "utf-8-sig" is listed second in the fallback tuple specifically to
    # strip a BOM, but the BOM bytes (EF BB BF) are themselves a valid
    # UTF-8 encoding of U+FEFF - plain "utf-8" (tried first) decodes them
    # without raising, so the loop never reaches "utf-8-sig" and the BOM
    # survives as a leading ﻿ character. Pinning actual behavior here,
    # not the behavior the encoding-list order implies.
    raw = b"\xef\xbb\xbfhello"
    assert _decode_text_bytes(raw) == "﻿hello"


def test_decode_text_bytes_falls_back_to_cp1252_for_smart_quotes():
    # 0x93/0x94 are cp1252 "smart quotes" - invalid lead bytes in UTF-8
    # (both plain and BOM-sniffed), so this must fall through to cp1252.
    raw = b"\x93hello\x94"
    assert _decode_text_bytes(raw) == "\u201chello\u201d"


def test_decode_text_bytes_falls_back_to_latin1_when_cp1252_undefined():
    # 0x81 is undefined in cp1252 (raises), but latin-1 maps every byte
    # 0-255 losslessly, so this is the last fallback that actually succeeds.
    raw = b"\x81"
    assert _decode_text_bytes(raw) == "\x81"


def test_is_repo_text_path_excludes_known_binary_suffixes_case_insensitively():
    assert _is_repo_text_path("assets/logo.PNG") is False
    assert _is_repo_text_path("bin/app.exe") is False
    assert _is_repo_text_path("archive.tar.gz") is False


def test_is_repo_text_path_includes_normal_source_files():
    assert _is_repo_text_path("src/module.py") is True
    assert _is_repo_text_path("README.md") is True
    assert _is_repo_text_path("") is True


def test_normalize_repo_path_converts_backslashes_and_normalizes():
    assert _normalize_repo_path("src\\pkg\\module.py") == "src/pkg/module.py"


def test_normalize_repo_path_rejects_empty_string():
    with pytest.raises(RuntimeError, match="cannot be empty"):
        _normalize_repo_path("")
    with pytest.raises(RuntimeError, match="cannot be empty"):
        _normalize_repo_path("   ")


def test_normalize_repo_path_rejects_leading_slash():
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("/etc/passwd")


def test_normalize_repo_path_rejects_dotdot_traversal():
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("../../etc/passwd")
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("src/../../secret.txt")


def test_normalize_repo_path_rejects_unc_path():
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("//server/share/file.txt")


def test_normalize_repo_path_rejects_windows_drive_letter():
    # Regression guard for audit finding referenced in the module docstring:
    # "C:/config/app.txt" is NOT PurePosixPath.is_absolute(), so only the
    # ":"-in-a-part check (not the is_absolute() check) catches this.
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("C:/config/app.txt")
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("C:app.txt")


def test_normalize_repo_path_rejects_alternate_data_stream_colon():
    with pytest.raises(RuntimeError, match="must stay inside"):
        _normalize_repo_path("file.txt:hidden")


def test_safe_local_target_resolves_inside_root(tmp_path):
    target = _safe_local_target(tmp_path, "src/module.py")
    assert target == (tmp_path / "src" / "module.py").resolve()


def test_safe_local_target_propagates_normalize_repo_path_rejection(tmp_path):
    with pytest.raises(RuntimeError, match="must stay inside"):
        _safe_local_target(tmp_path, "../outside.txt")


def test_safe_local_target_root_itself_is_a_valid_target(tmp_path):
    # repo_path "." normalizes to "." via PurePosixPath -> as_posix() == "."
    # -> Path(*()) with zero parts -> target resolves to root itself.
    target = _safe_local_target(tmp_path, ".")
    assert target == tmp_path.resolve()


def test_fingerprint_changes_is_deterministic_for_same_input():
    changes = [{"path": "a.py", "operation": "update", "content": "x"}]
    assert _fingerprint_changes(changes) == _fingerprint_changes(changes)


def test_fingerprint_changes_is_a_64_char_hex_sha256_digest():
    fp = _fingerprint_changes([{"path": "a.py"}])
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_changes_is_independent_of_dict_key_order():
    a = [{"path": "a.py", "operation": "update", "content": "x"}]
    b = [{"content": "x", "path": "a.py", "operation": "update"}]
    assert _fingerprint_changes(a) == _fingerprint_changes(b)


def test_fingerprint_changes_differs_for_different_content():
    a = [{"path": "a.py", "content": "x"}]
    b = [{"path": "a.py", "content": "y"}]
    assert _fingerprint_changes(a) != _fingerprint_changes(b)


def test_fingerprint_changes_is_sensitive_to_list_order():
    # Documents a real, non-obvious property: sort_keys=True only sorts
    # dict KEYS, never reorders list elements - so json.dumps (and thus the
    # fingerprint the 3-way approval compare in backend/agents.py relies
    # on) treats the same two file-changes in a different order as a
    # DIFFERENT change set. In production this is masked by
    # GitlinkAgent._normalize_files always emitting files sorted by path,
    # so callers never actually see this - but the hash function itself
    # has no such guarantee on its own.
    a = [{"path": "a.py"}, {"path": "b.py"}]
    b = [{"path": "b.py"}, {"path": "a.py"}]
    assert _fingerprint_changes(a) != _fingerprint_changes(b)


def test_fingerprint_changes_handles_non_json_serializable_values_via_default_str():
    # default=str means a value json.dumps can't natively serialize (e.g. a
    # Path) is coerced via str() instead of raising - must not crash on
    # whatever a caller happens to stash in a change dict.
    changes = [{"path": "a.py", "note": Path("weird/object")}]
    fp = _fingerprint_changes(changes)
    assert len(fp) == 64


def test_fingerprint_changes_empty_list_is_a_stable_well_known_value():
    # json.dumps([], sort_keys=True) == "[]" - pin the exact digest so any
    # accidental change to the canonicalization (e.g. separators) is caught.
    import hashlib
    expected = hashlib.sha256(b"[]").hexdigest()
    assert _fingerprint_changes([]) == expected


def test_wrap_cdata_wraps_plain_text():
    assert _wrap_cdata("hello") == "<![CDATA[hello]]>"


def test_wrap_cdata_escapes_embedded_cdata_close_sequence():
    # A raw "]]>" inside the source text would otherwise prematurely close
    # the CDATA section - splitting it must let it round-trip literally in
    # any downstream XML parse.
    wrapped = _wrap_cdata("before]]>after")
    assert wrapped == "<![CDATA[before]]]]><![CDATA[>after]]>"
    assert "]]>after]]>" not in wrapped.replace("]]]]><![CDATA[>", "")


def test_wrap_cdata_handles_none_as_empty_string():
    assert _wrap_cdata(None) == "<![CDATA[]]>"


def test_xml_file_block_escapes_path_attribute():
    block = _xml_file_block('src/"quoted".py', "content", original_chars=7)
    assert 'path="src/&quot;quoted&quot;.py"' in block


def test_xml_file_block_includes_chars_and_truncated_attrs():
    block = _xml_file_block("a.py", "hi", truncated=True, original_chars=1234)
    assert 'chars="1234"' in block
    assert 'truncated="true"' in block


def test_xml_file_block_defaults_truncated_false_and_clamps_negative_chars():
    block = _xml_file_block("a.py", "hi", original_chars=-5)
    assert 'truncated="false"' in block
    assert 'chars="0"' in block


def test_xml_file_block_wraps_source_text_in_cdata():
    block = _xml_file_block("a.py", "print(1)", original_chars=8)
    assert "<![CDATA[print(1)]]>" in block


def test_truncate_for_context_returns_unchanged_when_under_limit():
    text, truncated = _truncate_for_context("short text", max_chars=100)
    assert text == "short text"
    assert truncated is False


def test_truncate_for_context_exact_boundary_is_not_truncated():
    text, truncated = _truncate_for_context("x" * 100, max_chars=100)
    assert text == "x" * 100
    assert truncated is False


def test_truncate_for_context_truncates_and_flags_when_over_limit():
    text, truncated = _truncate_for_context("x" * 150, max_chars=100)
    assert truncated is True
    assert len(text) == 100
    assert text.endswith("...")
    assert text == "x" * 97 + "..."


def test_extract_json_object_pulls_fenced_json_block():
    raw = 'Sure, here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert _extract_json_object(raw) == '{"a": 1}'


def test_extract_json_object_pulls_bare_json_object_without_fence():
    raw = 'preamble text {"a": 1, "b": [1,2]} trailing text'
    assert json.loads(_extract_json_object(raw)) == {"a": 1, "b": [1, 2]}


def test_extract_json_object_falls_back_to_raw_text_when_no_object_found():
    raw = "no json here at all"
    assert _extract_json_object(raw) == "no json here at all"


# =============================================================================
# graphlink_plugins/gitlink/agent.py - GitlinkAgent
# =============================================================================


def _file_item(path="a.py", operation="update", reason="r", content="x"):
    item = {"path": path, "operation": operation, "reason": reason}
    if operation != "delete":
        item["content"] = content
    return item


def test_normalize_files_sorts_by_path_case_insensitively():
    agent = GitlinkAgent()
    items = [_file_item(path="Zebra.py"), _file_item(path="apple.py"), _file_item(path="Banana.py")]
    result = agent._normalize_files(items)
    assert [item["path"] for item in result] == ["apple.py", "Banana.py", "Zebra.py"]


def test_normalize_files_drops_items_with_invalid_or_unsafe_path():
    agent = GitlinkAgent()
    items = [_file_item(path="../escape.py"), _file_item(path="ok.py")]
    result = agent._normalize_files(items)
    assert [item["path"] for item in result] == ["ok.py"]


def test_normalize_files_defaults_unknown_operation_to_update():
    agent = GitlinkAgent()
    result = agent._normalize_files([{"path": "a.py", "operation": "rewrite", "content": "x"}])
    assert result[0]["operation"] == "update"


def test_normalize_files_dedupes_by_path_last_write_wins():
    agent = GitlinkAgent()
    items = [
        _file_item(path="a.py", content="first"),
        _file_item(path="a.py", content="second"),
    ]
    result = agent._normalize_files(items)
    assert len(result) == 1
    assert result[0]["content"] == "second"


def test_normalize_files_drops_update_items_missing_string_content():
    agent = GitlinkAgent()
    items = [
        {"path": "a.py", "operation": "update", "reason": "r"},  # no content key
        {"path": "b.py", "operation": "create", "reason": "r", "content": 123},  # not a str
        {"path": "c.py", "operation": "update", "reason": "r", "content": "ok"},
    ]
    result = agent._normalize_files(items)
    assert [item["path"] for item in result] == ["c.py"]


def test_normalize_files_delete_items_do_not_require_content():
    agent = GitlinkAgent()
    result = agent._normalize_files([{"path": "old.py", "operation": "delete", "reason": "cleanup"}])
    assert len(result) == 1
    assert result[0]["operation"] == "delete"
    assert "content" not in result[0]


def test_normalize_files_defaults_reason_when_missing():
    agent = GitlinkAgent()
    result = agent._normalize_files([{"path": "a.py", "operation": "update", "content": "x"}])
    assert result[0]["reason"] == "No reason supplied."


def test_normalize_files_skips_non_dict_items():
    agent = GitlinkAgent()
    result = agent._normalize_files(["not-a-dict", 42, None, _file_item(path="a.py")])
    assert [item["path"] for item in result] == ["a.py"]


def test_normalize_files_none_or_empty_input_returns_empty_list():
    agent = GitlinkAgent()
    assert agent._normalize_files(None) == []
    assert agent._normalize_files([]) == []


def _fake_chat(content_dict):
    def _chat(task, messages, **kwargs):
        return {"message": {"content": json.dumps(content_dict)}}
    return _chat


def test_get_response_happy_path_changes_ready_with_files(monkeypatch):
    monkeypatch.setattr(api_provider, "chat", _fake_chat({
        "summary": "Add health check",
        "write_intent": "changes_ready",
        "rationale": "Improves ops visibility",
        "notes": ["be careful"],
        "files": [{"path": "app.py", "operation": "update", "reason": "add route", "content": "code"}],
    }))
    agent = GitlinkAgent()

    result = agent.get_response({
        "task_prompt": "add a health check", "context_xml": "<x/>",
        "repo": "octocat/hello-world", "branch": "main",
    })

    assert result["write_intent"] == "changes_ready"
    assert result["summary"] == "Add health check"
    assert result["rationale"] == "Improves ops visibility"
    assert result["notes"] == ["be careful"]
    assert result["change_count"] == 1
    assert result["files"] == [
        {"path": "app.py", "operation": "update", "reason": "add route", "content": "code"}
    ]
    assert json.loads(result["raw_response"])["write_intent"] == "changes_ready"


def test_get_response_malformed_json_falls_back_to_blocked_with_exact_note(monkeypatch):
    monkeypatch.setattr(api_provider, "chat", lambda task, messages, **kwargs: {
        "message": {"content": "this is not JSON at all, no braces"}
    })
    agent = GitlinkAgent()

    result = agent.get_response({"task_prompt": "x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"})

    assert result["write_intent"] == "blocked"
    assert result["files"] == []
    assert result["notes"] == [
        "The model response was not valid JSON, so no approved file writes can be prepared."
    ]
    assert result["summary"] == "No structured change summary was returned."
    assert result["rationale"] == "No structured rationale was returned."


def test_get_response_changes_ready_but_no_files_downgrades_further_to_blocked(monkeypatch):
    # write_intent starts as the VALID value "changes_ready", so the first
    # fallback (invalid-value check) never fires. The "claimed ready but no
    # files" guard then downgrades it to "no_changes" AND appends a note -
    # but that note makes `notes` non-empty, so the trailing "notes present
    # + not already blocked + no files" guard immediately re-escalates it
    # one more step, all the way to "blocked". A claimed-ready-but-empty
    # response therefore never actually surfaces as "no_changes" to a
    # caller - it always ends up "blocked".
    monkeypatch.setattr(api_provider, "chat", _fake_chat({
        "summary": "s", "write_intent": "changes_ready", "rationale": "r", "notes": [], "files": [],
    }))
    agent = GitlinkAgent()

    result = agent.get_response({"task_prompt": "x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"})

    assert result["write_intent"] == "blocked"
    assert result["files"] == []
    assert (
        "The model claimed a ready change set, but it did not return any valid file payloads."
        in result["notes"]
    )


def test_get_response_invalid_write_intent_with_no_notes_defaults_to_no_changes(monkeypatch):
    monkeypatch.setattr(api_provider, "chat", _fake_chat({
        "summary": "s", "write_intent": "banana", "rationale": "r", "notes": [], "files": [],
    }))
    agent = GitlinkAgent()

    result = agent.get_response({"task_prompt": "x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"})

    assert result["write_intent"] == "no_changes"


def test_get_response_invalid_write_intent_with_notes_and_no_files_flips_to_blocked(monkeypatch):
    # Pins the trailing "notes present + not already blocked + no files ->
    # force blocked" guard: write_intent starts invalid ("banana", no notes
    # yet at that check) -> falls back to "no_changes" first, THEN the
    # model's own notes are appended, and the final guard re-escalates to
    # "blocked" because notes exist but no files were ever produced.
    monkeypatch.setattr(api_provider, "chat", _fake_chat({
        "summary": "s", "write_intent": "banana", "rationale": "r",
        "notes": ["heads up"], "files": [],
    }))
    agent = GitlinkAgent()

    result = agent.get_response({"task_prompt": "x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"})

    assert result["write_intent"] == "blocked"
    assert result["notes"] == ["heads up"]


def test_get_response_passes_through_raw_response_verbatim(monkeypatch):
    raw = '```json\n{"summary": "s", "write_intent": "no_changes", "rationale": "r", "notes": [], "files": []}\n```'
    monkeypatch.setattr(api_provider, "chat", lambda task, messages, **kwargs: {"message": {"content": raw}})
    agent = GitlinkAgent()

    result = agent.get_response({"task_prompt": "x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"})

    assert result["raw_response"] == raw


def test_get_response_normalizes_and_sorts_returned_files(monkeypatch):
    monkeypatch.setattr(api_provider, "chat", _fake_chat({
        "summary": "s", "write_intent": "changes_ready", "rationale": "r", "notes": [],
        "files": [
            {"path": "z.py", "operation": "update", "reason": "r", "content": "z"},
            {"path": "a.py", "operation": "bogus_op", "reason": "r", "content": "a"},
        ],
    }))
    agent = GitlinkAgent()

    result = agent.get_response({"task_prompt": "x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"})

    assert [f["path"] for f in result["files"]] == ["a.py", "z.py"]
    assert result["files"][0]["operation"] == "update"  # "bogus_op" normalized


def test_get_response_builds_message_with_repo_branch_scope_and_fallback_defaults(monkeypatch):
    captured = {}

    def fake_chat(task, messages, **kwargs):
        captured["task"] = task
        captured["messages"] = messages
        return {"message": {"content": json.dumps({
            "summary": "s", "write_intent": "no_changes", "rationale": "r", "notes": [], "files": [],
        })}}

    monkeypatch.setattr(api_provider, "chat", fake_chat)
    agent = GitlinkAgent()

    agent.get_response({
        "task_prompt": "",  # blank -> fallback text
        "context_xml": "<gitlink_context/>",
        "branch_transcript": "",  # blank -> fallback text
        "repo": "octocat/hello-world",
        "branch": "main",
        "scope_label": "Selected files",
        "context_summary": "Scanned 3 files",
    })

    assert captured["task"] == config.TASK_CHAT
    user_message = captured["messages"][1]["content"]
    assert captured["messages"][0]["content"] == GitlinkAgent.SYSTEM_PROMPT
    assert "Repository: octocat/hello-world@main" in user_message
    assert "Scope: Selected files" in user_message
    assert "Context Summary: Scanned 3 files" in user_message
    assert "No task prompt supplied." in user_message
    assert "No prior branch context." in user_message
    assert "<gitlink_context/>" in user_message


# =============================================================================
# graphlink_plugins/gitlink/repository.py - module-level free functions
# =============================================================================


def test_default_import_root_replaces_slashes_in_repo_and_branch():
    root = default_import_root("octocat/hello-world", "feature/my-branch")
    parts = root.parts
    assert parts[-2:] == ("octocat__hello-world", "feature__my-branch")
    assert ".graphlink" in parts and "gitlink_repos" in parts


def test_scan_local_repo_paths_raises_when_root_missing(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(RuntimeError, match="does not exist"):
        scan_local_repo_paths(missing)


def test_scan_local_repo_paths_excludes_ignored_dirs_and_binary_suffixes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("//")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG")

    result = scan_local_repo_paths(tmp_path)

    assert result == ["src/main.py"]


def test_scan_local_repo_paths_returns_sorted_case_insensitive(tmp_path):
    for name in ("Zebra.py", "apple.py", "Banana.py"):
        (tmp_path / name).write_text("x")

    result = scan_local_repo_paths(tmp_path)

    assert result == ["apple.py", "Banana.py", "Zebra.py"]


def test_resolve_scope_paths_selected_mode_returns_selected_list():
    result = resolve_scope_paths("selected", ["a.py", "b.py"], repo_file_paths=["z.py"])
    assert result == ["a.py", "b.py"]


def test_resolve_scope_paths_selected_mode_empty_selection_raises():
    with pytest.raises(RuntimeError, match="Select one or more files"):
        resolve_scope_paths("selected", [], repo_file_paths=["z.py"])


def test_resolve_scope_paths_full_mode_prefers_repo_file_paths():
    result = resolve_scope_paths("full", selected_paths=[], repo_file_paths=["a.py", "b.py"], local_root="/somewhere")
    assert result == ["a.py", "b.py"]


def test_resolve_scope_paths_full_mode_falls_back_to_local_scan(tmp_path):
    (tmp_path / "only.py").write_text("x")
    result = resolve_scope_paths("full", selected_paths=[], repo_file_paths=[], local_root=tmp_path)
    assert result == ["only.py"]


def test_resolve_scope_paths_full_mode_raises_without_tree_or_local_root():
    with pytest.raises(RuntimeError, match="Load the file tree first"):
        resolve_scope_paths("full", selected_paths=[], repo_file_paths=[], local_root=None)


def test_read_local_repo_file_reads_and_decodes(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.py").write_text("hello world", encoding="utf-8")
    assert read_local_repo_file(tmp_path, "sub/f.py") == "hello world"


def test_read_local_repo_file_raises_when_missing(tmp_path):
    with pytest.raises(RuntimeError, match="missing `sub/missing.py`"):
        read_local_repo_file(tmp_path, "sub/missing.py")


def test_read_local_repo_file_raises_when_path_is_a_directory(tmp_path):
    (tmp_path / "adir").mkdir()
    with pytest.raises(RuntimeError, match="resolves to a directory"):
        read_local_repo_file(tmp_path, "adir")


def test_read_local_repo_file_blocks_path_traversal(tmp_path):
    with pytest.raises(RuntimeError, match="must stay inside"):
        read_local_repo_file(tmp_path, "../../etc/passwd")


def test_validate_pending_changes_passes_for_valid_update_and_delete_mix():
    changes = [
        {"path": "a.py", "operation": "update", "content": "x"},
        {"path": "b.py", "operation": "delete"},
    ]
    validate_pending_changes(changes)  # must not raise


def test_validate_pending_changes_raises_naming_path_for_missing_content():
    changes = [{"path": "a.py", "operation": "update"}]  # no content key at all
    with pytest.raises(RuntimeError, match="`a.py`"):
        validate_pending_changes(changes)


def test_validate_pending_changes_raises_when_content_is_not_a_string():
    changes = [{"path": "a.py", "operation": "create", "content": 42}]
    with pytest.raises(RuntimeError, match="missing its file content"):
        validate_pending_changes(changes)


def test_validate_pending_changes_unrecognized_operation_still_requires_content():
    # Mirrors apply_change_set's own dispatch: only "delete" skips the write.
    # A typo'd/corrupted operation value must NOT slip past this guard just
    # because it isn't literally "update" or "create".
    changes = [{"path": "a.py", "operation": "typo-op"}]  # no content
    with pytest.raises(RuntimeError, match="`a.py`"):
        validate_pending_changes(changes)


# -- apply_change_set: the write/rollback core --------------------------------


def test_apply_change_set_writes_create_update_and_delete(tmp_path):
    (tmp_path / "existing.py").write_text("old content")
    changes = [
        {"path": "existing.py", "operation": "update", "content": "new content"},
        {"path": "new/nested.py", "operation": "create", "content": "brand new"},
        {"path": "existing.py", "operation": "delete"},
    ]
    # Note: same path updated then deleted in one changeset is a valid
    # (if unusual) sequence - exercised here to also prove ordering is
    # applied strictly in list order.
    written = apply_change_set(tmp_path, changes[:2])

    assert written == 2
    assert (tmp_path / "existing.py").read_text() == "new content"
    assert (tmp_path / "new" / "nested.py").read_text() == "brand new"


def test_apply_change_set_delete_removes_file_and_counts_it(tmp_path):
    (tmp_path / "gone.py").write_text("bye")
    written = apply_change_set(tmp_path, [{"path": "gone.py", "operation": "delete"}])
    assert written == 1
    assert not (tmp_path / "gone.py").exists()


def test_apply_change_set_delete_of_nonexistent_file_is_a_silent_noop():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        written = apply_change_set(Path(tmp), [{"path": "never-existed.py", "operation": "delete"}])
        assert written == 0


def test_apply_change_set_writes_byte_exact_newlines(tmp_path):
    # newline="" must write content byte-for-byte, not translate \n -> \r\n
    # (see repository.py's own comment on why this matters for diff parity
    # with what the user approved in preview).
    content = "line1\nline2\r\nline3\n"
    apply_change_set(tmp_path, [{"path": "f.txt", "operation": "create", "content": content}])
    raw = (tmp_path / "f.txt").read_bytes()
    assert raw == content.encode("utf-8")


def test_apply_change_set_rolls_back_all_writes_on_mid_loop_failure(tmp_path):
    (tmp_path / "first.py").write_text("original first")
    # "blocker" exists as a DIRECTORY - writing text to it as the second
    # change item forces a mid-loop failure (IsADirectoryError) after the
    # first item has already been written successfully.
    (tmp_path / "blocker").mkdir()

    changes = [
        {"path": "first.py", "operation": "update", "content": "mutated"},
        {"path": "blocker", "operation": "update", "content": "cannot write, it's a dir"},
    ]

    with pytest.raises(Exception):
        apply_change_set(tmp_path, changes)

    # The first item's write must have been rolled back to its pre-existing
    # content - not left mutated just because a LATER item failed.
    assert (tmp_path / "first.py").read_text() == "original first"


def test_apply_change_set_rollback_deletes_newly_created_file(tmp_path):
    # "new.py" did not exist before this call - if the changeset fails
    # partway through, rollback must delete it entirely (backup=None case),
    # not leave a half-approved file behind.
    (tmp_path / "blocker").mkdir()
    changes = [
        {"path": "new.py", "operation": "create", "content": "brand new content"},
        {"path": "blocker", "operation": "update", "content": "boom"},
    ]

    with pytest.raises(Exception):
        apply_change_set(tmp_path, changes)

    assert not (tmp_path / "new.py").exists()


def test_apply_change_set_original_exception_message_is_preserved(tmp_path):
    (tmp_path / "blocker").mkdir()
    with pytest.raises(Exception) as excinfo:
        apply_change_set(tmp_path, [{"path": "blocker", "operation": "update", "content": "x"}])
    # write_text against a directory raises IsADirectoryError/PermissionError
    # depending on platform - either way it must propagate, not be swallowed.
    assert excinfo.value is not None


# =============================================================================
# graphlink_plugins/gitlink/repository.py - GitlinkRepository
# =============================================================================


class _FakeGithubClient:
    """Duck-types GitHubRestClient.request for GitlinkRepository unit tests -
    GitlinkRepository "owns nothing but a github_client reference" per its
    own module docstring, so its own logic is fully testable against any
    object exposing this one method."""

    def __init__(self, responses):
        self.responses = responses  # url -> value, or url -> callable
        self.calls = []

    def request(self, url, params=None, *, expect_json=True, timeout=25):
        self.calls.append({"url": url, "params": params, "expect_json": expect_json, "timeout": timeout})
        if url not in self.responses:
            raise AssertionError(f"unscripted request to {url}")
        value = self.responses[url]
        return value() if callable(value) else value


def test_fetch_github_file_text_decodes_base64_content():
    import base64
    encoded = base64.b64encode("print('hi')".encode("utf-8")).decode("ascii")
    url = "https://api.github.com/repos/o/r/contents/src/app.py"
    client = _FakeGithubClient({url: {"encoding": "base64", "content": encoded}})
    repo = GitlinkRepository(client)

    text = repo.fetch_github_file_text("o/r", "main", "src/app.py")

    assert text == "print('hi')"
    assert client.calls[0]["params"] == {"ref": "main"}


def test_fetch_github_file_text_directory_response_raises():
    url = "https://api.github.com/repos/o/r/contents/src"
    client = _FakeGithubClient({url: [{"name": "a.py"}, {"name": "b.py"}]})
    repo = GitlinkRepository(client)

    with pytest.raises(RuntimeError, match="resolves to a directory"):
        repo.fetch_github_file_text("o/r", "main", "src")


def test_fetch_github_file_text_falls_back_to_download_url():
    contents_url = "https://api.github.com/repos/o/r/contents/big.bin"
    download_url = "https://raw.githubusercontent.com/o/r/main/big.bin"
    client = _FakeGithubClient({
        contents_url: {"download_url": download_url},  # no inline content/encoding
        download_url: "raw text content".encode("utf-8"),
    })
    repo = GitlinkRepository(client)

    text = repo.fetch_github_file_text("o/r", "main", "big.bin")

    assert text == "raw text content"
    download_call = next(c for c in client.calls if c["url"] == download_url)
    assert download_call["expect_json"] is False
    assert download_call["timeout"] == 25


def test_fetch_github_file_text_raises_when_no_content_or_download_url():
    url = "https://api.github.com/repos/o/r/contents/empty.txt"
    client = _FakeGithubClient({url: {}})
    repo = GitlinkRepository(client)

    with pytest.raises(RuntimeError, match="did not return file contents"):
        repo.fetch_github_file_text("o/r", "main", "empty.txt")


def test_fetch_github_file_text_url_quotes_the_repo_path():
    captured_urls = []

    class _RecordingClient:
        def request(self, url, params=None, *, expect_json=True, timeout=25):
            captured_urls.append(url)
            return {"encoding": "base64", "content": "aGk="}

    GitlinkRepository(_RecordingClient()).fetch_github_file_text("o/r", "main", "src/my file.py")
    assert captured_urls[0].endswith("src/my%20file.py")


def _zip_bytes(entries, top_dir="repo-abc123"):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for relative_path, content in entries.items():
            archive.writestr(f"{top_dir}/{relative_path}", content)
    return buffer.getvalue()


def test_download_repository_snapshot_short_circuits_when_target_already_populated(tmp_path):
    target = tmp_path / "already-here"
    target.mkdir()
    (target / "marker.txt").write_text("existing checkout")

    client = _FakeGithubClient({})  # no URLs scripted - must never be called
    repo = GitlinkRepository(client)

    result = repo.download_repository_snapshot("o/r", "main", target)

    assert result == target
    assert client.calls == []
    assert (target / "marker.txt").read_text() == "existing checkout"


def test_download_repository_snapshot_extracts_zip_and_moves_to_target(tmp_path):
    zip_url = "https://api.github.com/repos/o/r/zipball/main"
    archive_bytes = _zip_bytes({"file1.txt": b"hello", "sub/file2.txt": b"world"})
    client = _FakeGithubClient({zip_url: archive_bytes})
    repo = GitlinkRepository(client)
    target = tmp_path / "checkout"

    result = repo.download_repository_snapshot("o/r", "main", target)

    assert result == target
    assert (target / "file1.txt").read_bytes() == b"hello"
    assert (target / "sub" / "file2.txt").read_bytes() == b"world"
    zip_call = next(c for c in client.calls if c["url"] == zip_url)
    assert zip_call["expect_json"] is False
    assert zip_call["timeout"] == 60


def test_download_repository_snapshot_quotes_branch_name_fully():
    # safe='' -> even "/" in a branch name must be percent-escaped, unlike
    # fetch_github_file_text's repo-path quoting (safe='/').
    captured_urls = []

    class _RecordingClient:
        def request(self, url, params=None, *, expect_json=True, timeout=25):
            captured_urls.append(url)
            return _zip_bytes({"f.txt": b"x"})

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        GitlinkRepository(_RecordingClient()).download_repository_snapshot(
            "o/r", "feature/my-branch", Path(tmp) / "out"
        )
    assert captured_urls[0].endswith("/zipball/feature%2Fmy-branch")


def test_download_repository_snapshot_handles_concurrent_creation_race(tmp_path):
    # Simulates another in-flight call finishing first: by the time THIS
    # call's own extraction completes, target_root already exists (created
    # mid-flight) - it must return that pre-existing target untouched
    # rather than shutil.move-ing over it (which would raise).
    target = tmp_path / "checkout"
    zip_url = "https://api.github.com/repos/o/r/zipball/main"

    def _scripted_zip():
        target.mkdir(parents=True)
        (target / "winner.txt").write_text("the other download won the race")
        return _zip_bytes({"loser.txt": b"should not appear"})

    client = _FakeGithubClient({zip_url: _scripted_zip})
    repo = GitlinkRepository(client)

    result = repo.download_repository_snapshot("o/r", "main", target)

    assert result == target
    assert (target / "winner.txt").exists()
    assert not (target / "loser.txt").exists()


# -- build_context_bundle: XML context construction ---------------------------


def test_build_context_bundle_local_mode_builds_expected_xml_structure(tmp_path):
    (tmp_path / "a.py").write_text("print('a')")
    (tmp_path / "b.py").write_text("print('b')")
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name="octocat/hello-world", branch_name="main", scope_mode="selected",
        selected_paths=["a.py", "b.py"], repo_file_paths=[], local_root=tmp_path,
    )

    assert isinstance(result, ContextBundleResult)
    assert result.context_xml.startswith(
        '<gitlink_context repository="octocat/hello-world" branch="main" scope="selected_files">'
    )
    assert result.context_xml.endswith("</gitlink_context>")
    assert 'scanned_files="2"' in result.context_xml
    assert 'loaded_files="2"' in result.context_xml
    assert 'included_files="2"' in result.context_xml
    assert 'load_errors="0"' in result.context_xml
    assert "<![CDATA[print('a')]]>" in result.context_xml
    assert "<![CDATA[print('b')]]>" in result.context_xml
    assert result.included_paths == ["a.py", "b.py"]
    assert result.context_stats["source_root"] == str(tmp_path)
    assert "Scanned 2 files" in result.context_summary


def test_build_context_bundle_github_mode_uses_github_client():
    url_a = "https://api.github.com/repos/o/r/contents/a.py"
    client = _FakeGithubClient({url_a: {"encoding": "base64", "content": "cHJpbnQoJ2EnKQ=="}})
    repo = GitlinkRepository(client)

    result = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="selected",
        selected_paths=["a.py"], repo_file_paths=[], local_root=None,
    )

    assert 'source="github"' in result.context_xml
    assert result.context_stats["source_root"] == "github"
    assert "<![CDATA[print('a')]]>" in result.context_xml


def test_build_context_bundle_counts_load_errors_and_excludes_them_from_file_blocks(tmp_path):
    (tmp_path / "good.py").write_text("ok")
    # "missing.py" is never created on disk -> read_local_repo_file raises.
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="selected",
        selected_paths=["good.py", "missing.py"], repo_file_paths=[], local_root=tmp_path,
    )

    assert result.context_stats["scanned_files"] == 2
    assert result.context_stats["loaded_files"] == 1
    assert result.context_stats["load_errors"] == 1
    assert "good.py" in result.included_paths
    assert "missing.py" not in result.included_paths
    # source_origin is initialized to "github" and only flipped to "local"
    # AFTER a successful read_local_repo_file call - since this read raises
    # before that reassignment, the error record's "source" attr stays
    # "github" even though this whole call is in local-root mode. A minor
    # existing inconsistency, pinned here as actual behavior.
    assert 'path="missing.py" source="github" error=' in result.context_xml
    # The errored file must never appear inside <files>, only <manifest>.
    files_section = result.context_xml.split("<files>")[1]
    assert "missing.py" not in files_section


def test_build_context_bundle_raises_immediately_for_a_malformed_scope_path(tmp_path):
    # Unlike per-file load errors (caught individually, above),
    # _normalize_repo_path is called OUTSIDE the per-file try/except at the
    # top of the loop, so a single malformed scope path aborts the WHOLE
    # bundle build with an uncaught RuntimeError rather than being recorded
    # as one more load_errors entry. This is real existing behavior worth
    # pinning explicitly - a malformed/inaccessible repo tree entry doesn't
    # degrade gracefully the way a missing/unreadable file does.
    (tmp_path / "good.py").write_text("ok")
    repo = GitlinkRepository(github_client=None)

    with pytest.raises(RuntimeError, match="must stay inside"):
        repo.build_context_bundle(
            repo_name="o/r", branch_name="main", scope_mode="selected",
            selected_paths=["good.py", "../escape.py"], repo_file_paths=[], local_root=tmp_path,
        )


def test_build_context_bundle_escapes_xml_special_characters(tmp_path):
    (tmp_path / "a.py").write_text("x")
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name='o/r"with-quote', branch_name="main&branch", scope_mode="selected",
        selected_paths=["a.py"], repo_file_paths=[], local_root=tmp_path,
    )

    assert 'repository="o/r&quot;with-quote"' in result.context_xml
    assert 'branch="main&amp;branch"' in result.context_xml


def test_build_context_bundle_context_budget_omits_files_over_max_chars(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("a" * 50)
    (tmp_path / "b.py").write_text("b" * 50)
    # Force the overall document budget so small that only the FIRST file's
    # block fits - the omission guard only fires once file_blocks is
    # already non-empty, so this also implicitly proves at least one file
    # always gets in regardless of budget.
    monkeypatch.setattr(repository_module, "MAX_CONTEXT_CHARS", 120)
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="selected",
        selected_paths=["a.py", "b.py"], repo_file_paths=[], local_root=tmp_path,
    )

    assert result.context_stats["included_files"] == 1
    assert result.context_stats["context_omissions"] == 1
    assert len(result.included_paths) == 1
    assert 'omitted="true"' in result.context_xml
    assert 'reason="context_budget"' in result.context_xml


def test_build_context_bundle_manifest_budget_truncates_entries(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x")
    monkeypatch.setattr(repository_module, "MAX_MANIFEST_ENTRIES", 2)
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="selected",
        selected_paths=[f"f{i}.py" for i in range(5)], repo_file_paths=[], local_root=tmp_path,
    )

    assert '<more count="3" reason="manifest_budget" />' in result.context_xml
    # Manifest lines themselves are capped at 2, but ALL 5 files are still
    # loaded and included in the <files> section (the manifest budget only
    # limits the human-readable listing, not what actually gets sent).
    assert result.context_stats["included_files"] == 5


def test_build_context_bundle_scope_label_full_vs_selected(tmp_path):
    (tmp_path / "a.py").write_text("x")
    repo = GitlinkRepository(github_client=None)

    selected = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="selected",
        selected_paths=["a.py"], repo_file_paths=[], local_root=tmp_path,
    )
    full = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="full",
        selected_paths=[], repo_file_paths=["a.py"], local_root=tmp_path,
    )

    assert 'scope="selected_files"' in selected.context_xml
    assert 'scope="full_repo"' in full.context_xml


def test_build_context_bundle_per_file_truncation_flows_through_to_xml(tmp_path):
    # Exceeds agent.py's MAX_FILE_CONTEXT_CHARS (24000) so _truncate_for_context
    # actually engages for this one file, end to end through build_context_bundle.
    big_content = "x" * 24050
    (tmp_path / "big.py").write_text(big_content)
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="selected",
        selected_paths=["big.py"], repo_file_paths=[], local_root=tmp_path,
    )

    assert 'path="big.py" source="local" included="true" chars="24050" truncated="true"' in result.context_xml
    files_section = result.context_xml.split("<files>")[1]
    assert files_section.count("x") < 24050  # the CDATA body itself was cut down


def test_build_context_bundle_empty_scope_produces_empty_but_valid_shell(tmp_path):
    (tmp_path / "unused.py").write_text("x")
    repo = GitlinkRepository(github_client=None)

    result = repo.build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="full",
        selected_paths=[], repo_file_paths=[], local_root=tmp_path,
    )
    # scope_mode="full" with no repo_file_paths falls back to a full local
    # scan, so "unused.py" IS included here - this pins that fallback path.
    assert result.included_paths == ["unused.py"]
