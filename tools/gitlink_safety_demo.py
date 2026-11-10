"""Repeatable, offline Gitlink review/apply checks in disposable Git repositories.

Run from the checkout: python tools/gitlink_safety_demo.py [--json report.json]
No model, network request, desktop window, or user repository is used. The
crash cases run the real writer in a child process and exit after one write.
This is a deterministic regression demonstration, not a live-model evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.agents import AgentDispatcher, _build_gitlink_preview_text, _fingerprint_changes  # noqa: E402
from backend.api.intents_gitlink import register_gitlink_intents  # noqa: E402
from backend.domain.graph import SceneDocument  # noqa: E402
from backend.events import SessionBus  # noqa: E402
from backend.notifications import NotificationState  # noqa: E402
from graphlink_plugins.gitlink import apply_transaction  # noqa: E402
from graphlink_plugins.gitlink.change_review import prepare_change_review  # noqa: E402
from graphlink_plugins.gitlink.repository import apply_change_set, scan_local_repo_paths  # noqa: E402

ORIGINALS = {"first.py": b"original first\r\n", "second.py": b"original second\n"}
PROPOSAL = [
    {"path": "first.py", "operation": "update", "content": "reviewed first\n"},
    {"path": "second.py", "operation": "update", "content": "reviewed second\n"},
    {"path": "new.py", "operation": "create", "content": "reviewed new\n"},
]
CRASH_EXIT = 73
SCENARIOS = (
    "missing_fingerprint", "tampered_proposal", "changed_checkout", "unknown_selection",
    "stale_last_target", "missing_baselines", "selected_subset_and_replay",
    "path_escape", "crash_recovery", "recovery_independent_edit", "recovery_directory_conflict",
)


def require(condition, message):
    # Keep the checks active under python -O too.
    if not condition:
        raise AssertionError(message)


def snapshot(root):
    """Hash all fixture files (excluding Git internals), not just expected targets."""
    return {
        str(path.relative_to(root)).replace(os.sep, "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def git_env():
    """Do not let a caller's Git routing/config point this demo at their repo."""
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def make_repo(parent, name):
    root = parent / name
    root.mkdir()
    subprocess.run(["git", "-c", "init.templateDir=", "init", "--quiet", str(root)],
                   env=git_env(), check=True, capture_output=True)
    for path, content in ORIGINALS.items():
        (root / path).write_bytes(content)
    return root


class DemoSettings:
    def get_enable_system_prompt(self):
        return True


class ReviewSession:
    """Use the production intent, dispatcher, state transitions, and disk writer."""

    def __init__(self, root):
        self.root = root
        self.bus = SessionBus("disposable-gitlink-demo")
        self.document = SceneDocument()
        notifications = NotificationState()
        self.bus.register_topic("scene", lambda: {})
        self.bus.register_topic("notification", notifications.payload)
        self.dispatcher = AgentDispatcher(DemoSettings())
        register_gitlink_intents(self.bus, self.document, notifications, self.dispatcher)
        self.node = self.document.add_gitlink_node(0, 0, None)
        self.document.set_gitlink_local_root(self.node.id, str(root))
        reviewed, originals = prepare_change_review(PROPOSAL, str(root))
        self.fingerprint = _fingerprint_changes(reviewed)
        preview = _build_gitlink_preview_text(reviewed, str(root), "demo/local", "main",
                                               original_texts=originals)
        self.document.complete_gitlink_run(self.node.id, "Scripted fixture, no model call", reviewed,
                                           preview, self.fingerprint, str(root))

    async def apply(self, fingerprint, paths=None):
        await self.bus.dispatch_intent("scene", "applyGitlinkChanges", [self.node.id, fingerprint, paths])
        tasks = [handle.task for handle in self.dispatcher._runs.values()
                 if handle.kind == "gitlink_apply" and handle.task is not None]
        if tasks:
            await asyncio.gather(*tasks)
        require(self.node.pending_request_id is None, "Apply leaked the node's busy lease")
        require(not list(self.dispatcher._runs.values()), "Apply leaked a run registry entry")
        return self.node.state.gitlink_error


async def intent_scenario(name, parent):
    root = make_repo(parent, "checkout")
    session = ReviewSession(root)
    token = session.fingerprint
    paths = None
    expected_error = ""
    if name == "missing_fingerprint":
        token, expected_error = "", "changed after approval"
    elif name == "tampered_proposal":
        session.node.state.gitlink_pending_changes[0]["content"] = "unreviewed payload"
        expected_error = "changed after approval"
    elif name == "changed_checkout":
        other = make_repo(parent, "other-checkout")
        session.document.set_gitlink_local_root(session.node.id, str(other))
        expected_error = "path changed"
    elif name == "unknown_selection":
        paths, expected_error = ["not-reviewed.py"], "no longer match"
    elif name == "stale_last_target":
        # The last existing target is stale. The first must remain untouched.
        (root / "second.py").write_bytes(b"independent editor change\n")
        expected_error = "changed since the preview"
    elif name == "missing_baselines":
        for row in session.node.state.gitlink_pending_changes:
            del row["expected_sha256"]
        token = _fingerprint_changes(session.node.state.gitlink_pending_changes)
        session.node.state.gitlink_change_fingerprint = token
        expected_error = "no local-file baselines"
    elif name == "selected_subset_and_replay":
        (root / "second.py").write_bytes(b"unselected editor change\n")
        paths = ["new.py", "first.py"]
    before = snapshot(parent)
    with patch.object(apply_transaction, "atomic_replace_file", wraps=apply_transaction.atomic_replace_file) as writer:
        error = await session.apply(token, paths)
        writes = writer.call_count
        if expected_error:
            require(expected_error in error, f"Wrong refusal: {error!r}")
            require(writes == 0, "A refused request reached the file writer")
            require(snapshot(parent) == before, "A refused request changed fixture files")
            require(not apply_transaction._journal_dir(root).exists(), "Refusal left a recovery journal")
            return {"outcome": "refused", "atomic_writes": writes, "files_unchanged": True,
                    "reason": expected_error}
        require(not error, error)
        require(writes == 2, f"Expected exactly two selected writes, got {writes}")
        require((root / "first.py").read_bytes() == b"reviewed first\n", "Selected update differs")
        require((root / "new.py").read_bytes() == b"reviewed new\n", "Selected creation differs")
        require((root / "second.py").read_bytes() == b"unselected editor change\n", "Unselected file changed")
        require(session.node.state.gitlink_pending_changes == [], "Proposal was not consumed")
        require(session.node.state.gitlink_change_fingerprint is None, "Fingerprint was not consumed")
        after = snapshot(parent)
        error = await session.apply(token, ["second.py"])
        require("no approved change set" in error, "Consumed proposal could be replayed")
        require(writer.call_count == writes and snapshot(parent) == after, "Replay changed files")
        return {"outcome": "selected files applied; replay refused", "atomic_writes": writes,
                "unselected_file_preserved": True, "proposal_consumed": True, "replay_writes": 0}


def crash_worker(root):
    """Child only: lose the process after the real writer's first rename/fsync."""
    reviewed, _ = prepare_change_review(PROPOSAL, str(root))
    real_write = apply_transaction.atomic_replace_file

    def exit_after_write(*args, **kwargs):
        real_write(*args, **kwargs)
        os._exit(CRASH_EXIT)  # no finally blocks, Python rollback, or clean shutdown

    with patch.object(apply_transaction, "atomic_replace_file", exit_after_write):
        apply_change_set(root, reviewed, require_review=True)
    raise AssertionError("Crash injection did not run")


def recovery_scenario(name, parent):
    root = make_repo(parent, "checkout")
    completed = subprocess.run(
        [sys.executable, "-c",
         "import sys; from pathlib import Path; "
         "from tools.gitlink_safety_demo import crash_worker; crash_worker(Path(sys.argv[1]))", str(root)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    require(completed.returncode == CRASH_EXIT, f"Child did not exit at write boundary: {completed.stderr}")
    require((root / "first.py").read_bytes() == b"reviewed first\n", "Child did not actually write")
    require((root / "second.py").read_bytes() == ORIGINALS["second.py"], "Child wrote beyond crash point")
    journal = apply_transaction._journal_dir(root)
    require((journal / "manifest.json").is_file(), "Crash did not retain a durable manifest")

    if name == "recovery_independent_edit":
        (root / "second.py").write_bytes(b"post-crash independent edit\n")
        expected = "independent edits"
    elif name == "recovery_directory_conflict":
        # A create target was absent at review and crash. A third party now
        # created a directory there: it is not equivalent to a missing file.
        (root / "new.py").mkdir()
        expected = "not a regular file"
    else:
        expected = None
    before = snapshot(parent)
    try:
        scan_local_repo_paths(root)  # real read-side recovery entry point
    except RuntimeError as exc:
        reason = str(exc)
    else:
        raise AssertionError("Recovery must request review again or report a conflict")
    if expected:
        require(expected in reason, f"Wrong recovery refusal: {reason}")
        require(snapshot(parent) == before, "Conflicted recovery partially restored files")
        require(journal.is_dir(), "Conflicted recovery discarded its journal")
        if name == "recovery_directory_conflict":
            require((root / "new.py").is_dir(), "Recovery removed the independent directory")
        return {"outcome": "recovery refused", "child_exit_code": completed.returncode,
                "files_unchanged_on_recovery": True, "journal_retained": True, "reason": expected}
    require("Recovered a previously interrupted" in reason, f"Recovery did not require re-review: {reason}")
    for path, content in ORIGINALS.items():
        require((root / path).read_bytes() == content, f"Recovery did not restore exact bytes: {path}")
    require(not (root / "new.py").exists() and not journal.exists(), "Recovery left proposed file/journal")
    require(scan_local_repo_paths(root) == sorted(ORIGINALS), "Second read was not clean")
    return {"outcome": "original bytes restored; re-review required", "child_exit_code": completed.returncode,
            "original_bytes_restored": True, "journal_removed": True}


def run_scenario(name):
    with tempfile.TemporaryDirectory(prefix="graphlink-safety-demo-") as directory:
        parent = Path(directory)
        if name.startswith("recovery_") or name == "crash_recovery":
            return recovery_scenario(name, parent)
        if name == "path_escape":
            root = make_repo(parent, "checkout")
            outside = parent / "outside.py"
            outside.write_bytes(b"outside sentinel")
            before = snapshot(parent)
            try:
                prepare_change_review([{"path": "../outside.py", "operation": "update", "content": "bad"}],
                                      str(root))
            except RuntimeError as exc:
                require("inside" in str(exc), f"Wrong path refusal: {exc}")
            else:
                raise AssertionError("Parent traversal was accepted")
            require(snapshot(parent) == before, "Path refusal changed files")
            return {"outcome": "review refused", "files_unchanged": True, "outside_sentinel_preserved": True}
        return asyncio.run(intent_scenario(name, parent))


def run_demo():
    results = []
    for name in SCENARIOS:
        try:
            results.append({"scenario": name, "status": "passed", **run_scenario(name)})
        except Exception as exc:
            results.append({"scenario": name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, env=git_env(),
                              capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, env=git_env(),
                           capture_output=True, text=True, check=True).stdout.strip()
    return {"schema_version": 1, "scope": "offline deterministic Gitlink file-content safeguards",
            "revision": revision, "working_tree_dirty": bool(dirty), "python": platform.python_version(),
            "platform": platform.system(), "results": results,
            "passed": sum(row["status"] == "passed" for row in results), "total": len(results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Write a machine-readable report to a new file")
    args = parser.parse_args()
    # Expected refusals are captured in results rather than printed as alarming tracebacks.
    logging.getLogger("backend.agents").setLevel(logging.CRITICAL)
    report = run_demo()
    for row in report["results"]:
        print(f"{row['status'].upper():6} {row['scenario']}: {row.get('outcome', row.get('error'))}")  # noqa: T201 - CLI output
    print(f"\n{report['passed']}/{report['total']} checks passed ({report['platform']}, Python {report['python']})")  # noqa: T201
    if args.json:
        with args.json.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
