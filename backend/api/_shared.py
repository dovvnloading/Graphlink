"""ADR-002 stage 2.6: the one genuinely cross-cutting helper every
register_*_intents module needs.

publish_scene was a single closure inside register_canvas's own body
(`async def publish_scene(): await bus.publish("scene")`), invoked by
roughly 60 of its ~89 intents across every feature area - by far the most
pervasive dependency in the file. Once those intents scattered across
many modules, that single closure can no longer be shared directly; each
module instead calls make_publish_scene(bus) once, at the top of its own
register_*_intents function, to get its own equivalent callable. Relocated
verbatim from backend/canvas.py's former register_canvas (lines 288-289).

publish_token_counter (former lines 294-307) is used by exactly two
intents (sendMessage, regenerateResponse), both of which live in
backend/api/intents_chat.py - factored here anyway (not left as a local
closure there) only because intents_chat.py is already the tightest of
the split modules against the ADR's 300-line-per-registration-function
cap; see that module's own docstring.

make_publish_grid (ADR-002 stage 2.6 PR3) is publish_scene's grid-control
counterpart, needed once backend/api/intents_grid.py's own
register_grid_intents becomes the last consumer of register_canvas's
former local `publish_grid` closure. Relocated verbatim from
backend/canvas.py's former register_canvas (former lines 302-303).

claim_busy_node_or_notify factors out the single-in-flight-run-per-node
busy guard that backend/api/intents_gitlink.py's runGitlinkChangeSet and
backend/api/intents_code_sandbox.py's runCodeSandbox each hand-wrote
identically (down to the synchronous placeholder-claim-before-any-await
race fix - see run_gitlink_change_set's own inline comment, still the
canonical explanation, for exactly why that ordering matters). The two
callers differ only in their busy/placeholder values and in what
"start the run" means for their own node kind - everything else, including
the SceneError recovery path, was copy-pasted.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState


def make_publish_scene(bus: SessionBus):
    async def publish_scene():
        await bus.publish("scene")

    return publish_scene


def make_publish_grid(bus: SessionBus):
    async def publish_grid():
        await bus.publish("grid-control")

    return publish_grid


def make_publish_token_counter(bus: SessionBus):
    # R8a: guarded, unlike publish_scene above - "scene" is registered in
    # register_canvas itself, always present by construction. token_counter's
    # own topic is registered elsewhere (backend/token_counter.py's
    # register_token_counter, called once per session in backend/app.py),
    # and the ~30 pre-R8a canvas/agents tests that construct a bare
    # SessionBus + register_canvas directly never call it - has_topic keeps
    # sendMessage/regenerateResponse working in exactly those tests instead
    # of raising UnknownTopicError the first time either intent runs.
    async def publish_token_counter():
        if bus.has_topic("token-counter"):
            await bus.publish("token-counter")

    return publish_token_counter


async def claim_busy_node_or_notify(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    node_id: str,
    *,
    busy_message: str,
    placeholder: str,
    start_run: Callable[[], Any],
) -> Any | None:
    """Shared busy-guard + synchronous placeholder-claim for node kinds that
    allow only one in-flight run at a time (Gitlink's runGitlinkChangeSet,
    Execution Sandbox's runCodeSandbox).

    The busy pre-check and the placeholder claim happen in this same
    synchronous stretch, before `start_run()` or any await - a second
    concurrent call for the SAME node_id must never be able to pass the
    pre-check during a later `await publish_scene()` gap. `start_run`'s own
    dispatcher (the only real caller of the node's pending_request_id after
    this point) recognizes this exact placeholder and overwrites it with the
    real request_id, still synchronously.

    Returns whatever `start_run()` returns on success, or None (after
    already publishing a notification) if the node was busy, or if
    `start_run()` raised SceneError (node deleted/wrong-kind concurrently
    with the claim) - in which case the placeholder is cleared so it doesn't
    linger on a node this call is giving up on."""
    node_for_check = document.nodes.get(node_id)
    if node_for_check is not None and node_for_check.pending_request_id:
        notifications.show(busy_message, "info")
        await bus.publish("notification")
        return None
    if node_for_check is not None:
        node_for_check.pending_request_id = placeholder
    try:
        return start_run()
    except SceneError:
        if node_for_check is not None:
            node_for_check.pending_request_id = None
        notifications.show("This node no longer exists.", "warning")
        await bus.publish("notification")
        return None
