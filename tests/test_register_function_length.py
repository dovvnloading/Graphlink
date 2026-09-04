"""ADR-002 stage 2.7 exit gate: every `register*` function under backend/
stays at or under 300 lines.

Stage 2.6 set this cap (backend/canvas.py's own former ~1572-line
register_canvas, split into backend/api/intents_*.py) but its own exit
criterion was written over EVERY registration function while its scope
only ever named register_canvas - so register_settings (901 lines) and
register_chat_library (319 lines) both silently busted the cap for a full
stage before stage 2.7 found and split them. AST-based, same philosophy
as tests/test_node_state_migration.py: measuring the diff instead of
grepping catches this class of "the exit criterion was met for the one
named site, not for the property it actually describes" gap, and keeps it
caught - a future PR that grows any register* function past 300 lines
(a new feature area bolted onto an existing one, say) fails this test
immediately instead of silently re-accumulating the debt stage 2.6/2.7
just paid down.

Matches ANY function or async function whose name starts with "register",
at any nesting depth (ast.walk, not just module-level) - the same
matching a manual line-count audit would need to do to be trustworthy,
and the same shape the register_settings/register_chat_library splits
were actually measured against while doing this work.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIR = REPO_ROOT / "backend"
MAX_REGISTER_FUNCTION_LINES = 300


def _register_functions():
    for path in sorted(SCAN_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("register"):
                length = node.end_lineno - node.lineno + 1
                yield path, node, length


def test_no_register_function_exceeds_the_300_line_cap():
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name} is {length} lines "
        f"(cap: {MAX_REGISTER_FUNCTION_LINES})"
        for path, node, length in _register_functions()
        if length > MAX_REGISTER_FUNCTION_LINES
    ]
    assert not offenders, (
        "ADR-002 stage 2.6/2.7: a register* function under backend/ exceeds "
        f"the {MAX_REGISTER_FUNCTION_LINES}-line cap - split it the same way "
        "register_canvas/register_settings/register_chat_library were split:\n"
        + "\n".join(offenders)
    )


# Below this much headroom, report it. A cap that everything sits just under
# is a cap that will bind on whoever happens to touch one of those functions
# next, for a reason that has nothing to do with their change - the same
# erosion web_ui/scripts/check-bundle-size.mjs kept suffering, where a ratchet
# with 323 bytes left "passed" right up until an unrelated two-line bug fix
# tripped it. 10% of the cap (30 lines) is enough warning to split
# deliberately rather than under duress.
HEADROOM_WARN_LINES = MAX_REGISTER_FUNCTION_LINES // 10


def test_the_number_of_register_functions_near_the_cap_is_not_growing():
    """A cap everything sits just under will bind on whoever happens to touch
    one of those functions next, for a reason that has nothing to do with
    their change. That is the erosion web_ui/scripts/check-bundle-size.mjs
    kept suffering - a ratchet with 323 bytes left "passed" right up until an
    unrelated two-line bug fix tripped it.

    Four functions were within 30 lines of the cap when this was added (294,
    290, 285, 283), and splitting a 294-line registration function is real
    work that should be scheduled rather than forced. So this does not fail
    on those four - it fails on a FIFTH, which is the signal that the
    pressure is growing rather than being paid down.

    Lower the recorded count as they are split. Never raise it."""
    tight = sorted(
        (MAX_REGISTER_FUNCTION_LINES - length, f"{path.relative_to(REPO_ROOT).as_posix()}::{node.name}", length)
        for path, node, length in _register_functions()
        if MAX_REGISTER_FUNCTION_LINES - length < HEADROOM_WARN_LINES
    )
    assert len(tight) <= 4, (
        f"{len(tight)} register* functions are now within {HEADROOM_WARN_LINES} lines of the "
        f"{MAX_REGISTER_FUNCTION_LINES}-line cap, up from the 4 recorded here. Split one "
        "before adding another:\n    "
        + "\n    ".join(f"{length} lines ({headroom} left)  {name}" for headroom, name, length in tight)
    )


def test_at_least_one_register_function_is_found():
    # A collection bug (wrong glob, wrong name-matching predicate) would
    # make the test above vacuously pass with zero offenders - this
    # asserts the scan actually found the real, large population of
    # register* functions backend/ is known to have, not an empty list.
    count = sum(1 for _ in _register_functions())
    assert count >= 20, f"expected at least 20 register* functions under backend/, found {count}"
