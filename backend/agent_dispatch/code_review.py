"""CodeReviewDispatchOps - Review Lens dispatch: PR-diff fetch plus the
Review and Ask surfaces.

A MIXIN, not a standalone class: every method operates on the composing
class's shared state established by DispatcherCoreOps.__init__ - it is
composed exactly once, by backend/agents.py's
`class AgentDispatcher(DispatcherCoreOps, ...)`.

Method bodies follow backend/agent_dispatch/gitlink.py's own shapes; only
the Review Lens payloads differ. fetch/ask are plain blocking actions
that claim node.pending_request_id inline - they share
DispatcherCoreOps._run_node_blocking_action for that, rather than each
feature mixin keeping its own copy of the skeleton (this module used to
carry a byte-identical duplicate of gitlink's). The review run itself is
a fire-and-forget RunRegistry-claimed background task with a cooperative
cancel_event, still shaped like start_gitlink_run. Any name that lives in
backend/agents.py's module namespace (module helpers, constants, names
imported into it) is accessed late-bound as `agents_module.<name>`
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

from backend.agent_dispatch._composed import DispatcherParts


class CodeReviewDispatchOps(DispatcherParts):
    """Review Lens dispatch: PR-diff fetch plus the Review and Ask surfaces (mixin - see module docstring)."""

    async def fetch_code_review_diff(self, *, bus: SessionBus, notifications_state, node, pr_url: str):
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        async def _action():
            return await asyncio.to_thread(agents_module._fetch_code_review_bundle, self._settings_manager, pr_url)

        return await self._run_node_blocking_action(
            bus=bus,
            notifications_state=notifications_state,
            node=node,
            action=_action,
            timeout=agents_module.CODE_REVIEW_DIFF_TIMEOUT_SECONDS,
            timeout_message=(
                "Fetching the pull-request diff stopped responding before the request "
                "completed. Please try again."
            ),
            error_log_message="code review diff fetch failed",
            error_notify_prefix="Failed to fetch the pull-request diff",
            default=None,
        )

    async def start_code_review_run(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        bundle: dict,
        on_success,
        on_failure,
    ) -> None:
        """Review Lens's Run Review action - the same fire-and-forget shape
        as GitlinkDispatchOps.start_gitlink_run: the caller returns
        immediately after this schedules its background task; the eventual
        result lands via on_success/on_failure plus a "scene" republish.

        Cooperative cancellation only, via a threading.Event (the review
        engine has no cancellation primitive of its own) - the checkpoint
        is placed AFTER the blocking call returns, so a cancel requested
        while the model call is already in flight discards the result
        rather than truly interrupting the underlying network call.

        Busy guard: node.pending_request_id is the shared busy marker for
        EVERY code-review action on this node (fetch included) - a Run
        cannot start while a fetch or an Ask is in flight on the SAME
        node, and vice versa. The ONE exception is the caller's own
        synchronous placeholder claim (backend/api/intents_code_review.py
        claims _NODE_RUN_CLAIM_PLACEHOLDER before calling here) - this
        method recognizes ONLY that exact value as "already claimed by my
        own caller" and overwrites it, rather than rejecting a request
        its own caller just admitted.

        self._runs.claim() happens in that SAME synchronous stretch,
        alongside node.pending_request_id's own claim - never consulted
        via is_busy (node.pending_request_id remains the sole real guard;
        the registry is pure task/cancel_event bookkeeping into the
        shared cancel()/cancel_all() sweep)."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if node.pending_request_id and node.pending_request_id != agents_module._NODE_RUN_CLAIM_PLACEHOLDER:
            notifications_state.show("Review Lens is already busy for this node.", "info")
            await bus.publish("notification")
            return

        cancel_event = threading.Event()
        handle = self._runs.claim("code_review_run", node_id=node_id, cancel_event=cancel_event)
        request_id = handle.request_id
        node.pending_request_id = request_id
        await bus.publish("scene")

        async def _run():
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(agents_module._call_review_lens_agent, bundle),
                    timeout=agents_module.CODE_REVIEW_RUN_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    notifications_state.show("Review Lens run cancelled.", "info")
                    await bus.publish("notification")
                else:
                    on_success(result)
                    await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                notifications_state.show(
                    "Review Lens stopped responding before the request completed. "
                    "Please try again.",
                    "error",
                )
                await bus.publish("notification")
            except Exception as exc:
                agents_module.logger.exception("code review dispatch failed")
                on_failure(f"Review Lens run failed: {exc}")
                notifications_state.show(f"Review Lens run failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                # Only clear if this task's OWN request_id is still the one
                # recorded - a stale, already-superseded task finishing
                # late must never clobber a newer legitimate busy marker.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    async def ask_code_review_question(
        self, *, bus: SessionBus, notifications_state, node, question: str,
        review_summary: str,
    ):
        """One follow-up Q&A over the node's already-fetched diff - the
        "chat about the changes" surface. A plain blocking action (same
        skeleton as the fetch above): the answer lands via
        append_code_review_qa in the caller, not here."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        diff_text = node.state.code_review_diff_text
        async def _action():
            return await asyncio.to_thread(
                agents_module._ask_review_lens_agent, diff_text, question, review_summary,
            )

        return await self._run_node_blocking_action(
            bus=bus,
            notifications_state=notifications_state,
            node=node,
            action=_action,
            timeout=agents_module.CODE_REVIEW_ASK_TIMEOUT_SECONDS,
            timeout_message=(
                "Answering that question stopped responding before the request "
                "completed. Please try again."
            ),
            error_log_message="code review ask failed",
            error_notify_prefix="Failed to answer that question",
            default=None,
        )

    def cancel_code_review(self, request_id: str) -> bool:
        """kind="code_review_run": see RunRegistry.cancel's own docstring
        for why kind= is passed now that code_review_run shares self._runs
        with other cancel_event-bearing kinds."""
        return self._runs.cancel(request_id, kind="code_review_run")
