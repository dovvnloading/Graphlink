"""The harness transcript (PLAN-2026-08-24 §2.6/§3.2.4): one append-only
JSONL file per harness node, living INSIDE the node's workspace directory
(decision #4: GC/ownership come for free with the workspace; deleting the
node deletes its history as one unit).

The transcript - not HarnessState - is the source of truth for
conversation history: the node state carries only status/summary surface
the canvas renders, so a long conversation never bloats session.dat or the
scene wire the way serializing history into the graph would (the Py-Coder
serializer-branch mistake this subsystem exists to retire).

Line shapes (one JSON object per line):
  {"t": "meta", "v": 1, "workspaceId": ...}          - first line, once
  {"t": "msg", "role": ..., "content": ..., ...}     - one history message
Unknown "t" values and corrupt lines are skipped on load, never fatal -
the same tolerant-restore posture every session_load restorer takes with
on-disk input this app cannot guarantee it wrote itself.
"""

from __future__ import annotations

import json
from pathlib import Path

TRANSCRIPT_FILENAME = "transcript.jsonl"

# Reload bound: the tail of history a resumed run rebuilds its context
# from. A bound, not the whole file - the file is unbounded by design
# (append-only record), the model's context is not. Compaction (H3)
# replaces this crude tail with a real summarize-the-middle pass.
MAX_RELOADED_MESSAGES = 200

# Per-message content cap on RELOAD only (writes are already bounded by the
# tool-result caps upstream): a hand-edited or foreign file must not be
# able to flood a fresh context through one giant line.
_RELOAD_CONTENT_CAP = 20_000


def transcript_path(workspace: Path) -> Path:
    return workspace / TRANSCRIPT_FILENAME


def append_message(workspace: Path, message: dict) -> None:
    """Append one history message. Best-effort durability posture: an
    append that fails raises to the caller (the loop treats a transcript
    it cannot write as a run-fatal fault - a harness whose record silently
    diverges from what actually happened is worse than one that stops)."""
    path = transcript_path(workspace)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as fh:
        if is_new:
            fh.write(json.dumps({"t": "meta", "v": 1}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"t": "msg", **message}, ensure_ascii=False) + "\n")


def load_messages(workspace: Path) -> list[dict]:
    """The reload path: the last MAX_RELOADED_MESSAGES message lines, each
    content-capped, in file order. A missing file is an empty history (a
    fresh node), not an error."""
    path = transcript_path(workspace)
    if not path.exists():
        return []
    messages: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict) or item.get("t") != "msg":
                    continue
                role = str(item.get("role") or "")
                if role not in ("user", "assistant", "tool", "system"):
                    continue
                message = {k: v for k, v in item.items() if k != "t"}
                content = message.get("content")
                if isinstance(content, str) and len(content) > _RELOAD_CONTENT_CAP:
                    message["content"] = content[:_RELOAD_CONTENT_CAP] + "…[truncated on reload]"
                messages.append(message)
    except OSError:
        return []
    if len(messages) > MAX_RELOADED_MESSAGES:
        messages = messages[-MAX_RELOADED_MESSAGES:]
    # A tail cut mid tool-sequence would violate the role-alternation
    # invariant providers enforce (an orphaned tool result with no
    # assistant tool_calls turn before it) - drop leading tool messages
    # until the tail starts on a user/assistant boundary.
    while messages and messages[0].get("role") == "tool":
        messages.pop(0)
    return messages
