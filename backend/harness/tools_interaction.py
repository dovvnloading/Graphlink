"""`plan.update` and `user.ask` (PLAN-2026-08-24 §2.3/§3.2.6).

The two tools in the plan's convergent core set that are about the AGENT'S
RELATIONSHIP TO THE PERSON rather than about the workspace.

**`plan.update`** externalizes the model's own checklist. It is explicitly
NOT the Builder's plan: nothing gates on it, no step needs approving, and
the loop never reads it back. Its whole job is legibility - a 15-turn run
otherwise presents as an undifferentiated wall of tool calls, and a person
deciding whether to let it keep going needs to see the shape of the work.
Registered `approval="auto"` under `graph.read`: writing a checklist onto
the node the model is already running in is not a capability anyone needs
to consent to, and a tool that prompts is a tool the model stops using.

**`user.ask`** is the mid-run structured question. This is the one tool
that inverts the loop's direction: instead of the model acting and
reporting, it stops and blocks on a human. It reuses the EXACT mechanism
the approval gate already uses - a future parked on the RunHandle, cleared
in a `finally` so no exit path strands a phantom prompt - because that
machinery already solves cancel-means-resolve, disconnect auto-resolve,
and the "run is parked, not hung" UI state.

Both write through the run context's own callbacks rather than importing
the loop: the loop owns the node, the bus, and the RunHandle, so it hands
down exactly the two capabilities these need. That keeps this module free
of a circular import and makes both tools trivially testable with a fake
context.
"""

from __future__ import annotations

from backend.providers.base import ToolCall, ToolSpec
from backend.tools import GRAPH_READ, PROVIDER_CALL, RunContext, ToolRegistry, ToolResult

# A checklist longer than this is not a checklist. Bounded for the same
# reason every other model-authored list in this codebase is: the model
# writes it, so nothing but a cap constrains it.
MAX_PLAN_STEPS = 20
MAX_STEP_CHARS = 200
MAX_QUESTION_CHARS = 2_000
MAX_ANSWER_CHARS = 4_000

_VALID_STATUSES = ("pending", "active", "done")

PLAN_UPDATE_SPEC = ToolSpec(
    name="plan.update",
    description=(
        "Record or revise your checklist for the current task, shown to the "
        "user as you work. Send the WHOLE list every time - it replaces the "
        "previous one. Use it for multi-step work so the person can see what "
        "you intend and where you are; skip it for anything you will finish "
        f"in a turn or two. At most {MAX_PLAN_STEPS} steps."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "status": {"type": "string", "enum": list(_VALID_STATUSES)},
                    },
                    "required": ["text"],
                },
            },
        },
        "required": ["steps"],
    },
)

USER_ASK_SPEC = ToolSpec(
    name="user.ask",
    description=(
        "Ask the user a question and wait for their typed answer. Use this "
        "ONLY when you genuinely cannot proceed without a decision that is "
        "theirs to make - an ambiguous requirement, a destructive choice, a "
        "missing credential. Do not use it to confirm work you can simply "
        "do, and never use it to re-ask for something already denied. The "
        "run is blocked until they reply."
    ),
    input_schema={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
)


def _normalize_steps(raw) -> "list[dict[str, str]] | None":
    """Coerce model-authored input into the stored row shape, or None when
    it is not a list at all. Tolerant per-row (a row missing a status gets
    'pending'; a bad status falls back rather than failing the call) but
    strict about the container - the model was told to send a list."""
    if not isinstance(raw, list):
        return None
    steps: list[dict[str, str]] = []
    for item in raw[:MAX_PLAN_STEPS]:
        if isinstance(item, str):
            text, status = item, "pending"
        elif isinstance(item, dict):
            text = str(item.get("text") or "")
            status = str(item.get("status") or "pending")
        else:
            continue
        text = text.strip()
        if not text:
            continue
        if status not in _VALID_STATUSES:
            status = "pending"
        steps.append({"text": text[:MAX_STEP_CHARS], "status": status})
    return steps


def register_harness_interaction_tools(registry: ToolRegistry) -> None:
    async def plan_update(call: ToolCall, ctx: RunContext) -> ToolResult:
        steps = _normalize_steps(call.arguments.get("steps"))
        if steps is None:
            return ToolResult(content="plan.update needs `steps` to be a list.", is_error=True)
        writer = getattr(ctx, "set_plan", None)
        if writer is None:
            return ToolResult(content="This run has no plan surface.", is_error=True)
        await writer(steps)
        if not steps:
            return ToolResult(content="Checklist cleared.")
        done = sum(1 for step in steps if step["status"] == "done")
        return ToolResult(content=f"Checklist updated: {done}/{len(steps)} done.")

    async def user_ask(call: ToolCall, ctx: RunContext) -> ToolResult:
        question = str(call.arguments.get("question") or "").strip()
        if not question:
            return ToolResult(content="user.ask needs a non-empty question.", is_error=True)
        asker = getattr(ctx, "ask_user", None)
        if asker is None:
            return ToolResult(content="This run cannot ask the user a question.", is_error=True)
        answer = await asker(question[:MAX_QUESTION_CHARS])
        if answer is None:
            # Dismissed rather than answered. Reported as an ordinary result,
            # not an error: "they declined to say" is information the model
            # should reason about, not a malformed-call signal.
            return ToolResult(content="The user dismissed the question without answering.")
        return ToolResult(content=f"The user answered: {str(answer)[:MAX_ANSWER_CHARS]}")

    registry.register(PLAN_UPDATE_SPEC, plan_update, scopes={GRAPH_READ}, approval="auto")
    # provider.call, not graph.read: blocking the loop on a human is a
    # capability of the RUN, and provider.call is the scope every harness
    # run necessarily holds (it is what lets the loop call a model at all),
    # so gating on it keeps this tool available exactly when a loop exists
    # to be blocked - and absent from any read-only subagent that is not
    # granted it.
    registry.register(USER_ASK_SPEC, user_ask, scopes={PROVIDER_CALL}, approval="auto")
