"""ContentDispatchOps - single-shot content generation: charts, notes,
branch comparison, and branch synthesis.

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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.events import SessionBus

from backend.agent_dispatch._composed import DispatcherParts


class ContentDispatchOps(DispatcherParts):
    """Chart, note, branch-comparison, and branch-synthesis generation (mixin - see module docstring)."""

    async def start_chart_generation(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node_id: str,
        chart_type: str,
        source_text: str,
        on_success,
        on_failure,
    ) -> None:
        """R6.2: Chart generation - DIRECTLY AWAITED by its caller
        (backend/canvas.py's generateChart), NOT scheduled via
        asyncio.create_task the way start_image_reply/start_web_research/
        start_artifact_reply/start_gitlink_run above are. Those four are all
        fire-and-forget precisely because generation there fills in an
        ALREADY-EXISTING node while the WS connection's read loop moves on
        to keep reading further messages - but generateChart's own contract
        is a single combined create+generate action with no pre-existing
        node at all: the chart SceneNode is only ever created inside
        on_success below, so the caller genuinely needs the finished result
        (and the new node id it produces) back in the SAME round trip before
        it can return anything meaningful to the client. This is the exact
        same shape - and reasoning - as the Gitlink read-only helpers just
        above (fetch_gitlink_repositories/load_gitlink_repo_tree/etc.): "no
        natural intermediate UI state beyond loading for a one-shot action,
        and the caller needs the result back in the same round trip" (see
        that section's own comment). Legacy's own generate_chart likewise
        shows a blocking loading animation for the duration, not a
        fire-and-forget spinner elsewhere - the same UX this mirrors.

        Still guarded by self._runs's "chart" kind (ADR-002 stage 2.3 -
        see backend/run_lifecycle.py) - there is no background task to
        hold onto, only a "one generation in flight for this session"
        marker, so two overlapping generateChart calls (e.g. from two tabs
        open on the same session) cannot race each other.

        ADR-013 stage 13.3: `_call_chart_agent` now receives run_single_shot's
        own cancel_event and forwards it to respond_json as a real
        cancellation_event - a genuine interruption (api_provider.
        RequestCancelledError), not the "no cancellation checkpoint of its
        own" gap this docstring used to document for ChartDataAgent
        (retired this same stage). run_single_shot's own exception handling
        already treats a caught exception while cancel_event.is_set() as a
        silent cancel rather than a failure, so a cancelled generation
        neither calls on_failure nor shows a notification.

        Two distinct failure shapes, both routed through on_failure plus a
        notification, NEITHER of which creates a node (node creation only
        ever happens in on_success):
          1. `_call_chart_agent` returns a dict carrying a top-level "error"
             key - respond_json's StructuredOutputError case (the model
             could not produce schema-conforming JSON even after its own
             one repair attempt).
          2. A timeout or any other exception raised getting there.
        A dict with NO "error" key is still not guaranteed to be canonical -
        on_success (backend/canvas.py's own closure) is responsible for its
        own defensive canonicalize_chart_data/ChartDataError handling before
        calling document.add_chart_node, exactly as this feature's own
        contract requires; this method's job ends at handing back whatever
        _call_chart_agent produced.

        Reuses WATCHDOG_TIMEOUT_SECONDS (420s), not a new constant:
        respond_json makes at most TWO sequential blocking api_provider.
        chat() calls (the initial extraction call, plus one repair round
        trip on a non-canonical first attempt) - double Artifact's own
        single-call shape, but nowhere near Web Research's ~10-call chain
        that justified ITS own 900s bump, and 420s already carries ample
        headroom for two calls at any realistic per-call latency.

        ADR-002 stage 2.3: the guard/timeout/exception/notify skeleton
        below now lives once, shared with start_note_generation, in
        backend/run_lifecycle.py's run_single_shot - see that function's
        own docstring. Every message string and every branch's exact
        behavior (including the asymmetry where a top-level "error" key
        hands on_failure the RAW error text but prefixes the toast
        notification with "Chart generation failed: ") is unchanged from
        this method's pre-2.3 body."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        await agents_module.run_single_shot(
            self._runs,
            kind="chart",
            bus=bus,
            notifications_state=notifications_state,
            node_id=node_id,
            timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
            call=lambda cancel_event: agents_module._call_chart_agent(source_text, chart_type, cancel_event),
            validate=lambda result: (
                str(result["error"]) if isinstance(result, dict) and "error" in result else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message="A chart is already being generated.",
            timeout_message=(
                "Chart generation stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix="Chart generation failed",
            log_exception=lambda exc: agents_module.logger.exception(
                "chart generation dispatch failed (parent node %s)", node_id
            ),
            validate_notify=lambda message: f"Chart generation failed: {message}",
        )

    async def start_note_generation(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        node_id: str,
        note_kind: str,
        source_text: str,
        on_success,
        on_failure,
    ) -> None:
        """R8a: Key Takeaway / Explainer Note generation.

        DIRECTLY AWAITED by its caller rather than scheduled via
        asyncio.create_task, the same shape as start_chart_generation above
        and for the same reason: the result is a brand new note node, so the
        caller needs it back in the same round trip and there is no
        pre-existing node to attach a spinner to.

        `note_kind` selects the agent ("takeaway" | "explainer"). One method
        rather than two near-identical ones because the two differ ONLY in
        which agent class runs and how failures are worded - the guard,
        timeout, callback and cleanup logic are identical, and duplicating
        them would be two places to fix every future bug in.

        ADR-002 stage 2.3: that shared skeleton now lives once, in
        backend/run_lifecycle.py's run_single_shot, shared with
        start_chart_generation too - see that function's own docstring.
        Every message string and every branch's exact behavior is
        unchanged from this method's pre-2.3 body.
        """
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        label = agents_module.NOTE_AGENT_LABELS.get(note_kind, "Note")

        await agents_module.run_single_shot(
            self._runs,
            kind="note",
            bus=bus,
            notifications_state=notifications_state,
            node_id=node_id,
            timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
            call=lambda _cancel_event: agents_module._call_note_agent(note_kind, source_text),
            validate=lambda text: (
                # An agent that returns nothing usable must not silently
                # create an empty note - that reads as a broken feature.
                f"{label} generation returned an empty response. Please try again."
                if not str(text or "").strip() else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message=f"A {label.lower()} is already being generated.",
            timeout_message=(
                f"{label} generation stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix=f"{label} generation failed",
            log_exception=lambda exc: agents_module.logger.exception(
                "%s generation dispatch failed (source node %s)", label, node_id
            ),
        )

    async def start_branch_comparison(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        source_text: str,
        on_success,
        on_failure,
    ) -> None:
        """ADR-002 Workstream 1 ("Compare Branches"): mirrors start_note_
        generation's own shape exactly (directly awaited - the result is a
        brand new note node and there is no pre-existing node to attach a
        spinner to; single-slot busy guard; WATCHDOG_TIMEOUT_SECONDS;
        on_success/on_failure callbacks) but with its own "branch_
        comparison" kind in self._runs rather than reusing "note" - see
        that field's own comment in __init__ for why. source_text is
        already the fully-formatted multi-branch block (backend/canvas.py's
        _format_branches_for_comparison) - this method itself is agnostic
        to how many branches went into it.

        ADR-002 stage 2.4: the guard/timeout/exception/notify skeleton
        below now lives once, in backend/run_lifecycle.py's
        run_single_shot - the same primitive start_chart_generation/
        start_note_generation already share. Every message string and
        every branch's exact behavior is unchanged from this method's
        pre-2.4 body."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        await agents_module.run_single_shot(
            self._runs,
            kind="branch_comparison",
            bus=bus,
            notifications_state=notifications_state,
            node_id=None,
            timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
            call=lambda _cancel_event: agents_module._call_branch_comparison_agent(source_text),
            validate=lambda text: (
                "Branch comparison returned an empty response. Please try again."
                if not str(text or "").strip() else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message="A branch comparison is already being generated.",
            timeout_message=(
                "Branch comparison stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix="Branch comparison failed",
            log_exception=lambda exc: agents_module.logger.exception("branch comparison dispatch failed"),
        )

    async def start_branch_synthesis(
        self,
        *,
        bus: SessionBus,
        notifications_state,
        source_text: str,
        instructions: str,
        on_success,
        on_failure,
    ) -> None:
        """ADR-002 Workstream 1 ("Synthesize Branches"): mirrors start_branch_
        comparison's own shape exactly (directly awaited - the result is a
        brand new chat node and there is no pre-existing node to attach a
        spinner to; single-slot busy guard; WATCHDOG_TIMEOUT_SECONDS;
        on_success/on_failure callbacks) but with its own "branch_synthesis"
        kind in self._runs rather than reusing "branch_comparison" - see
        that field's own comment in __init__ for why. source_text is
        already the fully-formatted multi-branch block (backend/canvas.py's
        _format_branches_for_comparison, reused verbatim here - a
        labeled-branches text block is equally valid input whether the
        agent on the other end compares or synthesizes); instructions is
        the user's own free text steering the synthesis.

        ADR-002 stage 2.4: shares run_single_shot with start_branch_
        comparison above - see that method's own docstring."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        await agents_module.run_single_shot(
            self._runs,
            kind="branch_synthesis",
            bus=bus,
            notifications_state=notifications_state,
            node_id=None,
            timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
            call=lambda _cancel_event: agents_module._call_branch_synthesis_agent(source_text, instructions),
            validate=lambda text: (
                "Branch synthesis returned an empty response. Please try again."
                if not str(text or "").strip() else None
            ),
            on_success=on_success,
            on_failure=on_failure,
            busy_message="A branch synthesis is already being generated.",
            timeout_message=(
                "Branch synthesis stopped responding before the request completed. "
                "Please try again."
            ),
            exception_prefix="Branch synthesis failed",
            log_exception=lambda exc: agents_module.logger.exception("branch synthesis dispatch failed"),
        )
