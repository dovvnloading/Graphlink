"""Gitlink's Qt-free path-safety helpers, XML context formatting, and GitlinkAgent
(the LLM change-proposal agent), extracted out of graphlink_plugin_gitlink.py.

_normalize_repo_path / _safe_local_target are the security boundary between an
LLM-proposed file path and a write to the user's local disk - keeping them Qt-free and
directly importable is what lets tests/test_gitlink_path_safety.py exercise them without
any Qt widget or GUI application object.

GitlinkNode's build_context_bundle/_resolve_scope_paths/_scan_local_repo_paths
deliberately did NOT move here even though they were the original target of this
extraction: they are GitlinkNode instance methods entangled with widget state (the local
root text field, the scope combo box, get_selected_paths(), the settings-manager-gated
GitHub client, and on-disk repository snapshot downloads). Turning them into free
functions would mean inventing a new parameter-passing contract that doesn't exist in
the code today - a redesign, not a refactor - and this is exactly the kind of
security-sensitive, write-adjacent code where a subtly wrong redesign is a real risk
rather than a mechanical gain. They stay as GitlinkNode methods; this module supplies
the pure pieces they call into (_xml_file_block, _truncate_for_context,
_normalize_repo_path, etc.), all directly unit-testable without any Qt widget or GUI
application object.
"""

import hashlib
import html
import json
import re
from pathlib import Path, PurePosixPath

import api_provider
import graphlink_config as config
from graphlink_plugins.common.llm_json import extract_json_object


TEXT_FILE_EXCLUSION_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".wav", ".ogg", ".mp4", ".mov",
    ".avi", ".webm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".exe", ".dll",
    ".so", ".dylib", ".class", ".jar", ".pyc", ".pyd", ".bin", ".dat", ".db",
)

MAX_FILE_CONTEXT_CHARS = 24000


def _clean_text(value, limit=None):
    text = str(value or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _compact_label_text(text, limit=34):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _decode_text_bytes(raw_bytes):
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _is_repo_text_path(path_text):
    lowered = (path_text or "").lower()
    return not lowered.endswith(TEXT_FILE_EXCLUSION_SUFFIXES)


def _normalize_repo_path(path_text):
    raw_path = (path_text or "").strip().replace("\\", "/")
    raw_path = raw_path.lstrip("/")
    if not raw_path:
        raise RuntimeError("Repository file path cannot be empty.")

    normalized = PurePosixPath(raw_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise RuntimeError("Repository paths must stay inside the selected repository.")
    return normalized.as_posix()


def _safe_local_target(root_path, repo_path):
    root = Path(root_path).expanduser().resolve()
    target = (root / Path(*PurePosixPath(_normalize_repo_path(repo_path)).parts)).resolve()
    if target != root and root not in target.parents:
        raise RuntimeError("Resolved file path escaped the selected repository root.")
    return target


def _fingerprint_changes(changes):
    canonical = json.dumps(changes, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _wrap_cdata(text):
    return "<![CDATA[" + str(text or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _xml_file_block(path_text, source_text, *, truncated=False, original_chars=0):
    attrs = [
        f'path="{html.escape(path_text, quote=True)}"',
        f'chars="{max(0, int(original_chars))}"',
        f'truncated="{str(bool(truncated)).lower()}"',
    ]
    return f"    <file {' '.join(attrs)}>\n      {_wrap_cdata(source_text)}\n    </file>"


def _truncate_for_context(source_text, max_chars=MAX_FILE_CONTEXT_CHARS):
    text = source_text or ""
    if len(text) <= max_chars:
        return text, False
    return text[: max_chars - 3].rstrip() + "...", True


def _extract_json_object(raw_text):
    # Delegates to the regex shared with the other JSON-returning plugin agents - kept
    # as a wrapper so this function's existing call site is unchanged.
    return extract_json_object(raw_text)


class GitlinkAgent:
    SYSTEM_PROMPT = """
You are Graphlink's Gitlink codebase operator.

You receive a repository snapshot packaged as XML plus a task brief. Your job is to produce a disciplined file-level change set that can be previewed and only written after explicit user approval.

Rules:
1. Treat the XML as the authoritative repo context. Respect file paths and repository boundaries.
2. Do not invent files that are not necessary for the task.
3. Prefer the smallest coherent change set that satisfies the request.
4. When you update or create a file, return the full file contents for that file.
5. Use only repository-relative paths.
6. Never include markdown fences or commentary outside the JSON object.
7. If the repository context looks truncated, mention the uncertainty in `notes`.
8. Use `write_intent` values only from: `changes_ready`, `no_changes`, `blocked`.
9. Use `operation` values only from: `update`, `create`, `delete`.

Return exactly this JSON shape:
{
  "summary": "Short summary of the planned change set",
  "write_intent": "changes_ready",
  "rationale": "Why these changes are the right move",
  "notes": [
    "Short implementation note"
  ],
  "files": [
    {
      "path": "src/module.py",
      "operation": "update",
      "reason": "Why this file changes",
      "content": "Full file contents after the change"
    }
  ]
}
"""

    def _normalize_files(self, raw_items):
        normalized_items = []
        seen_paths = {}

        for item in raw_items or []:
            if not isinstance(item, dict):
                continue

            try:
                path_text = _normalize_repo_path(item.get("path", ""))
            except RuntimeError:
                continue

            operation = _clean_text(item.get("operation"), limit=20).lower()
            if operation not in {"update", "create", "delete"}:
                operation = "update"

            normalized_item = {
                "path": path_text,
                "operation": operation,
                "reason": _clean_text(item.get("reason"), limit=240) or "No reason supplied.",
            }

            if operation in {"update", "create"}:
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                normalized_item["content"] = content

            seen_paths[path_text] = normalized_item

        for path_text in sorted(seen_paths, key=str.lower):
            normalized_items.append(seen_paths[path_text])
        return normalized_items

    def get_response(self, payload):
        task_prompt = _clean_text(payload.get("task_prompt"), limit=4000)
        context_xml = payload.get("context_xml", "")
        branch_transcript = _clean_text(payload.get("branch_transcript"), limit=3000)
        repo_name = payload.get("repo", "")
        branch_name = payload.get("branch", "")
        scope_label = payload.get("scope_label", "")
        context_summary = payload.get("context_summary", "")

        message_sections = [
            f"Repository: {repo_name}@{branch_name}",
            f"Scope: {scope_label}",
            f"Context Summary: {context_summary}",
            "Task Brief:",
            task_prompt or "No task prompt supplied.",
            "Branch Transcript:",
            branch_transcript or "No prior branch context.",
            "Repository Context XML:",
            context_xml,
        ]

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(message_sections)},
        ]

        raw_text = api_provider.chat(task=config.TASK_CHAT, messages=messages)["message"]["content"]
        parsed_object = {}
        notes = []

        try:
            parsed_object = json.loads(_extract_json_object(raw_text))
        except json.JSONDecodeError:
            notes.append("The model response was not valid JSON, so no approved file writes can be prepared.")

        summary = _clean_text(parsed_object.get("summary"), limit=500) or "No structured change summary was returned."
        rationale = _clean_text(parsed_object.get("rationale"), limit=1200) or "No structured rationale was returned."
        write_intent = _clean_text(parsed_object.get("write_intent"), limit=20).lower()
        if write_intent not in {"changes_ready", "no_changes", "blocked"}:
            write_intent = "blocked" if notes else "no_changes"

        parsed_notes = parsed_object.get("notes")
        if isinstance(parsed_notes, list):
            for note in parsed_notes:
                cleaned = _clean_text(note, limit=240)
                if cleaned:
                    notes.append(cleaned)

        normalized_files = self._normalize_files(parsed_object.get("files"))
        if not normalized_files and write_intent == "changes_ready":
            write_intent = "no_changes"
            notes.append("The model claimed a ready change set, but it did not return any valid file payloads.")

        if notes and write_intent != "blocked" and not normalized_files:
            write_intent = "blocked"

        return {
            "summary": summary,
            "write_intent": write_intent,
            "rationale": rationale,
            "notes": notes,
            "files": normalized_files,
            "change_count": len(normalized_files),
            "raw_response": raw_text,
        }
