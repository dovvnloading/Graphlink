"""ResearchDispatchOps - Web Research and Artifact generation dispatch.

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
import inspect
import threading

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.events import SessionBus

from backend.agent_dispatch._composed import DispatcherParts


class ResearchDispatchOps(DispatcherParts):
    """Web Research and Artifact generation dispatch (mixin - see module docstring)."""

    async def start_web_research(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        node_id: str,
        query: str,
        branch_history: list,
        on_progress,
        on_success,
        on_failure,
        knowledge_collection_id: int = 0,
        retain_to_knowledge: bool = False,
    ) -> None:
        """R5.1: the Web Research independent-slot counterpart to
        start_image_reply above - NOT a variant of _dispatch, since there is
        exactly one caller (backend/canvas.py's run_web_research), so
        on_begin/on_end are inlined here directly rather than taking
        _dispatch's generic parameters. Guarded by self._runs's
        "web_research" kind, kept fully SEPARATE from both
        chat/conversation's own "chat" kind and image's own "image" kind -
        see that field's own comment in __init__ for why this must stay
        independent.

        Cooperative cancellation only, via a CancellationToken (not a
        threading.Event, since WebResearchService.run's own pipeline stages
        already accept `token: CancellationToken` - see
        graphlink_plugins/web_research/domain.py) - same honestly-documented
        limitation as existing chat/image dispatch: this does not force-kill
        a call already blocked inside a single blocking call with no
        checkpoint until it returns.

        ADR-002 stage 2.4e: migrated onto self._runs, using RunHandle.
        on_cancel (cancel_token.cancel, a plain bound-method callable) -
        the first surface to actually need it, since CancellationToken has
        no cancel_event-compatible Event to pass instead.

        ADR-020 stage 20.3: `knowledge_collection_id` (keyword-only,
        default 0 - the pre-20.3 global/unscoped sentinel, backend/
        knowledge_store.py's own module docstring) is threaded straight
        through into WebResearchRequest.knowledge_collection_id, which
        WebResearchService._retain_documents uses ONLY when the request's
        own `retain_to_knowledge` is True - see that field's own docstring
        for why retention is opt-in and off everywhere in production today.
        The real caller (backend/api/intents_web_research.py's
        run_web_research) resolves the calling session's current workspace
        BEFORE calling this method, via backend/knowledge_store.py's own
        get_or_create_workspace_collection - this method only carries the
        already-resolved id, it never resolves one itself (same "caller
        resolves, this method dispatches" split as every other kwarg here)."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        # Per-NODE, for the same reason as start_artifact_reply above: a
        # canvas whose whole premise is parallel branches should be able to
        # research two of them at once, and a refusal should name the node
        # that is actually busy. Same guard gitlink_run/code_sandbox use.
        # Busy means "this node has a run the registry still holds", not
        # merely "this node has a request id". Cancelling releases the
        # registry slot immediately while the cancelled worker unwinds in
        # its own time, and during that window the node still carries the
        # dead request's id - reading the field alone would refuse the very
        # next Run the user clicks after cancelling. The placeholder is the
        # caller's own synchronous claim (see agents._NODE_RUN_CLAIM_
        # PLACEHOLDER), which has no handle yet by definition.
        pending = getattr(node, "pending_request_id", None)
        if (
            pending
            and pending != agents_module._NODE_RUN_CLAIM_PLACEHOLDER
            and self._runs.get(pending) is not None
        ):
            notifications_state.show("Web research is already running for this node.", "info")
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy()
        # check above and this claim - same load-bearing ordering every
        # other migrated surface's claim relies on, see
        # backend/run_lifecycle.py's own docstring.
        cancel_token = agents_module.CancellationToken()
        handle = self._runs.claim("web_research", node_id=node_id, on_cancel=cancel_token.cancel)
        request_id = handle.request_id
        # Stamped HERE, synchronously, not inside _run() below: the busy
        # guard is this field now, and a value written after the first await
        # would leave a window in which a second click on the same node sees
        # an idle node. Same ordering start_gitlink_run already relies on.
        node.pending_request_id = request_id
        request = agents_module.WebResearchRequest(
            request_id=request_id,
            node_id=node_id,
            chat_epoch=0,
            original_query=query,
            branch_history=list(branch_history),
            knowledge_collection_id=knowledge_collection_id,
            # ADR-021 stage 21.5: was never set by any caller, so
            # _retain_documents (graphlink_plugins/web_research/service.py)
            # was unreachable in production despite being complete and
            # tested since ADR-017.
            retain_to_knowledge=bool(retain_to_knowledge),
        )

        async def _invoke(fn, *a):
            if inspect.iscoroutinefunction(fn):
                await fn(*a)
            else:
                fn(*a)

        async def _run():
            await bus.publish("scene")
            loop = asyncio.get_running_loop()
            service = agents_module.WebResearchService()

            async def _guarded_progress(event) -> None:
                # asyncio.to_thread's underlying thread is NOT actually
                # killed by wait_for's timeout (Future.cancel() on an
                # already-running thread is a no-op - see the watchdog
                # comment on WATCHDOG_TIMEOUT_SECONDS above for the chat
                # path's identical limitation), so a slow service.run() can
                # keep calling progress() well after this request's own
                # finally block has already released its registry claim
                # and cleared node.pending_request_id. Re-check liveness here
                # (on the loop thread, so no race with the release above) and
                # drop the event if this request is no longer the active one
                # - otherwise a stale progress tick can resurrect a
                # since-failed/cancelled node's stage, or clobber a brand
                # new run started on the same node in the meantime.
                if self._runs.get(request_id) is None:
                    return
                await _invoke(on_progress, event)

            def _thread_on_progress(event) -> None:
                # Runs on the WORKER THREAD (inside asyncio.to_thread). Given
                # the low event frequency (<=16 events per run), this
                # deliberately does NOT need the token-streaming pipeline's
                # Queue+_pump batching machinery - a single
                # run_coroutine_threadsafe per event is simpler and still
                # correctly ordered, because service.run() calls progress()
                # synchronously and single-threaded, and each event's
                # coroutine mutates SceneNode fields synchronously before its
                # first await, so asyncio's FIFO call_soon scheduling
                # preserves emission order even if the subsequent
                # bus.publish("scene") awaits interleave.
                asyncio.run_coroutine_threadsafe(_guarded_progress(event), loop)

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        service.run, request, token=cancel_token, progress=_thread_on_progress
                    ),
                    timeout=agents_module.WEB_RESEARCH_WATCHDOG_TIMEOUT_SECONDS,
                )
                if self._runs.get(request_id) is None:
                    # 6.2 review fix: a cancel popped this handle while the
                    # blocking call was finishing - the same discriminator
                    # _guarded_progress already uses for progress events, now
                    # applied to the TERMINAL callbacks too. Without it, a
                    # cancelled run's late result (or its RequestCancelled
                    # below) writes stale stage/result state onto a node a
                    # replacement run may already own, since release-on-cancel
                    # freed the "web_research" slot the instant cancel landed.
                    return
                await _invoke(on_success, result)
                await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_token.cancel()
                message = (
                    "Web research stopped responding before the request completed. "
                    "Please try again."
                )
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, agents_module.ResearchFailure(message, code="watchdog_timeout"))
                notifications_state.show(message, "error")
                await bus.publish("notification")
                await bus.publish("scene")
            except agents_module.RequestCancelled as exc:
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, exc)
                notifications_state.show("Web research cancelled.", "info")
                await bus.publish("notification")
                await bus.publish("scene")
            except agents_module.ResearchFailure as exc:
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, exc)
                notifications_state.show(f"Web research failed: {exc}", "error")
                await bus.publish("notification")
                await bus.publish("scene")
            except Exception as exc:
                agents_module.logger.exception("web research dispatch failed")
                if self._runs.get(request_id) is not None:
                    await _invoke(on_failure, exc)
                notifications_state.show(f"Web research failed: {exc}", "error")
                await bus.publish("notification")
                await bus.publish("scene")
            finally:
                self._runs.release(request_id)
                # 6.2 review fix: same stale-task guard as artifact/gitlink -
                # a cancelled run's late unwind must not wipe a replacement
                # run's in-flight marker on this same node.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    def cancel_web_research(self, request_id: str) -> bool:
        """kind="web_research": ADR-002 stage 2.4e - the first surface to
        actually exercise RunHandle.on_cancel (added in stage 2.4b), since
        CancellationToken.cancel is not a threading.Event.set. See
        RunRegistry.cancel's own docstring for why kind= is passed."""
        return self._runs.cancel(request_id, kind="web_research")

    async def start_artifact_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        current_artifact: str,
        history: list,
        on_reply,
        on_failure=None,
    ) -> None:
        """R5.2: the Artifact/Drafter independent-slot counterpart to
        start_image_reply/start_web_research above - NOT a variant of
        _dispatch, since _dispatch is hardcoded to a single-string on_reply
        contract and a fixed driver function, while _call_artifact_agent
        returns a two-element tuple and must run its own fail-closed
        tag-parsing/raise (see ArtifactAgent.get_response) before any
        mutation callback fires. Guarded by self._runs's "artifact" kind,
        kept fully SEPARATE from chat/conversation's own "chat" kind,
        image's own "image" kind, and web_research's own "web_research"
        kind - see that field's own comment in __init__ for why this must
        stay independent.

        Cooperative cancellation only, via a threading.Event (not the
        CancellationToken web-research uses - ArtifactAgent has no such
        primitive) - same honestly-documented limitation as every other
        dispatch surface: ArtifactAgent.get_response has no cancellation
        checkpoint of its own. The checkpoint is deliberately placed AFTER
        the blocking call returns: if cancel_event is set by then, on_reply
        is simply never called, so the document is left untouched.

        Reuses WATCHDOG_TIMEOUT_SECONDS (420s), not a new constant:
        ArtifactAgent.get_response makes exactly ONE blocking
        api_provider.chat() call (see _call_artifact_agent below), the same
        call-count as chat's own _call_chat_agent - Web Research's own 900s
        bump exists specifically because WebResearchService.run chains ~10
        sequential calls inside one outer timeout, which does not apply
        here.

        ADR-002 stage 2.4d: migrated onto self._runs - claim()/release()/
        attach_task() directly, the exact same fire-and-forget pattern
        chat's own _dispatch and image's own start_image_reply already
        use. node.pending_request_id below is set inside _run() itself,
        AFTER the claim already landed in this outer coroutine - it is a
        UI-bookkeeping side channel only (never consulted for the busy
        guard, unlike gitlink_run/code_sandbox's use of the same
        field), so it needs no claim-ordering treatment of its own."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        # Per-NODE, not per-kind. RunRegistry.is_busy() is kind-scoped and
        # session-wide (RunHandle's own docstring: node_id is "informational
        # only ... NEVER consulted"), so one artifact node in flight blocked
        # every other artifact node on the canvas - and the refusal could not
        # say which node was holding the slot. gitlink_run/code_sandbox
        # already guard on node.pending_request_id for exactly this reason
        # (see AgentDispatcher.__init__'s own comment); this kind now does
        # too, and the registry claim below stays purely for cancel/task
        # bookkeeping so cancel()/cancel_all() sweeps are unchanged.
        # Busy means "this node has a run the registry still holds", not
        # merely "this node has a request id". Cancelling releases the
        # registry slot immediately while the cancelled worker unwinds in
        # its own time, and during that window the node still carries the
        # dead request's id - reading the field alone would refuse the very
        # next Run the user clicks after cancelling. The placeholder is the
        # caller's own synchronous claim (see agents._NODE_RUN_CLAIM_
        # PLACEHOLDER), which has no handle yet by definition.
        pending = getattr(node, "pending_request_id", None)
        if (
            pending
            and pending != agents_module._NODE_RUN_CLAIM_PLACEHOLDER
            and self._runs.get(pending) is not None
        ):
            notifications_state.show("Artifact generation is already running for this node.", "info")
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy()
        # check above and this claim - same load-bearing ordering
        # _dispatch's/start_image_reply's own claims rely on, see
        # backend/run_lifecycle.py's own docstring.
        cancel_event = threading.Event()
        handle = self._runs.claim("artifact", node_id=getattr(node, "id", None), cancel_event=cancel_event)
        request_id = handle.request_id
        # Synchronous stamp - see start_web_research's own comment above.
        node.pending_request_id = request_id
        # Callers that do not record failures on the node (tests, and any
        # future dispatch surface with no document) keep working unchanged.
        record_failure = on_failure if on_failure is not None else (lambda _message: None)

        async def _run():
            await bus.publish("scene")
            try:
                new_content, ai_message = await asyncio.wait_for(
                    asyncio.to_thread(agents_module._call_artifact_agent, current_artifact, history),
                    timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    notifications_state.show("Artifact generation cancelled.", "info")
                    await bus.publish("notification")
                else:
                    if inspect.iscoroutinefunction(on_reply):
                        await on_reply(new_content, ai_message)
                    else:
                        on_reply(new_content, ai_message)
                    await bus.publish("scene")
            except asyncio.TimeoutError:
                cancel_event.set()
                message = (
                    "Artifact generation stopped responding before the request completed. "
                    "Please try again."
                )
                # Recorded on the node as well as toasted: the toast says
                # something failed, the node says WHICH one.
                record_failure(message)
                notifications_state.show(message, "error")
                await bus.publish("notification")
            except Exception as exc:
                if cancel_event.is_set():
                    # 6.2 review fix: a cancelled run whose provider call then
                    # errors ends quietly - the user already asked for it to
                    # stop; an "Artifact generation failed" toast would be
                    # noise about work they abandoned.
                    return
                agents_module.logger.exception("artifact dispatch failed")
                message = f"Artifact generation failed: {exc}"
                record_failure(message)
                notifications_state.show(message, "error")
                await bus.publish("notification")
            finally:
                self._runs.release(request_id)
                # 6.2 review fix (reproduced live): only clear if this task's
                # OWN request_id is still the one recorded - the same
                # stale-task guard every gitlink/sandbox finally has.
                # Release-on-cancel frees the "artifact" slot the instant a
                # cancel lands, so a NEW artifact run can claim and stamp
                # this same node before this old worker unwinds; the old
                # unconditional clear wiped the new run's in-flight marker.
                if node.pending_request_id == request_id:
                    node.pending_request_id = None
                await bus.publish("scene")

        self._runs.attach_task(handle, asyncio.create_task(_run()))

    # -- R5.3: Gitlink ------------------------------------------------------
    #
    # Four PLAIN async methods below (fetch_gitlink_repositories/
    # load_gitlink_repo_tree/import_gitlink_snapshot/build_gitlink_context) -
    # NO dict-tracking: the caller (backend/canvas.py's register_canvas) already
    # guards busy-state via node.pending_request_id directly before calling,
    # and each of these is awaited DIRECTLY by that caller (not scheduled via
    # asyncio.create_task the way start_chat_reply/start_web_research/
    # start_artifact_reply/start_gitlink_run/start_gitlink_apply are) - there
    # is no natural intermediate UI state beyond "loading" for a one-shot
    # listing/import/context-build action, and the caller needs the result
    # back in the same round trip. node.pending_request_id is still the busy
    # marker for the duration (see AgentDispatcher.__init__'s own comment on
    # why every Gitlink action - including these four - shares that one
    # field); it is set/cleared inline here rather than via a background task.

    def cancel_artifact(self, request_id: str) -> bool:
        """kind="artifact": ADR-002 stage 2.4d - the first surface to
        actually exercise RunRegistry.cancel()'s kind= filter (added in
        stage 2.4b) for real, now that artifact shares self._runs
        alongside chat/image, both also cancel_event-bearing. Without
        this filter a stale or mismatched request_id could trip the wrong
        kind's in-flight run - see RunRegistry.cancel's own docstring."""
        return self._runs.cancel(request_id, kind="artifact")
