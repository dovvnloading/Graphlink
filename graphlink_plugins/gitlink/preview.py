"""Byte-faithful, inert text for reviewed Gitlink file changes.

UTF-8 (including an explicitly reported UTF-8 BOM) is the supported encoding.
Do not guess legacy encodings: the writer emits UTF-8, so replacement decoding
could conceal an encoding conversion. Escaped JSON strings retain every line
ending, space and control character in the diff. This is a review presentation,
not a source of authorization; the server-held proposal and hashes still bind
Apply independently.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any


def display_review_path(path: str) -> str:
    """Keep ordinary paths readable; quote/escape controls and non-ASCII text."""
    return path if all(" " <= char <= "~" for char in path) else json.dumps(path, ensure_ascii=True)


def decode_review_utf8(raw: bytes, path: str) -> str:
    """Decode reversibly or refuse; never silently transcode the review baseline."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"Cannot review {display_review_path(path)}: original bytes are not valid UTF-8. "
            "Only UTF-8 (with or without a UTF-8 BOM) is supported for reviewed changes; "
            "no encoding conversion was performed and nothing was written."
        ) from exc


def encode_review_utf8(content: str, path: str) -> bytes:
    try:
        return content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            f"Cannot review {display_review_path(path)}: proposed content cannot be encoded "
            "as UTF-8. Nothing was written."
        ) from exc


def _escaped_lines(text: str) -> list[str]:
    # str.splitlines() recognizes additional Unicode separators and, unless
    # keepends=True, erases exactly the distinctions this preview must retain.
    # Split only CRLF/CR/LF, keep the terminator, then use an injective escaped
    # representation. Quotes make leading/trailing spaces visible; controls,
    # BOM and bidirectional formatting cannot alter how the UI presents text.
    return [json.dumps(line, ensure_ascii=True) for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text) if line]


def _line_endings(raw: bytes | None) -> str:
    if raw is None:
        return "absent file"
    crlf = raw.count(b"\r\n")
    return f"CRLF={crlf}, LF={raw.count(b'\n') - crlf}, CR={raw.count(b'\r') - crlf}"


def _final_newline(raw: bytes | None) -> str:
    if raw is None:
        return "absent file"
    if not raw:
        return "empty file"
    if raw.endswith(b"\r\n"):
        return "CRLF"
    if raw.endswith(b"\n"):
        return "LF"
    if raw.endswith(b"\r"):
        return "CR"
    return "none"


def _bom(raw: bytes | None) -> str:
    if raw is None:
        return "absent file"
    return "present" if raw.startswith(b"\xef\xbb\xbf") else "absent"


def _digest(raw: bytes | None) -> str:
    return "absent file" if raw is None else hashlib.sha256(raw).hexdigest()


def build_change_preview(
    files: list[dict[str, Any]], *, original_bytes: dict[str, bytes | None] | None,
) -> str:
    """Render the same captured bytes that prepare_change_review hashed.

    Missing snapshots are allowed only for an explicitly unbound discovery
    preview. They never establish that the original file is empty or unchanged.
    """
    sections: list[str] = []
    for item in files:
        path = item["path"]
        shown_path = display_review_path(path)
        operation = item.get("operation", "update")
        after = None if operation == "delete" else encode_review_utf8(item["content"], path)
        lines = [f"### {shown_path} [{operation}]", ""]
        if original_bytes is None:
            lines.extend([
                "!! WARNING: original bytes are unavailable. This preview has no local-file baseline.",
                "Configure a local checkout and regenerate the proposal before Apply.",
            ])
            if after is None:
                lines.append("Proposed action: DELETE the file (original content unavailable).")
            else:
                lines.append(f"Proposed UTF-8 content: {len(after)} bytes; SHA-256: {_digest(after)}")
                lines.append("Escaped proposed content (JSON strings, including line endings):")
                lines.extend(_escaped_lines(item["content"]) or ['"" (empty file)'])
            sections.append("\n".join(lines))
            continue
        if path not in original_bytes:
            raise RuntimeError(f"Missing captured review bytes for {shown_path}; regenerate the proposal.")
        before = original_bytes[path]
        before_text = "" if before is None else decode_review_utf8(before, path)
        after_text = "" if after is None else decode_review_utf8(after, path)
        if operation == "create":
            lines.append(f"File creation: absent -> {len(after or b'')} bytes" + (" (empty file)." if after == b"" else "."))
        elif operation == "delete":
            lines.append(f"File deletion: {len(before or b'')} bytes -> absent" + (" (empty file)." if before == b"" else "."))
        elif before == after:
            lines.append("Identical bytes: no file-content change.")
        else:
            lines.append(f"Bytes changed: {len(before or b'')} -> {len(after or b'')} bytes.")
        lines.extend([
            "Encoding: UTF-8; no newline or Unicode normalization.",
            f"UTF-8 BOM: {_bom(before)} -> {_bom(after)}.",
            f"Line endings: {_line_endings(before)} -> {_line_endings(after)}.",
            f"Final newline: {_final_newline(before)} -> {_final_newline(after)}.",
            f"SHA-256 before: {_digest(before)}",
            f"SHA-256 after: {_digest(after)}",
        ])
        diff_lines = list(difflib.unified_diff(
            _escaped_lines(before_text), _escaped_lines(after_text),
            fromfile=f"a/{shown_path}" if before is not None else "/dev/null",
            tofile=f"b/{shown_path}" if after is not None else "/dev/null", lineterm="",
        ))
        if diff_lines:
            lines.extend([
                "", "Escaped UTF-8 diff (JSON string per line; line endings and controls are literal escapes):",
                *diff_lines,
            ])
        # Empty-file create/delete have no content hunks. Their existence
        # change is stated above rather than mislabeled as an unavailable diff.
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
