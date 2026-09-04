"""GitlinkDispatchOps - Gitlink dispatch: repository listing/tree/
snapshot/context plumbing plus the Run and Apply surfaces.

A MIXIN, not a standalone class: every method operates on the composing
class's shared state established by DispatcherCoreOps.__init__ - it is
composed exactly once, by backend/agents.py's
`class AgentDispatcher(DispatcherCoreOps, ...)`.

Method bodies are relocated VERBATIM from backend/agents.py; only the class
wrapper, imports, and the patch-seam rewrites below are new. The one later
departure: the plain-blocking-action skeleton the four repository plumbing
methods share was extracted here first, then copied verbatim into
code_review.py - it now lives on DispatcherCoreOps as
_run_node_blocking_action, where every dispatch mixin already has it. Any
name that lives in backend/agents.py's module namespace (module helpers,
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

if TYPE_CHECKING:
    from backend.events import SessionBus

from backend.agent_dispatch._composed import DispatcherParts


class GitlinkDispatchOps(DispatcherParts):
    """Gitlink dispatch: repo plumbing plus the Run and Apply surfaces (mixin - see module docstring)."""

    async def fetch_gitlink_repositories(self, *, bus: SessionBus, notifications_state, node) -> list[str]:
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        async def _action():
            return await asyncio.to_thread(agents_module._list_github_repositories, self._settings_manager)

        return await self._run_node_blocking_action(
            bus=bus,
            notifications_state=notifications_state,
            node=node,
            action=_action,
            timeout=agents_module.GITLINK_REPO_LIST_TIMEOUT_SECONDS,
            timeout_message=(
                "Loading GitHub repositories stopped responding before the request completed. "
                "Please try again."
            ),
            error_log_message="gitlink repository listing failed",
            error_notify_prefix="Failed to load GitHub repositories",
            default=[],
        )

    async def load_gitlink_repo_tree(self, *, bus: SessionBus, notifications_state, node, repo: str, branch: str):
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        async def _action():
            return await asyncio.to_thread(agents_module._load_gitlink_tree, self._settings_manager, repo, branch)

        return await self._run_node_blocking_action(
            bus=bus,
            notifications_state=notifications_state,
            node=node,
            action=_action,
            timeout=agents_module.GITLINK_TREE_TIMEOUT_SECONDS,
            timeout_message=(
                "Loading the repository file tree stopped responding before the request "
                "completed. Please try again."
            ),
            error_log_message="gitlink repo tree load failed",
            error_notify_prefix="Failed to load the repository file tree",
            default=None,
        )

    async def import_gitlink_snapshot(
        self, *, bus: SessionBus, notifications_state, node, repo: str, branch: str,
        local_root_hint: str, imported_root_hint: str,
    ):
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        async def _action():
            resolved_repo, resolved_branch, local_root_path = await asyncio.to_thread(
                agents_module._ensure_gitlink_snapshot, self._settings_manager, repo, branch,
                local_root_hint, imported_root_hint,
            )
            return resolved_repo, resolved_branch, str(local_root_path)

        return await self._run_node_blocking_action(
            bus=bus,
            notifications_state=notifications_state,
            node=node,
            action=_action,
            timeout=agents_module.GITLINK_IMPORT_TIMEOUT_SECONDS,
            timeout_message=(
                "Importing the repository snapshot stopped responding before the request "
                "completed. Please try again."
            ),
            error_log_message="gitlink snapshot import failed",
            error_notify_prefix="Failed to import the repository snapshot",
            default=None,
        )

    async def build_gitlink_context(
        self, *, bus: SessionBus, notifications_state, node, scope_mode: str, selected_paths: list[str],
    ):
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        async def _action():
            return await asyncio.to_thread(
                agents_module._build_gitlink_context_bundle,
                self._settings_manager,
                repo=node.state.gitlink_repo,
                branch=node.state.gitlink_branch,
                scope_mode=scope_mode,
                selected_paths=selected_paths,
                repo_file_paths=list(node.state.gitlink_repo_file_paths),
                local_root_hint=node.state.gitlink_local_root,
                imported_root_hint=node.state.gitlink_imported_root,
            )

        return await self._run_node_blocking_action(
            bus=bus,
            notifications_state=notifications_state,
            node=node,
            action=_action,
            timeout=agents_module.GITLINK_CONTEXT_TIMEOUT_SECONDS,
            timeout_message=(
                "Building the repository context stopped responding before the request "
                "completed. Please try again."
            ),
            error_log_message="gitlink context build failed",
            error_notify_prefix="Failed to build the repository context",
            default=None,
        )

    async def start_gitlink_run(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        repo: str,
        branch: str,
        scope_mode: str,
        task_prompt: str,
        context_xml: str,
        context_summary: str,
        local_root: str,
        on_success,
        on_failure,
    ) -> None:
        """R5.3: Gitlink's Generate Change Set action - the independent
        Gitlink Run slot, mirroring start_web_research/start_artifact_reply's
        own fire-and-forget shape: the caller (register_canvas's
        run_gitlink_change_set) returns immediately after this schedules its
        background task; the eventual result lands via on_success/on_failure
        plus a "scene" republish, same as every other kind's real dispatch.

        Cooperative cancellation only, via a threading.Event
        (GitlinkAgent.get_response has no cancellation primitive of its own)
        - same honestly-documented limitation as every other dispatch
        surface: the checkpoint is placed AFTER the blocking call returns, so
        a cancel requested while the model call is already in flight discards
        the result rather than truly interrupting the underlying network
        call.

        The fingerprint is computed over the EXACT change set about to be
        shown - mirrors legacy's own shown_fingerprint, computed immediately
        before display, never a value captured earlier or later.

        DEFENSE-IN-DEPTH busy guard, checked here too (not only by
        register_canvas's own run_gitlink_change_set pre-check): node.
        pending_request_id is the shared busy marker for EVERY Gitlink
        action on this node, and the whole point of that field is making the
        Run-cannot-start-while-an-Apply-is-in-flight (and vice versa)
        guarantee hold regardless of call site. Checking it again here means
        a future caller that skips the canvas.py pre-check can never
        accidentally start a second concurrent Gitlink action on the same
        node. The ONE exception is _GITLINK_RUN_CLAIM_PLACEHOLDER (see that
        constant's own comment): run_gitlink_change_set stores that exact
        sentinel into node.pending_request_id, synchronously, immediately
        before calling this method - this method recognizes it as "already
        claimed by my own caller" and overwrites it, rather than rejecting a
        request its own caller just admitted.

        R5.3 post-review FIX 4(a): node.pending_request_id is now claimed
        SYNCHRONOUSLY here, immediately after the busy check and BEFORE
        asyncio.create_task(_run()) below - mirroring start_gitlink_apply's
        own claim exactly. Before this fix, the slot stayed empty until
        _run() actually got a turn on the event loop, leaving a real gap
        between "Run was requested" and "Run's sub-task actually started"
        during which a second concurrent Run or an Apply for the same node
        could slip past the busy check above.

        ADR-002 stage 2.4f: self._runs.claim() now happens in that SAME
        synchronous stretch, immediately alongside node.pending_request_id's
        own claim - not at the old dict-literal write site (which sat
        after this method's first `await`, see backend/run_lifecycle.py's
        own docstring for why that would reopen a race for a kind that DID
        need one). Unlike every prior migrated kind, is_busy("gitlink_run")
        is never checked anywhere - see this field's own comment in
        __init__ for why node.pending_request_id remains the sole real
        guard, this registry claim exists purely to carry cancel_event/task
        bookkeeping into the shared cancel()/cancel_all() sweep."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if node.pending_request_id and node.pending_request_id != agents_module._GITLINK_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return

        cancel_event = threading.Event()
        handle = self._runs.claim("gitlink_run", node_id=node_id, cancel_event=cancel_event)
        request_id = handle.request_id
        node.pending_request_id = request_id
        await bus.publish("scene")

        async def _run():
            try:
                payload = {
                    "task_prompt": task_prompt,
                    "context_xml": context_xml,
                    "repo": repo,
                    "branch": branch,
                    "scope_label": "Full Repo Access" if scope_mode == "full" else "Selected Files",
                    "context_summary": context_summary,
                    "branch_transcript": "",
                }
                result = await asyncio.wait_for(
                    asyncio.to_thread(agents_module._call_gitlink_agent, payload),
                    timeout=agents_module.GITLINK_WATCHDOG_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    notifications_state.show("Gitlink generation cancelled.", "info")
                    await bus.publish("notification")
                else:
                    proposal_markdown = agents_module._build_gitlink_proposal_markdown(repo, branch, result)
                    preview_text = agents_module._build_gitlink_preview_text(result["files"], local_root, repo, branch)
                    fingerprint = agents_module._fingerprint_changes(result["files"]) if result["files"] else None
                    # R5.3 post-review FIX 2: local_root is now forwarded to
                    # on_success too, so document.complete_gitlink_run can
                    # record exactly which local_root THIS run used (see that
                    # method's own docstring) - the write-destination binding
                    # start_gitlink_apply's fourth check enforces.
                    on_success(proposal_markdown, result["files"], preview_text, fingerprint, local_root)
                    await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                notifications_state.show(
                    "Gitlink generation stopped responding before the request completed. "
                    "Please try again.",
                    "error",
                )
                await bus.publish("notification")
            except Exception as exc:
                agents_module.logger.exception("gitlink dispatch failed")
                on_failure(f"Gitlink generation failed: {exc}")
                notifications_state.show(f"Gitlink generation failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                # R5.3 post-review FIX 4(c): only clear if this task's OWN
                # request_id is still the one recorded - a stale,
                # already-superseded task finishing late must never clobber
                # a newer legitimate busy marker.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    async def start_gitlink_apply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        client_fingerprint: str,
        local_root: str,
        on_success,
        on_failure,
    ) -> None:
        """R5.3: Gitlink's Apply action - THE code the whole increment hinges
        on. The fingerprint check and the freeze of the data that will
        actually be written happen in the SAME synchronous stretch of this
        coroutine, with ZERO await between them. Python asyncio is
        single-threaded; only an await yields control - so it is IMPOSSIBLE
        (not merely unlikely) for node.state.gitlink_pending_changes to be
        mutated between the recompute and the freeze immediately after it. This is a
        STRONGER guarantee than legacy's own check, because legacy's
        confirmation dialog is a real blocking call that pumps the Qt event
        loop (letting a background thread's finished signal run mid-dialog) -
        this coroutine has no equivalent yield point until deliberately
        introduced AFTER the freeze.

        R5.3 post-review FIX 5: node.pending_request_id is now claimed
        SYNCHRONOUSLY here, immediately after the busy check above and
        BEFORE the local_root_text validation - mirroring start_gitlink_run's
        own early synchronous claim (see that method's own docstring). Before
        this fix, the busy slot stayed unclaimed all the way through the
        local_root_text validation, the `await asyncio.to_thread(local_root_
        path.exists)` call below (a real yield point), and the entire atomic
        fingerprint/local_root section, only ever being set at the very end,
        just before scheduling _run(). Two genuinely concurrent Apply calls
        for the SAME node (two different WebSocket connections on the same
        session, e.g. two browser tabs - not a single connection's
        sequential message loop) could both read node.pending_request_id as
        falsy before either claimed it, both proceed through the exists()
        await and the atomic section, and both end up scheduling a write via
        apply_change_set concurrently - a real write-safety issue, since two
        concurrent writers touching the same files' backup/rollback
        bookkeeping is not something apply_change_set was designed to
        tolerate. Every early-return failure path BELOW this claim (empty
        pending_changes, empty local_root, nonexistent local_root,
        fingerprint mismatch, local_root mismatch) now ALSO clears
        node.pending_request_id back to None before returning, since none of
        those paths ever reach _run()'s own finally block - without that
        clear, a legitimately-rejected Apply would leave the node
        permanently stuck "busy".

        ADR-002 stage 2.4f: self._runs.claim() now happens in that SAME
        synchronous stretch as node.pending_request_id's own claim, for
        the same reason start_gitlink_run's own claim moved there (see
        this field's own comment in __init__: node.pending_request_id
        remains the sole real busy guard, this registry claim is pure
        task bookkeeping). Consequently EVERY one of the 5 early-return
        branches below - which already clear node.pending_request_id
        before returning - must ALSO release this registry claim, or it
        leaks forever on every rejected Apply (none of those branches
        ever reach _run()'s own finally, the only other place a release
        happens)."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if node.pending_request_id:
            notifications_state.show("Gitlink is already busy for this node.", "info")
            await bus.publish("notification")
            return

        # ADR-006 stage 6.2: gitlink_apply gains a cancel_event, checked at
        # the worker's ENTRY only (below, before any file is written) - once
        # writing begins the apply deliberately runs to completion, because
        # stopping between file writes would leave the working tree in a
        # half-applied state the UI has no way to represent. So cancel (and
        # session-disconnect cancel_all) covers the queued-but-not-started
        # window and frees the slot immediately; a mid-write apply finishes.
        cancel_event = threading.Event()
        handle = self._runs.claim("gitlink_apply", node_id=node_id, cancel_event=cancel_event)
        request_id = handle.request_id
        node.pending_request_id = request_id

        async def _abandon(reason: str) -> None:
            """Give back everything this call claimed, and say why.

            Five refusal paths below shared these four lines verbatim. That
            is four chances each to forget one, and forgetting the first two
            is not a cosmetic slip: a missed `pending_request_id = None`
            leaves the node permanently busy, and a missed release() leaks
            the registry slot for the rest of the session.

            Its `await` sits on the ABANDON path only, so the atomic
            check-and-freeze section further down keeps its zero-await
            guarantee - the comparisons there still run with no suspension
            point between them; this only runs once one of them has already
            decided to give up."""
            node.pending_request_id = None
            self._runs.release(request_id)
            on_failure(reason)
            await bus.publish("scene")

        if not node.state.gitlink_pending_changes:
            await _abandon("There is no approved change set to write.")
            return

        local_root_text = (local_root or "").strip()
        if not local_root_text:
            await _abandon("Select or import a local repository path before applying changes.")
            return
        local_root_path = Path(local_root_text).expanduser()
        # R5.3 post-review FIX 3: wrapped in asyncio.to_thread, like every
        # other filesystem check in this file - this was the sole exception,
        # running synchronously directly on the shared event loop. Placed
        # BEFORE the atomic check-and-freeze section below, so this await
        # does not touch that section's own zero-await guarantee (which only
        # covers the fingerprint-check-through-snapshot-freeze part). R5.3
        # post-review FIX 5: this await is now the reason the busy claim
        # above had to move earlier - a second concurrent call could
        # otherwise slip past the busy check while this await has yielded
        # control.
        local_root_exists = await asyncio.to_thread(local_root_path.exists)
        if not local_root_exists:
            await _abandon("The selected local repository path does not exist.")
            return

        # --- Atomic check-and-freeze: NO await between these statements. ---
        current_fingerprint = agents_module._fingerprint_changes(node.state.gitlink_pending_changes)
        if (
            client_fingerprint != current_fingerprint
            or current_fingerprint != node.state.gitlink_change_fingerprint
        ):
            await _abandon("The proposed change set changed after approval. Review it again before applying.")
            return
        # R5.3 post-review FIX 2: the fingerprint above says nothing about
        # WHERE the content is written - _fingerprint_changes only hashes
        # file content/paths/operations, never local_root (deliberately not
        # modified here - it is reused verbatim from gitlink/agent.py, shared
        # with the legacy Qt app). Without this separate check, a
        # gitlink_local_root edited after Run but before Apply would let
        # previously-reviewed content be written into a directory that was
        # never diffed or shown to the user. Compared as raw trimmed text,
        # consistent with how local_root_text itself is derived just above
        # and how document.complete_gitlink_run records
        # gitlink_change_local_root.
        if local_root_text != (node.state.gitlink_change_local_root or ""):
            await _abandon(
                "The local repository path changed since this proposal was generated. "
                "Regenerate the change set before applying."
            )
            return
        changes_snapshot = [dict(item) for item in node.state.gitlink_pending_changes]
        # --- End atomic section. Everything past this point operates ONLY on
        # changes_snapshot, never on node.state.gitlink_pending_changes again. ---

        # R5.3 post-review FIX 5: request_id was already generated and
        # claimed into node.pending_request_id right after the busy check
        # above - NOT re-generated here. Only the change_state transition and
        # publish happen at this point now.
        node.state.gitlink_change_state = "applying"
        await bus.publish("scene")

        async def _run():
            try:
                if cancel_event.is_set():
                    # Cancelled before any file was written (see the claim's
                    # own comment) - report it on the node rather than leaving
                    # "applying" stuck.
                    on_failure("Apply cancelled before any files were written.")
                    return
                written_files = await asyncio.wait_for(
                    asyncio.to_thread(agents_module._call_gitlink_apply, local_root_path, changes_snapshot),
                    timeout=agents_module.GITLINK_APPLY_TIMEOUT_SECONDS,
                )
                on_success(written_files)
                notifications_state.show(f"Applied {written_files} file changes.", "info")
                await bus.publish("notification")
            except asyncio.TimeoutError:
                on_failure(
                    "Applying changes stopped responding before the request completed. "
                    "Some files may have been partially written - check the repository "
                    "before retrying."
                )
                notifications_state.show("Gitlink apply timed out.", "error")
                await bus.publish("notification")
            except Exception as exc:
                agents_module.logger.exception("gitlink apply failed")
                on_failure(f"Failed to write approved changes: {exc}")
                notifications_state.show(f"Gitlink apply failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                # R5.3 post-review FIX 4(c): only clear if this task's OWN
                # request_id is still the one recorded - same stale-task
                # guard as start_gitlink_run's own finally block above.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    # -- R5.4: Execution Sandbox -----------------------------------------------
    #
    # SECURITY BOUNDARY (stated plainly, not softened): CodeSandboxNode
    # executes code with the full privileges of the user's account. The only
    # two protections are the WS-Origin handshake check and a mandatory
    # human-approval step. There is no code-level sandbox - no container,
    # VM, or OS-level resource/permission restriction. Execution Sandbox's
    # own timeout (VirtualEnvSandbox.execute_code's built-in limit) is a
    # hang guard, not a security control: it does not stop a malicious
    # script from reading files, exfiltrating data, or running arbitrary
    # code during pip install via a hostile package's build backend, before
    # the approved script itself ever runs.
    #
    # The method below runs its entire pipeline as ONE coroutine on the
    # event loop - the blocking LLM/subprocess calls are wrapped in
    # asyncio.to_thread, but the PAUSE between them (waiting for a human to
    # approve or deny the candidate code) needs no thread-crossing at all: it
    # collapses into a plain `asyncio.Future[bool]` (self._runs's
    # "code_sandbox" handle's own approval_future field), created BEFORE the
    # background task even starts. `approved = await approval_future` IS the
    # entire "waiting for approval" state - nothing else is needed.

    def cancel_gitlink(self, request_id: str) -> bool:
        """kind="gitlink_run": ADR-002 stage 2.4f - see RunRegistry.cancel's
        own docstring for why kind= is passed now that gitlink_run shares
        self._runs with other cancel_event-bearing kinds."""
        return self._runs.cancel(request_id, kind="gitlink_run")
