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


def test_at_least_one_register_function_is_found():
    # A collection bug (wrong glob, wrong name-matching predicate) would
    # make the test above vacuously pass with zero offenders - this
    # asserts the scan actually found the real, large population of
    # register* functions backend/ is known to have, not an empty list.
    count = sum(1 for _ in _register_functions())
    assert count >= 20, f"expected at least 20 register* functions under backend/, found {count}"
