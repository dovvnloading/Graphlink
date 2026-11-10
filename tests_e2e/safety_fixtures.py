"""Fixed, harmless inputs and observations for the dedicated E2E entry point.

No request accepts code, a filesystem destination, or an arbitrary proposal.
The fixture seam replaces model output/native folder selection; it never
approves, invokes, applies, validates, or writes a proposal on the test's behalf.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import subprocess
import threading
from unittest.mock import patch

from fastapi import APIRouter, HTTPException

from backend.agents import _build_gitlink_preview_text, _fingerprint_changes
from backend.domain.model import SceneNode
from backend.domain.node_states import HarnessState
from backend.providers.base import ToolCall
from backend.session_context import get_session_context
from graphlink_plugins.gitlink import apply_transaction
from graphlink_plugins.gitlink.change_review import prepare_change_review

ORIGINALS = {"first.py": b"first\r\n", "second.py": b"second\n"}
PROPOSAL = [
    {"path": "first.py", "operation": "update", "content": "first\n"},
    {"path": "second.py", "operation": "update", "content": "reviewed second\n"},
    {"path": "new.py", "operation": "create", "content": "reviewed new\n"},
]
SPACED_ORIGINALS = {"a b.py": b"single-space original\n", "a  b.py": b"double-space original\n"}
LONG_REVIEWED_SOURCE = "".join(f"reviewed single {index}\n" for index in range(60)) + "REVIEWED_SINGLE_SPACE_TAIL\n"
SPACED_PROPOSAL = [
    {"path": "a b.py", "operation": "update", "content": LONG_REVIEWED_SOURCE},
    {"path": "a  b.py", "operation": "update", "content": "reviewed double-space file\n"},
]
# The tail is executable, harmless, and deliberately beyond character 400.
# Identical code increments interpreter state, so a cached approval is observable.
PYTHON_SOURCE = (
    "# Complete source must be visible, including the harmless tail.\n"
    + "# harmless padding for full source disclosure\n" * 15
    + "counter = globals().get('counter', 0) + 1\n"
    + "from pathlib import Path\n"
    + "Path('counter.txt').write_text(str(counter), encoding='utf-8')\n"
    + "print('APPROVED_TAIL', counter)\n"
)
DIFFERENT_SOURCE = "from pathlib import Path\nPath('different.txt').write_text('approved', encoding='utf-8')\n"
TAMPERED_SOURCE = "from pathlib import Path\nPath('tampered.txt').write_text('must not run', encoding='utf-8')\n"


def git_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def files_snapshot(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "hex": path.read_bytes().hex(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


class SafetyFixtures:
    def __init__(self, app, root: Path, source_root: Path):
        self.app = app
        self.root = root
        self.source_root = source_root
        self.case_root: Path | None = None
        self.checkout: Path | None = None
        self.node: SceneNode | None = None
        self.calls: list[ToolCall] = []
        self.turn_index = 0
        self.writes: list[dict] = []
        self.lock = threading.Lock()
        self.sequence = 0
        self.initial_files: dict = {}
        router = APIRouter(prefix="/api/e2e")
        router.add_api_route("/prepare/{kind}", self.prepare, methods=["POST"])
        router.add_api_route("/action/{action}", self.action, methods=["POST"])
        router.add_api_route("/state", self.state, methods=["GET"])
        router.add_api_route("/info", self.info, methods=["GET"])
        # create_app installs the SPA catch-all last; fixture API routes must
        # precede it. They still pass through the real auth/origin middleware.
        app.router.routes[0:0] = router.routes

    def context(self):
        bus = self.app.state.bus.session()
        return bus, get_session_context(bus)

    def info(self):
        def git(*args):
            return subprocess.run(
                ["git", *args], cwd=self.source_root, env=git_environment(),
                check=True, capture_output=True, text=True,
            ).stdout.strip()

        return {
            "commit": git("rev-parse", "HEAD"),
            "working_tree_dirty": bool(git("status", "--porcelain")),
            "os": platform.platform(), "python": platform.python_version(),
            "git": git("--version"),
            "packages": {name: importlib.metadata.version(name)
                         for name in ("fastapi", "starlette", "uvicorn", "websockets")},
            "authentication": "real capability token and origin validation; ephemeral test token",
            "mocked": ["provider bootstrap/discovery", "token counting uses existing character fallback (no tokenizer download)",
                       "scripted provider tool-call output",
                       "fixture proposal/native-folder selection", "writer observation wrapper delegates unchanged"],
            "environment": "allowlisted OS/runtime environment; inherited Git routing and provider/cloud credentials discarded",
            "storage": "settings, chat and knowledge databases (including autosave index), and execution scratch roots are disposable",
            "real": ["built React UI", "WebSocket transport", "intent validation", "approval gate",
                     "AgentDispatcher", "proposal/baseline validation", "atomic file writer", "persistent PythonREPL"],
        }

    async def prepare(self, kind: str):
        if kind not in {"gitlink", "gitlink-long", "python", "python-single"}:
            raise HTTPException(404, "unknown fixed fixture")
        bus, context = self.context()
        dispatcher = context.agent_dispatcher
        if list(dispatcher._runs.values()):
            raise HTTPException(409, "finish or stop the previous fixture first")
        dispatcher.dispose_all_harness_processes()
        dispatcher._settings_manager.state["has_completed_onboarding"] = True
        context.canvas_document.clear_for_load()
        self.sequence += 1
        self.case_root = self.root / f"case-{self.sequence}"
        self.checkout = self.case_root / "checkout"
        self.checkout.mkdir(parents=True)
        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "--quiet", str(self.checkout)],
            env=git_environment(), check=True, capture_output=True,
        )
        (self.case_root / "outside-sentinel.txt").write_bytes(b"outside fixture sentinel\n")
        self.writes = []
        self.turn_index = 0
        self.calls = []
        document = context.canvas_document
        if kind.startswith("gitlink"):
            originals = SPACED_ORIGINALS if kind == "gitlink-long" else ORIGINALS
            proposal = SPACED_PROPOSAL if kind == "gitlink-long" else PROPOSAL
            for name, raw in originals.items():
                (self.checkout / name).write_bytes(raw)
            self.node = document.add_gitlink_node(0, 0, None)
            document.set_gitlink_local_root(self.node.id, str(self.checkout))
            self.review(proposal)
        else:
            self.node = document.add_harness_node(0, 0, "Offline Python approval fixture")
            assert self.node is not None and isinstance(self.node.state, HarnessState)
            self.node.state.harness_workspace_path = str(self.checkout)
            # Native folder picking is outside this test. Seed the same exact
            # trust-list entry, but let bound_root enforce it normally.
            dispatcher._settings_manager.state["harness_trusted_dirs"] = [str(self.checkout)]
            dispatcher.harness_session_grants(self.node.state.harness_workspace_id).add("python.exec")
            sources = [PYTHON_SOURCE] if kind == "python-single" else [PYTHON_SOURCE, PYTHON_SOURCE, DIFFERENT_SOURCE]
            self.calls = [ToolCall(id=f"fixture-python-{index}", name="python.exec", arguments={"code": code})
                          for index, code in enumerate(sources)]
        self.initial_files = files_snapshot(self.case_root)
        await bus.publish("scene")
        return {**self.state(), "source": PYTHON_SOURCE, "different_source": DIFFERENT_SOURCE}

    def review(self, files):
        _, context = self.context()
        reviewed, originals = prepare_change_review(files, str(self.checkout))
        preview = _build_gitlink_preview_text(
            reviewed, str(self.checkout), "fixture/local", "main", original_bytes=originals,
        )
        context.canvas_document.complete_gitlink_run(
            self.node.id, "Deterministic offline proposal fixture", reviewed, preview,
            _fingerprint_changes(reviewed), str(self.checkout),
        )

    async def action(self, action: str):
        if self.node is None or self.checkout is None:
            raise HTTPException(409, "prepare a fixture first")
        bus, _ = self.context()
        if action == "external-edit":
            (self.checkout / "second.py").write_bytes(b"independent editor change\n")
        elif action == "replace-proposal":
            self.review([{**row, "content": "replacement first\n"} if row["path"] == "first.py" else dict(row)
                         for row in PROPOSAL])
            await bus.publish("scene")
        elif action == "tamper-python":
            self.calls[0].arguments["code"] = TAMPERED_SOURCE
        else:
            raise HTTPException(404, "unknown fixed fixture action")
        return self.state()

    def state(self):
        if self.node is None:
            return {"prepared": False}
        _, context = self.context()
        state = self.node.state
        pending = context.agent_dispatcher._runs.get(getattr(state, "harness_run_id", ""))
        future = pending.approval_future if pending is not None else None
        return {
            "node_id": self.node.id,
            "files": files_snapshot(self.case_root), "initial_files": self.initial_files,
            "writes": list(self.writes),
            "busy": self.node.pending_request_id is not None,
            "active_runs": len(list(context.agent_dispatcher._runs.values())),
            "fingerprint": getattr(state, "gitlink_change_fingerprint", None),
            "error": getattr(state, "gitlink_error", ""),
            "change_state": getattr(state, "gitlink_change_state", ""),
            "approval_id": getattr(state, "harness_approval_id", ""),
            "request_id": getattr(state, "harness_run_id", ""),
            "awaiting_approval": getattr(state, "harness_awaiting_approval", False),
            "pending_approval_resolved": future.done() if future is not None else None,
            "harness_status": getattr(state, "harness_status", ""),
            "activity": getattr(state, "harness_activity", []),
            "provider_turns": self.turn_index,
        }

    def provider_turn(self, *_args, **_kwargs):
        with self.lock:
            index = self.turn_index
            self.turn_index += 1
        calls = self.calls[index:index + 1]
        return {"message": {"content": "" if calls else "Offline fixture complete."},
                "tool_calls": calls, "usage": {"input_tokens": 1, "output_tokens": 1}}

    @contextmanager
    def installed(self):
        import api_provider

        real_write = apply_transaction.atomic_replace_file

        def observed_write(target, data, original_mode=None):
            real_write(target, data, original_mode)
            relative = Path(target).resolve().relative_to(self.case_root.resolve()).as_posix()
            self.writes.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

        with patch.object(api_provider, "chat_turn_with_tools", self.provider_turn), \
                patch.object(apply_transaction, "atomic_replace_file", observed_write):
            try:
                yield
            finally:
                # A browser may fail to launch before a session ever exists.
                # Teardown must not create a new session after uvicorn closes
                # its loop merely to look for processes that never started.
                for bus in self.app.state.bus._sessions.values():
                    context = get_session_context(bus)
                    try:
                        context.agent_dispatcher.cancel_all_pending_approvals()
                    finally:
                        context.agent_dispatcher.dispose_all_harness_processes()
