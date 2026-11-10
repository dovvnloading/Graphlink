"""Byte-faithful previews and the reviewed writer, with disposable Git repos.

No review validator, dispatcher or writer is substituted. A wrapping observer
counts actual writes in refusal cases; deterministic proposal fixtures replace
model generation. The shared demo fixture scrubs inherited GIT_* routing.
"""
from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import patch

import pytest

from backend.agents import _build_gitlink_preview_text, _call_gitlink_apply, _fingerprint_changes
from graphlink_plugins.gitlink import apply_transaction
from graphlink_plugins.gitlink.change_review import (
    BASELINE_FIELD,
    REVIEW_FORMAT_FIELD,
    REVIEW_FORMAT_VERSION,
    prepare_change_review,
)
from graphlink_plugins.gitlink.preview import build_change_preview, display_review_path
from tools.gitlink_safety_demo import ReviewSession, make_repo, snapshot


@pytest.fixture
def checkout(tmp_path):
    return make_repo(tmp_path, "checkout")


def _review(root, before, after, operation="update"):
    target = root / "fixture.txt"
    if before is not None:
        target.write_bytes(before)
    proposal = {"path": target.name, "operation": operation, "reason": "deterministic byte fixture"}
    if after is not None:
        proposal["content"] = after.decode("utf-8")
    reviewed, originals = prepare_change_review([proposal], str(root))
    preview = _build_gitlink_preview_text(reviewed, str(root), "local/fixture", "main", original_bytes=originals)
    return target, reviewed, preview


@pytest.mark.parametrize("before,after,expected", [
    (b"alpha\r\n", b"alpha\n", ["Bytes changed: 7 -> 6", "CRLF=1, LF=0, CR=0 -> CRLF=0, LF=1, CR=0", 'Final newline: CRLF -> LF', '-"alpha\\r\\n"', '+"alpha\\n"']),
    (b"alpha\n", b"alpha\r\n", ["Bytes changed: 6 -> 7", "CRLF=0, LF=1, CR=0 -> CRLF=1, LF=0, CR=0", 'Final newline: LF -> CRLF']),
    (b"alpha", b"alpha\n", ["Final newline: none -> LF", '-"alpha"', '+"alpha\\n"']),
    (b"alpha\n", b"alpha", ["Final newline: LF -> none", '-"alpha\\n"', '+"alpha"']),
    (b"alpha", b"alpha\r\n", ["Final newline: none -> CRLF"]),
    (b"alpha\r\n", b"alpha", ["Final newline: CRLF -> none"]),
    (b"alpha\r", b"alpha\n", ["CRLF=0, LF=0, CR=1 -> CRLF=0, LF=1, CR=0", "Final newline: CR -> LF"]),
    (b"a\r\nb\n", b"a\nb\r\n", ["Bytes changed: 5 -> 5", '-"a\\r\\n"', '+"a\\n"', '-"b\\n"', '+"b\\r\\n"']),
    (b"a\r\nb\nc\r", b"a\nb\nc\n", ["CRLF=1, LF=1, CR=1 -> CRLF=0, LF=3, CR=0"]),
    (b"alpha\n", b"\xef\xbb\xbfalpha\n", ["UTF-8 BOM: absent -> present", '+"\\ufeffalpha\\n"']),
    (b"\xef\xbb\xbfalpha\n", b"alpha\n", ["UTF-8 BOM: present -> absent", '-"\\ufeffalpha\\n"']),
    (b" ", b"  ", ['-" "', '+"  "', "Bytes changed: 1 -> 2"]),
    (b"alpha \n", b"alpha\t\n", ['-"alpha \\n"', '+"alpha\\t\\n"']),
    (b"\x00\b\f\v\x1b", b"control", ['-"\\u0000\\b\\f\\u000b\\u001b"']),
    ("left\u202e\u2066\u200b\u2028\u2029".encode(), b"right", ['\\u202e\\u2066\\u200b\\u2028\\u2029']),
    ("café".encode(), "cafe\u0301".encode(), ['-"caf\\u00e9"', '+"cafe\\u0301"']),
    (b"\\n", b"\n", ['-"\\\\n"', '+"\\n"']),
])
def test_every_supported_byte_change_is_visible_and_applies_exactly(checkout, before, after, expected):
    target, reviewed, preview = _review(checkout, before, after)
    for text in expected:
        assert text in preview
    assert "No textual diff available" not in preview
    assert "Identical bytes" not in preview
    assert hashlib.sha256(before).hexdigest() in preview
    assert hashlib.sha256(after).hexdigest() in preview
    assert all(char not in preview for char in ["\r", "\x00", "\x1b", "\ufeff", "\u202e", "\u2066", "\u2028", "\u2029"])
    assert reviewed[0][BASELINE_FIELD] == hashlib.sha256(before).hexdigest()
    assert reviewed[0][REVIEW_FORMAT_FIELD] == REVIEW_FORMAT_VERSION
    assert _call_gitlink_apply(checkout, reviewed) == 1  # real writer positive control
    assert target.read_bytes() == after


@pytest.mark.parametrize("before,after,operation,description", [
    (None, b"", "create", "File creation: absent -> 0 bytes (empty file)."),
    (b"", None, "delete", "File deletion: 0 bytes -> absent (empty file)."),
])
def test_empty_file_existence_changes_are_explicit(checkout, before, after, operation, description):
    target, reviewed, preview = _review(checkout, before, after, operation)
    assert description in preview
    assert "Identical bytes" not in preview
    assert "No textual diff" not in preview
    assert _call_gitlink_apply(checkout, reviewed) == 1
    if after is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == after


@pytest.mark.parametrize("content", [b"", b"same\r\n", b"\xef\xbb\xbf", b" \t\x00", "café".encode()])
def test_identical_bytes_are_distinguished_from_unavailable_or_changed_diff(checkout, content):
    target, reviewed, preview = _review(checkout, content, content)
    assert "Identical bytes: no file-content change." in preview
    assert "Bytes changed" not in preview
    assert "Escaped UTF-8 diff" not in preview
    assert "No textual diff" not in preview
    assert _call_gitlink_apply(checkout, reviewed) == 1
    assert target.read_bytes() == content


@pytest.mark.parametrize("raw", [b"caf\xe9\n", b"\xff\x81", b"\xff\xfea\x00", b"\x00\x00\xfe\xff\x00\x00\x00a"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_non_utf8_originals_refuse_without_guessing_or_writing(checkout, raw, operation):
    target = checkout / "encoded.txt"
    target.write_bytes(raw)
    proposed = [{"path": target.name, "operation": operation, "content": "café\n"}]
    before = snapshot(checkout)
    with patch.object(apply_transaction, "atomic_replace_file", wraps=apply_transaction.atomic_replace_file) as writer:
        with pytest.raises(RuntimeError, match="original bytes are not valid UTF-8"):
            prepare_change_review(proposed, str(checkout))
        assert writer.call_count == 0
    assert snapshot(checkout) == before
    assert target.read_bytes() == raw


def test_invalid_unicode_proposal_refuses_before_any_write(checkout):
    target = checkout / "fixture.txt"
    target.write_bytes(b"unchanged")
    with pytest.raises(RuntimeError, match="proposed content cannot be encoded as UTF-8"):
        prepare_change_review([{"path": target.name, "operation": "update", "content": "\ud800"}], str(checkout))
    assert target.read_bytes() == b"unchanged"


def test_preview_and_baseline_share_one_raw_snapshot_and_external_edit_cannot_apply(checkout, monkeypatch):
    from graphlink_plugins.gitlink import change_review
    target = checkout / "fixture.txt"
    before = b"old\r\n"
    target.write_bytes(before)
    reviewed, originals = prepare_change_review([{"path": target.name, "operation": "update", "content": "old\n"}], str(checkout))
    target.write_bytes(b"independent\n")
    with monkeypatch.context() as scoped:
        scoped.setattr(change_review, "read_review_bytes", lambda *_: pytest.fail("preview re-read disk"))
        preview = _build_gitlink_preview_text(reviewed, str(checkout), "local/fixture", "main", original_bytes=originals)
    assert '-"old\\r\\n"' in preview
    assert "independent" not in preview
    assert hashlib.sha256(before).hexdigest() in preview
    disk = snapshot(checkout)
    with patch.object(apply_transaction, "atomic_replace_file", wraps=apply_transaction.atomic_replace_file) as writer:
        with pytest.raises(RuntimeError, match="changed since the preview"):
            _call_gitlink_apply(checkout, reviewed)
        assert writer.call_count == 0
    assert snapshot(checkout) == disk


def test_saved_old_review_with_valid_hashes_and_matching_fingerprint_refuses_via_intent(checkout):
    session = ReviewSession(checkout)
    for item in session.node.state.gitlink_pending_changes:
        del item[REVIEW_FORMAT_FIELD]
    # A genuine pre-upgrade saved proposal has internally consistent binding,
    # but its old human preview may conceal newline-only changes.
    fingerprint = _fingerprint_changes(session.node.state.gitlink_pending_changes)
    session.node.state.gitlink_change_fingerprint = fingerprint
    session.node.state.gitlink_preview_text = "No textual diff available."
    disk = snapshot(checkout)
    with patch.object(apply_transaction, "atomic_replace_file", wraps=apply_transaction.atomic_replace_file) as writer:
        error = asyncio.run(session.apply(fingerprint))
        assert "predates byte-faithful file previews" in error
        assert writer.call_count == 0
    assert snapshot(checkout) == disk


def test_preview_missing_snapshot_refuses_instead_of_rereading_or_claiming_empty(checkout):
    changes = [{"path": "fixture.txt", "operation": "update", "content": "replacement"}]
    with pytest.raises(RuntimeError, match="Missing captured review bytes"):
        _build_gitlink_preview_text(changes, str(checkout), "local/fixture", "main")
    with pytest.raises(RuntimeError, match="Missing captured review bytes"):
        build_change_preview(changes, original_bytes={})


def test_unbound_preview_is_explicit_and_content_cannot_forge_sections():
    proposed = "\n### other.txt [update]\n````\n<img src=x>\u202e"
    preview = build_change_preview([{"path": "fixture.txt", "operation": "update", "content": proposed}], original_bytes=None)
    assert "original bytes are unavailable" in preview
    assert "Configure a local checkout and regenerate" in preview
    assert "\n### other.txt" not in preview
    assert "\\u202e" in preview
    assert "<img src=x>" in preview  # the UI renders this only as text


@pytest.mark.parametrize("path", ["line\n### forged.txt", "escape\x1b[0m", "bidi\u202etxt", "café.txt", "emoji\U0001f600.txt"])
def test_unusual_paths_are_unambiguously_escaped_in_review_headings(path):
    preview = build_change_preview([{"path": path, "operation": "create", "content": "safe"}], original_bytes={path: None})
    assert preview.startswith("### " + display_review_path(path) + " [create]\n")
    assert "\x1b" not in preview and "\u202e" not in preview
    assert "\n### forged.txt" not in preview
