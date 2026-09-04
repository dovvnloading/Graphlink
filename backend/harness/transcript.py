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
  {"t": "meta", "v": 2, "profile": {...}, "app": ..., "git": {...}}
  {"t": "msg", "role": ..., "content": ..., ...}     - one history message
  {"t": "compact", "content": ...}                   - a compaction record
Unknown "t" values and corrupt lines are skipped on load, never fatal -
the same tolerant-restore posture every session_load restorer takes with
on-disk input this app cannot guarantee it wrote itself.

A compaction record (H3) is what keeps history append-only while still
shrinking context: it does not rewrite or remove the message lines
before it. On load, everything preceding it collapses into the one
message it carries - so a reload reconstructs exactly the post-
compaction state the live run continued from, rather than replaying
turns that run had already dropped. The record stores the RENDERED
message content rather than its ingredients, so what reloads is
verbatim what ran, even if the framing changes in a later version.

THE META LINE (§2.6) records the session PROFILE - which root this
transcript's history was produced against, and whether that root was a
trusted user directory or managed scratch - plus the app version and the
workspace's git context when it has one. `check_profile` is what turns
that record into §3.3's locked-profile invariant: a transcript written
against a user's project folder must never be replayed into a run bound
to scratch (or to a DIFFERENT folder), because every "I read X" and "I
wrote Y" in that history would silently now refer to somewhere else.

THE WRITER (§2.6's "background single-writer with flush barriers") is one
daemon thread per workspace draining a queue, so the turn loop never
blocks on disk. `flush()` is the barrier: it blocks until everything
queued so far is on disk, and the loop calls it before reading history
back and at every terminal transition. If the writer thread ever dies the
append path falls back to writing inline, so durability degrades to the
old synchronous behavior rather than to silent loss.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path

TRANSCRIPT_FILENAME = "transcript.jsonl"

META_VERSION = 2

# Reload bound: the tail of history a resumed run rebuilds its context
# from. A bound, not the whole file - the file is unbounded by design
# (append-only record), the model's context is not. Compaction (H3)
# replaces this crude tail with a real summarize-the-middle pass.
MAX_RELOADED_MESSAGES = 200

# Per-message content cap on RELOAD only (writes are already bounded by the
# tool-result caps upstream): a hand-edited or foreign file must not be
# able to flood a fresh context through one giant line.
_RELOAD_CONTENT_CAP = 20_000

_FLUSH_TIMEOUT_SECONDS = 5.0
_GIT_TIMEOUT_SECONDS = 2.0


def transcript_path(workspace: Path) -> Path:
    return workspace / TRANSCRIPT_FILENAME


# -- the background single-writer -------------------------------------------


class _Writer:
    """One daemon thread + queue per transcript file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name=f"transcript-{path.parent.name}", daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                line, done = item
                try:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(line)
                        fh.flush()
                except OSError:
                    # Swallowed deliberately: a transcript we cannot write is
                    # bad, but killing a live agent run over it is worse, and
                    # the flush barrier below reports staleness by timing out
                    # rather than by exception from a background thread.
                    pass
                if done is not None:
                    done.set()
            finally:
                self._queue.task_done()

    def write(self, line: str) -> None:
        self._queue.put((line, None))

    def flush(self, timeout: float = _FLUSH_TIMEOUT_SECONDS) -> bool:
        """Block until everything queued BEFORE this call is on disk."""
        if not self._thread.is_alive():
            return False
        done = threading.Event()
        self._queue.put(("", done))
        return done.wait(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


_writers: dict[str, _Writer] = {}
_writers_lock = threading.Lock()


def _writer_for(path: Path) -> "_Writer | None":
    key = str(path)
    with _writers_lock:
        writer = _writers.get(key)
        if writer is not None and writer.alive:
            return writer
        try:
            writer = _Writer(path)
        except RuntimeError:
            # Interpreter shutting down - no new threads. Caller writes inline.
            return None
        _writers[key] = writer
        return writer


def _emit(path: Path, payload: dict) -> None:
    """Queue one line, falling back to an inline write when no writer thread
    is available (interpreter shutdown, thread creation refused)."""
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    writer = _writer_for(path)
    if writer is None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return
    writer.write(line)


def flush(workspace: Path, timeout: float = _FLUSH_TIMEOUT_SECONDS) -> bool:
    """The barrier: everything appended so far is on disk when this returns
    True. Called before reading history back and at terminal transitions."""
    path = transcript_path(workspace)
    key = str(path)
    with _writers_lock:
        writer = _writers.get(key)
    if writer is None:
        return True
    return writer.flush(timeout)


def shutdown_writers() -> None:
    """Flush and stop every writer - app shutdown / test teardown."""
    with _writers_lock:
        writers = list(_writers.values())
        _writers.clear()
    for writer in writers:
        writer.flush()


# -- the meta line / session profile ----------------------------------------


def _git_context(root: Path) -> dict:
    """Best-effort branch + short SHA for a workspace that is a git repo.
    Bounded and failure-tolerant: this is provenance for a human reading a
    transcript later, never something a run depends on."""
    def _run(args: list[str]) -> str:
        try:
            done = subprocess.run(
                args, cwd=str(root), capture_output=True, text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not branch:
        return {}
    return {"branch": branch, "commit": _run(["git", "rev-parse", "--short", "HEAD"])}


def build_profile(root: Path, is_user_dir: bool) -> dict:
    """The §3.3 session profile: WHICH root, and under which trust posture."""
    return {"root": str(Path(root).resolve()), "isUserDir": bool(is_user_dir)}


def _meta_payload(profile: dict, root: Path) -> dict:
    try:
        from graphlink_version import __version__ as app_version
    except Exception:
        app_version = ""
    return {
        "t": "meta",
        "v": META_VERSION,
        "profile": profile,
        "app": app_version,
        "created": time.time(),
        "git": _git_context(Path(root)),
    }


def read_meta(workspace: Path) -> "dict | None":
    """The transcript's first line, or None for a file that has none (a
    fresh node, or a v1 transcript written before meta carried a profile)."""
    path = transcript_path(workspace)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    return None
                return item if isinstance(item, dict) and item.get("t") == "meta" else None
    except OSError:
        return None
    return None


def check_profile(workspace: Path, profile: dict) -> "str | None":
    """§3.3's locked-profile invariant. Returns None when this run may
    proceed, or a human-readable refusal when it may not.

    A transcript with no recorded profile (v1, or one this app has not
    written to yet) is adopted rather than refused: refusing would strand
    every history written before this record existed, and the FIRST write
    below stamps the profile so the lock applies from then on.
    """
    meta = read_meta(workspace)
    if meta is None:
        return None
    recorded = meta.get("profile")
    if not isinstance(recorded, dict) or not recorded.get("root"):
        return None
    if recorded.get("root") == profile.get("root"):
        return None
    return (
        "This agent's history was recorded against a different workspace "
        f"({recorded.get('root')}), and is now bound to {profile.get('root')}. "
        "Everything it previously read or wrote refers to the old location, so "
        "resuming here would be misleading. Re-grant the original folder to "
        "continue this history, or delete this node and start a fresh agent."
    )


def _ensure_meta(path: Path, profile: "dict | None", root: "Path | None") -> None:
    if path.exists():
        return
    payload = (
        _meta_payload(profile, root)
        if profile is not None and root is not None
        else {"t": "meta", "v": META_VERSION}
    )
    _emit(path, payload)


def append_message(
    workspace: Path, message: dict, *, profile: "dict | None" = None, root: "Path | None" = None,
) -> None:
    """Append one history message through the background writer. `profile`/
    `root` are used only when this is the file's first line, to stamp the
    meta record check_profile later reads."""
    path = transcript_path(workspace)
    _ensure_meta(path, profile, root)
    _emit(path, {"t": "msg", **message})


def append_compaction(workspace: Path, content: str) -> None:
    """Record that a compaction happened, carrying the exact replacement
    message the live run continued with - see this module's docstring."""
    path = transcript_path(workspace)
    _ensure_meta(path, None, None)
    _emit(path, {"t": "compact", "content": content})


def drop_leading_orphan_tools(messages: list[dict]) -> list[dict]:
    """Trim leading tool results whose assistant tool_calls turn is not
    in the list. A history that opens mid tool-sequence is invalid to
    every provider; both the reload tail cut below and compaction's own
    tail cut can produce one, so the rule lives in one place."""
    index = 0
    while index < len(messages) and messages[index].get("role") == "tool":
        index += 1
    return messages[index:]


_INTERRUPTED_TOOL_RESULT = (
    "Interrupted - the run stopped before this tool produced a result."
)


def close_unanswered_tool_calls(messages: list[dict]) -> list[dict]:
    """Give every requested tool_call a result, synthesizing one where the run
    never produced it.

    drop_leading_orphan_tools above handles a history that OPENS mid
    tool-sequence. This is the other end, and it was missing.

    backend/harness/loop.py appends the assistant turn - tool_calls and all -
    and persists it BEFORE invoking any tool, so an interruption between those
    two points leaves the transcript ending on an assistant turn whose calls
    were never answered. A Stop lands there, so does a timeout, a provider
    fault, or the process dying. On the next follow-up load_messages returns
    that turn and loop.py appends the new user message after it, producing:

        ... assistant(tool_calls=[c1, c2]), user("try again")

    which every major provider rejects - OpenAI requires a tool message per
    tool_call, Anthropic requires a tool_result block per tool_use. The
    follow-up fails, the bad turn is still on disk, and the node is wedged
    permanently: every subsequent attempt fails the same way.

    Repairing on LOAD rather than when the run lands is deliberate. It fixes
    transcripts already sitting on disk from before this existed, and it
    covers interruption paths that never reach a landing handler at all (a
    crash, a kill). A synthetic result rather than dropping the assistant
    turn, because the turn is a real record of what the agent decided to do -
    losing it would make the transcript lie about the run.

    Handles the partially-answered case too: a Stop between two calls leaves
    the first answered and the second not.
    """
    repaired: list[dict] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        repaired.append(message)
        index += 1
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if not isinstance(calls, list) or not calls:
            continue
        answered: set[str] = set()
        while index < len(messages) and messages[index].get("role") == "tool":
            answered.add(str(messages[index].get("tool_call_id", "")))
            repaired.append(messages[index])
            index += 1
        for call in calls:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id", ""))
            if call_id and call_id not in answered:
                repaired.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": str(call.get("name", "")),
                    "content": _INTERRUPTED_TOOL_RESULT,
                })
    return repaired


def load_messages(workspace: Path) -> list[dict]:
    """The reload path: the last MAX_RELOADED_MESSAGES message lines, each
    content-capped, in file order. A missing file is an empty history (a
    fresh node), not an error.

    Flushes first: a background writer means "what is on disk" and "what
    has been appended" can differ by milliseconds, and reading a history
    that is missing its own last turn would silently re-ask the model
    something it already answered."""
    flush(workspace)
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
                if not isinstance(item, dict):
                    continue
                if item.get("t") == "compact":
                    content = item.get("content")
                    if isinstance(content, str) and content:
                        # Collapse everything before this point, exactly
                        # as the live run did when it compacted.
                        messages = [{"role": "user", "content": content}]
                    continue
                if item.get("t") != "msg":
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
    return close_unanswered_tool_calls(drop_leading_orphan_tools(messages))
