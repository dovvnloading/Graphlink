"""ChatDispatchOps - the chat-shaped reply surfaces: ChatNode streaming
replies, ConversationNode replies, and image generation.

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


class ChatDispatchOps(DispatcherParts):
    """Chat, conversation, and image reply surfaces (mixin - see module docstring)."""

    async def start_chat_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        composer_document,
        conversation_history,
        on_reply,
        stream: bool = True,
        canvas_document=None,
        node_id: str | None = None,
        on_partial=None,
        on_usage=None,
        on_begin=None,
        on_end=None,
        state_topic: str | None = None,
    ) -> None:
        # `canvas_document`/`node_id` (R6.1): optional, forwarded straight
        # through to _dispatch for branch-system-prompt-override resolution -
        # see that method's own docstring. Both default None so every
        # pre-R6.1 caller (there are many across test_agents.py) keeps
        # working unchanged, falling back to persona()'s existing
        # resolution.
        #
        # ADR-006 stage 6.4: `on_begin`/`on_end`/`state_topic` become
        # overridable. The defaults keep the Composer identity (its live
        # preview binds stream frames via the app-composer snapshot's
        # request.id) - regenerate_response overrides all three to a
        # NODE-scoped identity (the target node's own pending_request_id,
        # republished on "scene"), which is what lets regenerate stream INTO
        # its node without ever lighting the Composer preview - the exact
        # confusion the old stream=False deferral existed to prevent, now
        # dissolved by giving the frames a different subscriber identity
        # instead of suppressing them. `on_partial` forwards to _dispatch's
        # partial-output preservation (see its docstring).
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=on_begin if on_begin is not None else composer_document.begin_request,
            on_end=on_end if on_end is not None else composer_document.end_request,
            state_topic=state_topic if state_topic is not None else "app-composer",
            stream=stream,
            canvas_document=canvas_document,
            node_id=node_id,
            on_partial=on_partial,
            # ADR-006 stage 6.8: caller-supplied real-usage callback (see
            # _dispatch) - intents_chat wires it to the token counter.
            on_usage=on_usage,
        )

    async def start_conversation_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node,
        conversation_history,
        on_reply,
        on_partial=None,
    ) -> None:
        """R4.3's ConversationNode equivalent of start_chat_reply: same
        _dispatch pipeline, but the in-flight request_id lives on the
        ConversationNode itself (`node.pending_request_id`, duck-typed - this
        module does not import canvas.py's SceneNode) rather than on
        ComposerDocument, and "scene" (not "app-composer") is republished
        around that change so the node's own in-flight state refreshes.

        ADR-006 stage 6.4: streams. R4.4's deferral is closed - the frames
        are keyed by the request_id on_begin just wrote into
        node.pending_request_id (published on "scene"), which is exactly the
        node-scoped subscription contract CodeSandboxNodeView already
        established, so the Composer preview never lights up for a
        conversation reply. `on_partial` commits accumulated text when the
        stream dies mid-reply (H5)."""
        return await self._dispatch(
            bus=bus,
            notifications_state=notifications_state,
            conversation_history=conversation_history,
            on_reply=on_reply,
            on_begin=lambda request_id: setattr(node, "pending_request_id", request_id),
            on_end=lambda: setattr(node, "pending_request_id", None),
            state_topic="scene",
            stream=True,
            on_partial=on_partial,
        )

    async def start_image_reply(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        prompt: str,
        on_reply,  # on_reply(image_bytes: bytes) -> None | Awaitable
    ) -> None:
        """R4.4a: the independent-slot counterpart to _dispatch, NOT a
        variant of it - image generation has no conversation_history/
        persona/on_begin/on_end/state_topic shape (there is no per-node
        "generating" flag to toggle the way ComposerDocument.request_state or
        a ConversationNode's pending_request_id do; the frontend shows a
        transient "Generating image..." notification instead of a per-node
        spinner). Guarded by self._runs's "image" kind, kept fully SEPARATE
        from chat/conversation's own "chat" kind - see that field's own
        comment in __init__ for why this must stay independent rather than
        reusing the existing single-slot guard.

        ADR-006 stage 6.2: claim() now passes a cancel_event -
        api_provider.generate_image still has no cancellation_event
        parameter and no mid-call checkpoint (it is one blocking network
        POST), so the event's effect is post-return suppression plus
        immediate slot release on cancel/disconnect; it cannot shorten the
        POST itself. (Legacy had zero cancel affordance here at all -
        ImageGenerationWorkerThread.stop() existed but was never called
        from any UI path.) The WATCHDOG_TIMEOUT_SECONDS
        ceiling IS still applied here even though legacy has none for image
        generation - a deliberate, explicitly-flagged improvement (leaving
        this as the only dispatch surface with no ceiling against a hung
        external HTTP call would be an unforced gap, not considered legacy
        design), not silent parity.

        ADR-002 stage 2.4c: migrated onto self._runs (RunRegistry) -
        claim()/release()/attach_task() directly, the same fire-and-forget
        pattern _dispatch's own chat/conversation migration established in
        stage 2.3 (claim SYNCHRONOUSLY in this coroutine, before scheduling
        the background task; release happens inside that task's own
        finally). Not run_single_shot: this surface is fire-and-forget
        (NOT awaited by its own caller), the other of the two dispatch
        shapes that primitive does not cover - see backend/run_lifecycle.py's
        own docstring."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if self._runs.is_busy("image"):
            # Single in-flight-image-request-per-session guard, mirroring
            # _dispatch's own "A response is already being generated." guard
            # in shape but tracked on the independent "image" kind, never
            # chat's own.
            notifications_state.show("An image is already being generated.", "info")
            await bus.publish("notification")
            return

        # Claimed SYNCHRONOUSLY, with no `await` between the is_busy() check
        # above and this claim - same load-bearing ordering _dispatch's own
        # claim relies on, see backend/run_lifecycle.py's own docstring.
        # ADR-006 stage 6.2: image gains a cancel_event. generate_image
        # still has no mid-call checkpoint (one blocking POST - the
        # docstring above remains true), so cancellation is post-return
        # suppression, the same shape artifact already uses: cancel frees
        # the slot immediately (RunRegistry.cancel), and when the POST
        # eventually returns, the result is discarded instead of applied.
        cancel_event = threading.Event()
        handle = self._runs.claim("image", cancel_event=cancel_event)
        request_id = handle.request_id

        async def _run():
            try:
                image_bytes = await asyncio.wait_for(
                    asyncio.to_thread(
                        agents_module.api_provider.generate_image,
                        prompt,
                        # ADR-006 stage 6.5: non-default sessions only - see
                        # _runtime_kwargs' own docstring.
                        **self._runtime_kwargs(),
                    ),
                    timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
                )
                if cancel_event.is_set():
                    return
                if inspect.iscoroutinefunction(on_reply):
                    await on_reply(image_bytes)
                else:
                    on_reply(image_bytes)
                # Unlike _dispatch, "scene" is NOT published here on success -
                # on_reply itself (canvas.py's _dispatch_image._on_reply)
                # already publishes "scene" after mutating the document, so a
                # second unconditional publish here would be redundant.
            except asyncio.TimeoutError:
                if cancel_event.is_set():
                    return  # cancelled runs end quietly, not with a timeout toast
                notifications_state.show(
                    "Image generation stopped responding before the request "
                    "completed. Please try again.",
                    "error",
                )
                await bus.publish("notification")
            except Exception as exc:
                # Catches api_provider.generate_image's real gating
                # RuntimeErrors (not API mode / no client / Anthropic
                # unsupported / no model configured / quota exceeded) and any
                # other failure the same way, matching _dispatch's own
                # generic "AI response failed: {exc}" catch-all shape - exc's
                # own text is forwarded verbatim after one shared prefix so
                # api_provider.py's distinct messages stay distinguishable to
                # the user without the WS layer duplicating that gating
                # knowledge.
                agents_module.logger.exception("image generation dispatch failed")
                notifications_state.show(f"Image generation failed: {exc}", "error")
                await bus.publish("notification")
            finally:
                # Unconditional on every exit path so the slot never leaks -
                # a future request must always be admitted once this one is
                # done, success or failure.
                self._runs.release(request_id)

        # NOT awaited here, same load-bearing reason _dispatch's own _run
        # task is not awaited inline - the WS connection's read loop must
        # keep reading further messages on this same socket while a
        # generation is in flight. The claim itself already landed above,
        # before this task was even created.
        self._runs.attach_task(handle, asyncio.create_task(_run()))
