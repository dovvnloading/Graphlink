"""ADR-002 stages 2.3+2.4: a permanent static gate on every fire-and-forget
dispatch surface's claim ordering.

Risk #1 from the stage 2.3 design review: the registry claim
(`self._runs.claim(...)`) must happen in the SAME synchronous stretch as
the kind's `is_busy(...)` pre-check, with zero `await` in between - see
backend/run_lifecycle.py's own docstring for why. This is a real
invariant, but it is NOT meaningfully provable with a dynamic race test:
asyncio coroutines only yield control at an `await` expression, so as long
as that stretch genuinely contains none, two concurrently-scheduled calls
cannot interleave there no matter how a test tries to race them - there is
no scheduler-level "unlucky timing" to trigger. (Confirmed directly for
_dispatch: temporarily inserting a real `await asyncio.sleep(0)` between
the two did NOT make the existing
test_rapid_fire_double_send_same_session_second_is_rejected fail, because
that test calls the two dispatches sequentially, each one's whole
synchronous prefix completing atomically before the next begins - it
already can't observe this cross-await interleaving hazard.) The only
regression that could ever violate this is a future EDIT adding an
`await` in that stretch - a static property, checked statically here, the
same AST-based-permanent-gate approach as tests/test_domain_purity.py.

Covers every fire-and-forget surface migrated onto RunRegistry so far -
add a new entry to MIGRATED_METHODS below as each further surface (ADR-002
stage 2.4) migrates. Not applicable to the directly-awaited run_single_shot
surfaces (chart, note, branch_comparison, branch_synthesis): those claim
and release in the SAME coroutine with no scheduling handoff, so there is
no cross-coroutine race for this gate to protect against in the first
place.
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENTS_PY = Path(__file__).resolve().parents[1] / "agents.py"

# (method_name, busy-check kind literal used only for the failure message)
MIGRATED_METHODS = [
    ("_dispatch", "chat"),
    ("start_image_reply", "image"),
    ("start_artifact_reply", "artifact"),
]


def _find_method(method_name: str):
    tree = ast.parse(AGENTS_PY.read_text(encoding="utf-8"), filename=str(AGENTS_PY))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentDispatcher":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"AgentDispatcher.{method_name} not found - did it move or get renamed?")


def _statement_calls(stmt, attr_name: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr_name
        for n in ast.walk(stmt)
    )


def _awaits_reaching_claim(statements):
    """Collects every `ast.Await` that lies on a control-flow path capable
    of reaching code AFTER `statements` (i.e. the claim) - skipping the
    body of an `if <cond>: ...; return` block with no `else`, since EVERY
    path through that specific shape either exits immediately (the
    `await`, if any, inside it never precedes a claim - the caller
    returned) or never entered the block at all (condition was false, no
    statements ran)."""
    offenders = []
    for stmt in statements:
        if isinstance(stmt, ast.If) and not stmt.orelse and stmt.body and isinstance(stmt.body[-1], (ast.Return, ast.Raise)):
            continue
        offenders.extend(n for n in ast.walk(stmt) if isinstance(n, ast.Await))
    return offenders


def _assert_no_await_between_busy_check_and_claim(method_name: str, kind: str) -> None:
    method = _find_method(method_name)
    body = method.body

    busy_check_index = next(
        (i for i, stmt in enumerate(body) if isinstance(stmt, ast.If) and _statement_calls(stmt.test, "is_busy")),
        None,
    )
    claim_index = next((i for i, stmt in enumerate(body) if _statement_calls(stmt, "claim")), None)

    assert busy_check_index is not None, (
        f"no `if ...is_busy(...):` guard found in AgentDispatcher.{method_name} - did the guard move?"
    )
    assert claim_index is not None, f"no `...claim(...)` call found in AgentDispatcher.{method_name} - did the claim move?"
    assert busy_check_index < claim_index, "the busy check must precede the claim, not follow it"

    # Both boundary statements are included, not just what lies strictly
    # between them - an `await` written directly on the busy-check or the
    # claim statement itself (e.g. a future edit turning `claim(...)` into
    # `await claim(...)`) must be caught too. Safe to include the
    # busy-check's own `If`: it matches _awaits_reaching_claim's own
    # "if ...: ...; return" exemption for the same reason an
    # is_configured()-style guard below it would, so it is correctly
    # skipped rather than double-flagging that check's own early-return
    # await.
    between = body[busy_check_index : claim_index + 1]
    offenders = [f"line {n.lineno}" for n in _awaits_reaching_claim(between)]
    assert not offenders, (
        f"an `await` was found on a path between AgentDispatcher.{method_name}'s \"{kind}\" busy-check "
        f"and its registry claim (at {', '.join(offenders)}) - this reopens the exact concurrent-"
        "double-admit race RunRegistry.claim()'s synchronous-by-design contract exists to close. "
        "See this test module's own docstring, and backend/run_lifecycle.py's."
    )


def test_no_await_between_busy_check_and_registry_claim():
    for method_name, kind in MIGRATED_METHODS:
        _assert_no_await_between_busy_check_and_claim(method_name, kind)
