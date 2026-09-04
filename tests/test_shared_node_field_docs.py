"""`content` and `history` are SHARED fields; their comments have to say so.

Both live on SceneNode rather than in a per-kind class in
backend/domain/node_states.py, and both were documented as belonging to one
kind - `content` to chat, `history` to conversation - with an explicit
"Unused for every other kind" line. Neither was true any more. A 2026-09-04
audit measured twelve kinds writing `content` and seven writing `history`,
and in `content`'s case a later line in the SAME comment block already
contradicted the earlier one.

That is a recurring shape in this codebase rather than a one-off: a comment
asserts a closed set, the set grows, and nothing fails. The same audit found
backend/api/_shared.py naming its two callers by hand and being wrong within
one commit of being written. This gate is the cheap answer for the two cases
where the claim is load-bearing - it is the reason these fields were left on
SceneNode, so a reader deciding whether to "finish the migration" needs the
list to be right.

Derived from the kinds that actually construct a SceneNode with each field,
across every module under backend/ that does so - DISCOVERED, not listed.
Hard-coding that list is how the first version of this gate shipped with the
wrong answer.

Same posture as tests/undo_classification.py: a hand-authored expectation
plus a gate that fails when the code and the expectation diverge. If this
fails because a new kind legitimately started using the field, update BOTH
the set below and the field's comment in backend/domain/model.py - that
pairing is the whole point.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

# Hand-authored, and deliberately duplicated in backend/domain/model.py's own
# field comments. The gate exists to keep the two copies honest.
CONTENT_KINDS = {
    "artifact", "chat", "container", "document", "frame", "harness",
    "html", "image", "note", "plan", "thinking", "web_research",
}
HISTORY_KINDS = {"artifact", "chat", "code_sandbox", "conversation", "gitlink", "html", "web_research"}


def _node_creating_modules() -> list[Path]:
    """Every module under backend/ that constructs a SceneNode, DISCOVERED.

    The first version of this gate hard-coded two paths - graph.py and
    session_load.py - and its own docstring called them "the two modules that
    create nodes". They were not. groups.py builds `kind="frame"` and
    `kind="container"` nodes with `content=`, so the list this gate was
    written to pin was itself wrong by two kinds, and injecting a thirteenth
    into groups.py would have left the gate green.

    That is precisely the failure this file exists to prevent - a closed set
    asserted by hand, growing, with nothing failing - reproduced one level up
    in the guard itself. Discovering the modules removes the hand-authored
    half of the problem; the kind sets below stay hand-authored on purpose,
    because a human deciding "yes, a thirteenth kind should write content" is
    the checkpoint.
    """
    modules: list[Path] = []
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken module fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee_name(node) == "SceneNode":
                modules.append(path)
                break
    return modules


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _kinds_constructing_with(field_name: str) -> set[str]:
    """Kinds passed to a SceneNode(...) call that also passes `field_name`."""
    kinds: set[str] = set()
    for path in _node_creating_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _callee_name(node) != "SceneNode":
                continue
            passed = {kw.arg for kw in node.keywords if kw.arg}
            if field_name not in passed:
                continue
            for kw in node.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    kinds.add(str(kw.value.value))
    return kinds


def test_the_module_discovery_finds_more_than_the_two_originally_hard_coded():
    """The specific miss. If discovery ever narrows back to graph.py and
    session_load.py, the kind sets above stop being trustworthy."""
    found = {p.name for p in _node_creating_modules()}
    assert {"graph.py", "session_load.py", "groups.py"} <= found, found


def test_content_is_written_by_the_kinds_its_comment_names():
    assert _kinds_constructing_with("content") == CONTENT_KINDS


def test_history_is_written_by_the_kinds_its_comment_names():
    assert _kinds_constructing_with("history") == HISTORY_KINDS


def test_the_scan_finds_a_sane_population():
    """Guards the guard: a renamed class or a changed construction style would
    make both checks above compare two empty sets and pass."""
    assert len(_kinds_constructing_with("content")) >= 5
    assert len(_kinds_constructing_with("history")) >= 5
