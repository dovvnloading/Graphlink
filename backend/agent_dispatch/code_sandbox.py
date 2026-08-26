"""CodeSandboxDispatchOps - the Execution Sandbox run surface and its
scratch-dir/cancellation plumbing.

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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.events import SessionBus


class CodeSandboxDispatchOps:
    """The Execution Sandbox run surface and its plumbing (mixin - see module docstring)."""

    async def remove_code_sandbox_scratch_dir(self, sandbox_id: str) -> None:
        """ADR-005 stage 5.3: node-delete teardown for Execution Sandbox.
        VirtualEnvSandbox is never cached on this dispatcher (see this
        class's own __init__ docstring) - the only state that survives a
        run is the plain sandbox_id string on the node itself - so there is
        no live object to ask for its base_dir; the path is recomputed the
        same deterministic way VirtualEnvSandbox.__init__ builds it
        (remove_scratch_dir_for_id also refuses to act on a blank
        sandbox_id, rather than rmtree-ing the shared "default" bucket a
        blank id resolves to - see that function's own docstring). A venv
        tree can be large, so the removal runs in a thread rather than
        blocking the event loop.

        Best-effort: an in-flight run for this node may still be exiting
        when a delete races it (cancelled moments earlier by remove_nodes'
        own code_exec_cancels loop), in which case removal can fail (e.g. a
        file still open on Windows) and is simply logged - the age sweep in
        graphlink_scratch_dirs.py is the backstop for anything left behind
        here."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        await asyncio.to_thread(agents_module.remove_scratch_dir_for_id, agents_module.EXECUTION_SANDBOX_ROOT, sandbox_id)

    def cancel_code_sandbox(self, request_id: str) -> bool:
        """Cooperative cancel, same honestly-documented limitation as every
        other dispatch surface (the checkpoint is a cancel_event check
        between stages, not a true mid-call interrupt) EXCEPT for the
        approval pause itself, which this DOES immediately and definitely
        unblock by resolving approval_future. See
        _cancel_with_pending_approval_denied for the shared shape."""
        return self._cancel_with_pending_approval_denied(request_id, "code_sandbox")

    async def start_code_sandbox_run(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        sandbox_id: str,
        prompt: str,
        existing_code: str,
        requirements_manifest: str,
        conversation_history: list,
        on_success,  # on_success(code, output, analysis)
        on_failure,  # on_failure(message)
    ) -> None:
        """R5.4: Execution Sandbox's Run action - mirrors legacy's
        CodeSandboxExecutionWorker (generate-or-reuse -> human-approval pause
        -> prepare venv -> install requirements -> execute-with-repair-loop
        -> analyze), collapsed into one coroutine via a plain
        asyncio.Future[bool] approval-pause: `approved = await
        approval_future` IS the entire "waiting for a human" state, created
        BEFORE the background task even starts so cancel_code_sandbox can
        always resolve it even before the pipeline reaches its own await.

        There is no persisted mode field - the real branch is resolved
        HERE, at call time: a non-blank prompt always means
        "generate" (regenerating ignores any existing code, mirrors
        legacy's own `existing_code = code if run_mode == "manual" else
        ""`); a blank prompt with existing code means "reuse the existing
        code as-is, skip generation entirely"; a blank prompt with no
        existing code is a guard-rail failure, exactly matching legacy's own
        CodeSandboxExecutionWorker.run() top-of-function check.

        A fresh VirtualEnvSandbox is constructed HERE, per run (never
        cached/reused on the dispatcher) - the only state that must survive
        between runs is the plain string sandbox_id (real SceneNode state,
        not a live object), exactly like _call_gitlink_agent constructing a
        fresh GitlinkAgent per call.

        Cancellation is MORE effective here than Py-Coder's own REPL-based
        cancel: VirtualEnvSandbox._run_subprocess polls `should_continue()`
        (wired to `not cancel_event.is_set()`) roughly every 100ms while its
        subprocess is running, and genuinely terminates it via self.stop()
        the instant that check fails - a real, near-immediate interrupt, not
        merely a "checked between stages" limitation. This mirrors legacy's
        own already-working stop() behavior; it is not a new capability
        introduced by this port. VirtualEnvSandbox.execute_code's own
        baked-in 240s timeout (unchanged - see graphlink_plugins/
        code_sandbox/domain.py) is what actually bounds a hung subprocess
        that never checks should_continue on its own.

        R5.4 post-review FIX 1: live output streaming. VirtualEnvSandbox's
        `ensure_base_environment`/`sync_requirements`/`execute_code` each
        already accept an `emit_line` callback (see graphlink_plugins/
        code_sandbox/domain.py's own `_run_subprocess`) - invoked once per
        line of subprocess stdout/stderr, on the WORKER THREAD inside
        asyncio.to_thread. `_thread_emit_line` below hands each line to the
        event loop the same load-bearing way `_dispatch`'s own
        `_thread_on_chunk` does (`loop.call_soon_threadsafe(...)` feeding an
        `asyncio.Queue` - the only safe way to cross that thread boundary;
        `bus`/the queue itself are never touched directly from the worker
        thread). UNLIKE `_dispatch`'s own `_pump`, there is deliberately NO
        batching/flush-interval machinery here - R5.1's web-research
        increment already made this exact call for its own low-frequency
        progress channel ("too sparse to justify it"), and this channel is
        the same shape: one `bus.publish_stream(...)` call per subprocess
        line, in order, not a 15-17Hz token stream. A final `done=True` frame
        is always sent last, from the shared `finally` below, so it fires on
        EVERY exit path (guard-rail failure, no-code-generated, denied
        approval, cancelled, timed-out, or a real success) - mirroring
        `_dispatch`'s own "unconditional final flush on every exit path"
        guarantee for its own stream. `topic="scene"` (not a
        Composer-specific topic): CodeSandboxNode state is scene state, same
        as every other plugin node kind's own dispatch surface.

        ADR-002 stage 2.4g: node.pending_request_id remains the sole real
        busy guard - this registry claim is pure task/cancel_event/
        approval_future bookkeeping."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if node.pending_request_id and node.pending_request_id != agents_module._CODE_EXEC_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Virtual Environment Runner is already busy for this node.", "info")
            await bus.publish("notification")
            return

        cancel_event = threading.Event()
        approval_future: asyncio.Future = asyncio.get_running_loop().create_future()
        # ADR-005 stage 5.5 review-fix: see RunHandle.approval_snapshot_fn's
        # own doc for the race this closes - _resolve_approval calls this
        # synchronously, atomically with future.set_result(), instead of
        # this coroutine re-reading node.state after resuming.
        handle = self._runs.claim(
            "code_sandbox",
            node_id=node_id,
            cancel_event=cancel_event,
            approval_future=approval_future,
            approval_snapshot_fn=lambda: node.state.code_sandbox_approval_allow_source_builds,
        )
        request_id = handle.request_id
        node.pending_request_id = request_id
        await bus.publish("scene")

        def _should_continue() -> bool:
            return not cancel_event.is_set()

        async def _run():
            loop = asyncio.get_running_loop()
            line_queue: asyncio.Queue = asyncio.Queue()
            _STREAM_DONE = object()
            stream_seq = 0

            def _thread_emit_line(line: str) -> None:
                # Runs on the WORKER THREAD inside asyncio.to_thread - never
                # touch `line_queue`/`bus` directly here, only via
                # call_soon_threadsafe (see this method's own docstring).
                loop.call_soon_threadsafe(line_queue.put_nowait, line)

            async def _drain_stream() -> None:
                nonlocal stream_seq
                while True:
                    item = await line_queue.get()
                    if item is _STREAM_DONE:
                        break
                    await bus.publish_stream(
                        topic="scene", request_id=request_id, seq=stream_seq, delta=item, done=False,
                    )
                    stream_seq += 1
                # Guaranteed final frame, unconditional and always last - see
                # the `finally` below that always queues _STREAM_DONE before
                # awaiting this task, on EVERY exit path.
                await bus.publish_stream(
                    topic="scene", request_id=request_id, seq=stream_seq, delta="", done=True,
                )

            drain_task = asyncio.create_task(_drain_stream())
            try:
                prompt_text = (prompt or "").strip()
                manifest = agents_module._normalize_requirements(requirements_manifest or "")
                current_code = (existing_code or "").strip()

                if prompt_text:
                    initial_response = await asyncio.to_thread(
                        agents_module._call_sandbox_generation_agent, conversation_history, prompt_text, manifest
                    )
                    if cancel_event.is_set():
                        notifications_state.show("Sandbox execution cancelled.", "info")
                        await bus.publish("notification")
                        return
                    extracted = agents_module._extract_python_block(initial_response)
                    if not extracted:
                        on_success(
                            "# No Python code was generated for this request.",
                            "[Sandbox was not executed]",
                            initial_response,
                        )
                        await bus.publish("scene")
                        return
                    current_code = extracted
                elif not current_code:
                    on_failure("Provide a task prompt or Python code before running the sandbox.")
                    await bus.publish("scene")
                    return

                # -- human-approval gate --------------------------------------
                node.state.code_sandbox_code = current_code
                node.state.code_sandbox_awaiting_approval = True
                # ADR-005 stage 5.5: reset the source-build opt-in to its
                # safe default every time a gate opens - a stale True from a
                # previous run's approval must never silently carry forward
                # into one the user has not actually reviewed. See
                # CodeSandboxState.code_sandbox_approval_allow_source_builds's
                # own comment.
                node.state.code_sandbox_approval_allow_source_builds = False
                # ADR-005 stage 5.5 review-fix: this is the INITIAL gate, not
                # a repair re-gate - see the repair re-gate's own identical
                # write, further down this function, for what this flag is
                # for.
                node.state.code_sandbox_approval_is_repair = False
                # R5.4 CODESANDBOX FIX (closing the requirements-disclosure
                # staleness race): freeze the DISCLOSED manifest into its own
                # snapshot field at the exact same moment the approval gate
                # opens, using `manifest` - already computed above, at the
                # top of this function, before this function's own
                # generation-agent await. This introduces no new race: it
                # only exposes a value already correctly frozen, never
                # re-reading node.state.code_sandbox_requirements (the user's
                # still-live, still-editable draft for the NEXT run) at this
                # point. See CodeSandboxState.code_sandbox_approval_
                # requirements's own comment for the full race this closes.
                node.state.code_sandbox_approval_requirements = manifest
                # ADR-002 P0: fingerprints exactly what this gate is asking
                # about - see CodeSandboxState.code_sandbox_approved_
                # fingerprint's own comment. Frozen from the same
                # already-correct local `manifest`/`current_code`, at the
                # same moment, for the same staleness-avoidance reason as the
                # requirements snapshot right above.
                node.state.code_sandbox_approved_fingerprint = agents_module._fingerprint_changes(
                    {"code": current_code, "manifest": manifest}
                )
                await bus.publish("scene")
                approved = await approval_future
                # ADR-005 stage 5.5 review-fix: read the ALREADY-SNAPSHOTTED
                # value off the handle, never node.state here. An earlier
                # version of this line re-read node.state.code_sandbox_
                # approval_allow_source_builds right after the await, which
                # looked safe (no await in between) but wasn't: this
                # coroutine only resumes once the event loop gets around to
                # it after _resolve_approval's future.set_result() call
                # (backend/agents.py) merely SCHEDULES that resumption - a
                # second WS connection's setCodeSandboxAllowSourceBuilds
                # could fully land in that scheduling gap. handle.
                # approval_snapshot was instead captured by _resolve_approval
                # itself, atomically with future.set_result(), in a
                # synchronous stretch nothing else can interleave with - see
                # RunHandle.approval_snapshot_fn's own doc (backend/
                # run_lifecycle.py) for the full race this closes.
                allow_source_builds = bool(handle.approval_snapshot)
                node.state.code_sandbox_awaiting_approval = False
                # Cleared here too, immediately once the approval resolves -
                # mirrors code_sandbox_awaiting_approval's own clear on this
                # exact line (and canvas.py's complete_code_sandbox_run/
                # fail_code_sandbox_run clear it again downstream, redundant
                # but harmless, for every other path that lands there).
                node.state.code_sandbox_approval_requirements = ""
                node.state.code_sandbox_approval_allow_source_builds = False
                # REVIEW-FIX: without this publish, a REPAIR round's own
                # re-gate further down writes these fields back (True/
                # non-empty) after the venv-create/pip-install/execute
                # latency with no scene broadcast for THIS cleared state in
                # between: CodeSandboxNodeView.tsx's busy flag only
                # resets on an OBSERVED awaiting-approval false->true
                # transition, so every repair round's approval dialog
                # renders with both Approve and Deny permanently disabled
                # otherwise. Placed after all four fields above are
                # settled, not right after the first one, so this broadcast
                # is a single coherent snapshot rather than one publish
                # mid-way through an in-progress state update.
                await bus.publish("scene")

                if not approved:
                    on_failure("Sandbox run cancelled: execution was not approved.")
                    await bus.publish("scene")
                    return

                sandbox = agents_module.VirtualEnvSandbox(sandbox_id)
                try:
                    await asyncio.to_thread(
                        sandbox.ensure_base_environment, _should_continue, _thread_emit_line
                    )
                    await asyncio.to_thread(
                        sandbox.sync_requirements,
                        manifest,
                        _should_continue,
                        _thread_emit_line,
                        allow_source_builds,
                    )
                except InterruptedError:
                    notifications_state.show("Sandbox execution cancelled.", "info")
                    await bus.publish("notification")
                    return

                max_attempts = 3
                final_output = ""
                final_return_code = 0
                last_error = ""
                try:
                    for attempt_index in range(max_attempts):
                        # ADR-002 P0: defense-in-depth, not the primary fix
                        # (the repair re-gate below is) - compares the code/
                        # manifest about to run against the fingerprint
                        # taken at the moment the approval gate opened, so a
                        # tool call that mutated the node's code out from
                        # under an already-approved run is caught here even
                        # if it slipped past the re-gate.
                        if agents_module._fingerprint_changes(
                            {"code": current_code, "manifest": manifest}
                        ) != node.state.code_sandbox_approved_fingerprint:
                            on_failure(
                                "Sandbox execution blocked: the approved code no longer matches what is about to run."
                            )
                            await bus.publish("scene")
                            return
                        final_output, final_return_code = await asyncio.to_thread(
                            sandbox.execute_code, current_code, _should_continue, _thread_emit_line
                        )
                        if not agents_module._is_sandbox_error_output(final_output, final_return_code):
                            break
                        last_error = final_output or "The sandbox process exited with an error."
                        if attempt_index == max_attempts - 1:
                            break
                        current_code = await asyncio.to_thread(
                            agents_module._call_sandbox_repair_agent, current_code, last_error, manifest, prompt_text or None
                        )
                        if not _should_continue():
                            notifications_state.show("Sandbox execution cancelled.", "info")
                            await bus.publish("notification")
                            return

                        # ADR-002 P0: the repair agent just produced code
                        # the user has never seen, so it must not run under
                        # the approval that only ever covered the FIRST
                        # version - a fresh gate opens for every repaired
                        # attempt. Re-disclose the (unchanged) manifest
                        # alongside it, since code_sandbox_approval_
                        # requirements was already cleared once the initial
                        # gate resolved above. The liveness re-check (a
                        # cancel/delete may have popped this run's handle
                        # while the repair agent call above was in flight)
                        # must happen before parking a fresh approval_future
                        # on it.
                        if self._runs.get(request_id) is None:
                            return
                        repair_future: asyncio.Future = asyncio.get_running_loop().create_future()
                        handle.approval_future = repair_future
                        node.state.code_sandbox_code = current_code
                        node.state.code_sandbox_approval_requirements = manifest
                        node.state.code_sandbox_approved_fingerprint = agents_module._fingerprint_changes(
                            {"code": current_code, "manifest": manifest}
                        )
                        # ADR-005 stage 5.5 review-fix: an earlier version of
                        # this re-gate left code_sandbox_approval_allow_
                        # source_builds untouched here, reasoning it was
                        # "still False from the initial gate's own clear
                        # above" - that assumption was never actually
                        # enforced: setCodeSandboxAllowSourceBuilds is
                        # ungated and could set it True during the execute/
                        # repair window (nothing here was awaiting_approval,
                        # so nothing rejected the intent), leaving the repair
                        # panel's checkbox rendered CHECKED without the user
                        # having touched it this round - a real, adversarial-
                        # review-confirmed contradiction of this field's own
                        # documented "reset on every gate-open" invariant.
                        # Reset explicitly here, matching the initial gate's
                        # own reset, even though sync_requirements is never
                        # called again on a repair round (so this has no
                        # install-level effect) - the value must still be
                        # honest about being reset, not just impotent.
                        node.state.code_sandbox_approval_allow_source_builds = False
                        # ADR-005 stage 5.5 review-fix: distinguishes "this
                        # panel's checkbox reflects a decision that can still
                        # affect an install" (initial gate) from "the install
                        # already happened, checking this now does nothing"
                        # (any repair gate) - CodeExecutionApprovalPanel.tsx
                        # uses this to hide the otherwise-genuinely-inert
                        # checkbox on repair rounds rather than let a user
                        # take an action that silently has no effect.
                        node.state.code_sandbox_approval_is_repair = True
                        node.state.code_sandbox_awaiting_approval = True
                        await bus.publish("scene")
                        repair_approved = await repair_future
                        node.state.code_sandbox_awaiting_approval = False
                        node.state.code_sandbox_approval_requirements = ""
                        # REVIEW-FIX: see the initial gate's own identical
                        # publish above for why - this loop can run more
                        # than once (up to max_attempts repair rounds), so
                        # every False here needs its own broadcast too.
                        await bus.publish("scene")
                        if not repair_approved:
                            on_failure("Sandbox run cancelled: repaired code was not approved.")
                            await bus.publish("scene")
                            return
                    else:
                        # Structurally unreachable (mirrors legacy's own
                        # identical dead `else` branch - every loop path
                        # above ends in an explicit `break`), kept for exact
                        # structural parity rather than optimized away.
                        final_output = final_output or last_error
                except InterruptedError:
                    notifications_state.show("Sandbox execution cancelled.", "info")
                    await bus.publish("notification")
                    return

                output_text = final_output if final_output else "[No output produced]"
                analysis = await asyncio.to_thread(
                    agents_module._call_code_analysis_agent, prompt_text or None, current_code, output_text
                )
                on_success(current_code, output_text, analysis)
                await bus.publish("scene")
            except Exception as exc:
                agents_module.logger.exception("code sandbox dispatch failed")
                on_failure(f"Sandbox execution failed: {exc}")
                notifications_state.show(f"Sandbox execution failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                line_queue.put_nowait(_STREAM_DONE)
                await drain_task
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    # -- R6.2: Chart node -----------------------------------------------------
