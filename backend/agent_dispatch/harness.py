"""HarnessDispatchOps - the Harness agent: session grants, persistent
shell/REPL process bookkeeping, workspace disposal, and the Harness run.

A MIXIN, not a standalone class: every method operates on the composing
class's shared state established by DispatcherCoreOps.__init__ - it is
composed exactly once, by backend/agents.py's
`class AgentDispatcher(DispatcherCoreOps, ...)`.

Method bodies are relocated VERBATIM from backend/agents.py; only the class
wrapper, imports, and the patch-seam rewrites below are new. Any name that
lives in backend/agents.py's module namespace (module helpers, constants,
names imported into it) is accessed late-bound as `agents_module.<name>`
through an in-body deferred import, NEVER via a module-top import here: a
top-level `from backend.agents import X` would be a circular import
(agents.py imports this module) AND would freeze the name at import time,
making the test suite's `monkeypatch.setattr(backend.agents, "X", ...)`
patches invisible to these methods. The deferred-import-then-attribute
pattern resolves the name on backend.agents at call time, so those patch
seams keep working with zero test changes.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from backend.agent_dispatch._composed import DispatcherParts

if TYPE_CHECKING:
    from backend.tools import ToolRegistry


# Serializes first-use snapshots across concurrent harness tasks. The
# contents remain per-dispatcher (one app session), never process-global.
_MACHINE_SNAPSHOT_LOCK = threading.Lock()


class HarnessDispatchOps(DispatcherParts):
    """The Harness agent: grants, process bookkeeping, and the Harness run (mixin - see module docstring)."""

    def answer_harness_question(self, request_id: str, answer) -> bool:
        """Resolve a harness run parked on user.ask with the user's TEXT.

        Sibling of _resolve_approval, deliberately separate: that one
        resolves with a bool and is shared across every approval-gated kind,
        while this resolves with a string and is meaningful only for a run
        whose parked future came from user.ask. The kind check is the same
        load-bearing guard cancel_harness documents - a foreign kind's
        request_id must never have its own pending approval resolved with a
        string it would then read as truthy consent."""
        handle = self._runs.get(request_id)
        if handle is None or handle.kind != "harness" or handle.approval_token:
            return False
        future = handle.approval_future
        if future is None or future.done():
            return False
        future.set_result(str(answer) if answer is not None else "")
        return True

    def approve_harness_tool_for_session(self, request_id: str) -> bool:
        """§2.4's graded consent: approve this call AND remember the tool for
        the rest of the agent session. Resolves the parked future with the
        sentinel string "session" rather than True - ToolRegistry.invoke
        reads that as "approve and add to ctx.session_grants". Kind-checked
        for the same reason answer_harness_question is."""
        handle = self._runs.get(request_id)
        if handle is None or handle.kind != "harness" or handle.approval_token:
            return False
        future = handle.approval_future
        if future is None or future.done():
            return False
        future.set_result("session")
        return True

    def approve_harness_network(self, request_id: str, approval_id: str, *, for_task: bool = False) -> bool:
        """Resolve only the exact, still-open Isolated network disclosure."""
        handle = self._runs.get(request_id)
        if (
            handle is None or handle.kind != "harness"
            or not handle.harness_network_approval
            or not approval_id or approval_id != handle.approval_token
        ):
            return False
        future = handle.approval_future
        if future is None or future.done():
            return False
        future.set_result("network-task" if for_task else "network-once")
        return True

    def harness_session_grants(self, workspace_id: str) -> set:
        """The session-scoped grant set for one harness node. Keyed by
        workspace id (not node id, which is reassigned on every reload) and
        held on the dispatcher so a grant made in one task still holds in
        the next - that is what makes it SESSION-scoped rather than
        run-scoped. Cleared with the rest of the node's live state."""
        store = getattr(self, "_harness_session_grants", None)
        if store is None:
            store = {}
            self._harness_session_grants = store
        return store.setdefault(workspace_id, set())

    def harness_machine_snapshot(self, workspace_id: str, root: Path, shell: str = "auto", profile: str = "host") -> str:
        """Probe once per session/workspace binding, reuse on follow-ups.

        The workspace can be rebound from scratch to a trusted folder:
        replacing its cached entry when the resolved root changes avoids
        telling the agent it is working in the old directory. Node deletion
        and session eviction drop the cache alongside other live resources.
        """
        from backend.harness.context import describe_machine

        root_key = str(root.resolve())
        with _MACHINE_SNAPSHOT_LOCK:
            snapshots = getattr(self, "_harness_machine_snapshots", None)
            if snapshots is None:
                snapshots = {}
                self._harness_machine_snapshots = snapshots
            previous = snapshots.get(workspace_id)
            if previous is not None and previous[:3] == (root_key, shell, profile):
                return previous[3]
            description = (describe_machine(root, shell=shell, profile=profile)
                           if profile == "isolated" else describe_machine(root, shell=shell))
            snapshots[workspace_id] = (root_key, shell, profile, description)
            return description

    def harness_shell_sessions(self):
        """The session's long-running shell processes (PLAN §2.3). Held
        HERE, not per-run, because a dev server started in one task must
        still be alive in the next - and torn down on the same triggers
        every other live resource is (node delete, session evict)."""
        if getattr(self, "_harness_shell_sessions", None) is None:
            from backend.harness.shell_sessions import ShellSessionRegistry

            self._harness_shell_sessions = ShellSessionRegistry()
        return self._harness_shell_sessions

    def harness_python_repls(self):
        """The session's python.exec interpreters - same per-session
        lifetime and teardown triggers as the shell sessions above, for the
        same reason (state surviving between calls IS the feature)."""
        if getattr(self, "_harness_python_repls", None) is None:
            from backend.harness.tools_python import PythonReplRegistry

            self._harness_python_repls = PythonReplRegistry()
        return self._harness_python_repls

    def dispose_harness_workspace(self, workspace_id: str) -> None:
        """Node-delete teardown for one harness workspace's live processes.
        Best-effort and synchronous-safe: both registries no-op on a
        workspace they never saw, so this is always callable."""
        if not workspace_id:
            return
        grants = getattr(self, "_harness_session_grants", None)
        if grants is not None:
            grants.pop(workspace_id, None)
        with _MACHINE_SNAPSHOT_LOCK:
            snapshots = getattr(self, "_harness_machine_snapshots", None)
            if snapshots is not None:
                snapshots.pop(workspace_id, None)
        sessions = getattr(self, "_harness_shell_sessions", None)
        if sessions is not None:
            sessions.stop_workspace(workspace_id)
        repls = getattr(self, "_harness_python_repls", None)
        if repls is not None:
            repls.stop_workspace(workspace_id)

    def dispose_all_harness_processes(self) -> None:
        """Session-evict/shutdown teardown: all harness session resources,
        including the machine snapshot and live processes."""
        with _MACHINE_SNAPSHOT_LOCK:
            snapshots = getattr(self, "_harness_machine_snapshots", None)
            if snapshots is not None:
                snapshots.clear()
        sessions = getattr(self, "_harness_shell_sessions", None)
        if sessions is not None:
            sessions.stop_all()
        repls = getattr(self, "_harness_python_repls", None)
        if repls is not None:
            repls.stop_all()

    def harness_tool_registry(self, document) -> "ToolRegistry":
        """The harness rides the SAME per-session registry the Builder
        built (tools.py: one registry per session, RunContext is what's
        per-run) - fs tools are simply registered into it on first harness
        use. Cross-visibility is governed by scope filtering, not by
        separate registries: the Builder's spec filter excludes fs.read
        (outside BUILDER_GRANTED_SCOPES) and the harness's excludes
        everything outside HARNESS_GRANTED_SCOPES, so each loop offers its
        model only what its own grant set could pass at invoke()."""
        registry = self.builder_tool_registry(document)
        # The builder registry can be invalidated (e.g. after an MCP
        # configuration change). Register once per registry INSTANCE, not
        # once per dispatcher, so a rebuilt registry never loses harness tools.
        # A weak reference avoids retaining a stale Builder registry (and
        # its tool handlers) after settings invalidate the shared cache.
        from weakref import ref

        registered_ref = getattr(self, "_harness_tools_registry_ref", None)
        if registered_ref is None or registered_ref() is not registry:
            from backend.harness.tool_catalog import register_tool_modules

            register_tool_modules(registry, self, document)
            self._harness_tools_registry_ref = ref(registry)
        return registry

    async def start_harness_run(
        self,
        *,
        bus,
        notifications_state,
        document,
        harness_node_id: str,
        user_text: str,
        model_ref=None,
        allow_session_grants: bool = True,
    ) -> str | None:
        """Claims the "harness" kind and runs one harness task as a
        background task (backend/harness/loop.run_harness). One harness
        run per session at a time - the same kind-level busy guard every
        other run kind has. Stop lands through finalize: RunRegistry.cancel
        frees the slot immediately and finalize writes the terminal
        "stopped" state (the 6.2 posture start_builder_run documents)."""
        from backend.domain.node_states import HarnessState

        if self._runs.is_busy("harness"):
            notifications_state.show("An agent run is already in progress.", "info")
            await bus.publish("notification")
            return None
        node = document.nodes.get(harness_node_id)
        if node is None or not isinstance(node.state, HarnessState):
            notifications_state.show("This agent node no longer exists.", "warning")
            await bus.publish("notification")
            return None

        cancel_event = threading.Event()

        async def _finalize() -> None:
            if node.state.harness_status == "running" and node.state.harness_run_id == request_id:
                node.state.harness_status = "stopped"
                node.state.harness_status_detail = "Stopped by user."
                # A tool may still be unwinding in its worker thread.
                # Preserve its last bounded output, but do not leave the
                # activity row advertising "running" after Stop.
                if node.state.harness_activity:
                    last = node.state.harness_activity[-1]
                    if last.get("outcome") == "running":
                        last["outcome"] = "stopped"
            node.state.harness_awaiting_approval = False
            node.state.harness_approval_tool_name = ""
            node.state.harness_approval_summary = ""
            node.state.harness_approval_id = ""
            node.state.harness_approval_session_offered = False
            node.state.harness_approval_network_offered = False
            if node.pending_request_id == handle.request_id:
                node.pending_request_id = None
            await bus.publish("scene")

        handle = self._runs.claim(
            "harness", node_id=harness_node_id, cancel_event=cancel_event, finalize=_finalize,
        )
        request_id = handle.request_id

        async def _run() -> None:
            from backend.harness import loop as harness_loop

            try:
                await harness_loop.run_harness(
                    document=document, dispatcher=self,
                    registry=self.harness_tool_registry(document),
                    bus=bus, notifications=notifications_state,
                    harness_node_id=harness_node_id, user_text=user_text,
                    request_id=request_id, handle=handle, cancel_event=cancel_event,
                    model_ref=model_ref, settings_manager=self._settings_manager,
                    allow_session_grants=allow_session_grants,
                    **self._runtime_kwargs(),
                )
            finally:
                self._runs.release(request_id)

        self._runs.attach_task(handle, asyncio.create_task(_run()))
        return request_id

    def cancel_harness(self, request_id: str) -> None:
        """Stop for a harness run: deny any parked approval first (cancel
        means deny - the code_sandbox/builder precedent), then the standard
        release-on-cancel. See _cancel_with_pending_approval_denied for the
        shared shape - its own docstring covers the exact foreign-kind
        approval-denial race the kind check inside it closes."""
        self._cancel_with_pending_approval_denied(request_id, "harness")
