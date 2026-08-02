"""ADR-002 stage 2.3: a permanent static gate on AgentDispatcher._dispatch's
claim ordering.

Risk #1 from the stage 2.3 design review: the registry claim
(`self._runs.claim("chat", ...)`) must happen in the SAME synchronous
stretch as the `is_busy("chat")` pre-check, with zero `await` in between -
see backend/run_lifecycle.py's own docstring for why. This is a real
invariant, but it is NOT meaningfully provable with a dynamic race test:
asyncio coroutines only yield control at an `await` expression, so as long
as that stretch genuinely contains none, two concurrently-scheduled
`_dispatch()` calls cannot interleave there no matter how a test tries to
race them - there is no scheduler-level "unlucky timing" to trigger.
(Confirmed directly: temporarily inserting a real `await asyncio.sleep(0)`
between the two did NOT make the existing
test_rapid_fire_double_send_same_session_second_is_rejected fail, because
that test calls the two dispatches sequentially, each one's whole
synchronous prefix completing atomically before the next begins - it
already can't observe this cross-await interleaving hazard.) The only
regression that could ever violate this is a future EDIT adding an
`await` in that stretch - a static property, checked statically here,
the same AST-based-permanent-gate approach as tests/test_domain_purity.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENTS_PY = Path(__file__).resolve().parents[1] / "agents.py"


def _find_dispatch_method():
    tree = ast.parse(AGENTS_PY.read_text(encoding="utf-8"), filename=str(AGENTS_PY))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentDispatcher":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "_dispatch":
                    return item
    raise AssertionError("AgentDispatcher._dispatch not found - did it move or get renamed?")


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


def test_no_await_between_the_chat_busy_check_and_the_registry_claim():
    dispatch = _find_dispatch_method()
    body = dispatch.body

    busy_check_index = next(
        (i for i, stmt in enumerate(body) if isinstance(stmt, ast.If) and _statement_calls(stmt.test, "is_busy")),
        None,
    )
    claim_index = next((i for i, stmt in enumerate(body) if _statement_calls(stmt, "claim")), None)

    assert busy_check_index is not None, "no `if ...is_busy(...):` guard found in _dispatch - did the guard move?"
    assert claim_index is not None, "no `...claim(...)` call found in _dispatch - did the claim move?"
    assert busy_check_index < claim_index, "the busy check must precede the claim, not follow it"

    # Both boundary statements are included, not just what lies strictly
    # between them - an `await` written directly on the busy-check or the
    # claim statement itself (e.g. a future edit turning `claim(...)` into
    # `await claim(...)`) must be caught too. Safe to include the
    # busy-check's own `If`: it matches _awaits_reaching_claim's own
    # "if ...: ...; return" exemption for the same reason the
    # is_configured() guard below it does, so it is correctly skipped
    # rather than double-flagging that check's own early-return await.
    between = body[busy_check_index : claim_index + 1]
    offenders = [f"line {n.lineno}" for n in _awaits_reaching_claim(between)]
    assert not offenders, (
        "an `await` was found on a path between _dispatch's chat busy-check and its registry "
        f"claim (at {', '.join(offenders)}) - this reopens the exact concurrent-double-admit "
        "race RunRegistry.claim()'s synchronous-by-design contract exists to close. See this "
        "test module's own docstring, and backend/run_lifecycle.py's."
    )
