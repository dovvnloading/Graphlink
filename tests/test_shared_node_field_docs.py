"""`content` and `history` are SHARED fields; their comments have to say so.

Both live on SceneNode rather than in a per-kind class in
backend/domain/node_states.py, and both were documented as belonging to one
kind - `content` to chat, `history` to conversation - with an explicit
"Unused for every other kind" line. Neither was true any more. A 2026-09-04
audit measured ten kinds writing `content` and seven writing `history`,
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
across the two modules that create nodes: backend/domain/graph.py (live
creation) and backend/session_load.py (restore from a saved chat).

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
NODE_CREATING_MODULES = (
    REPO_ROOT / "backend" / "domain" / "graph.py",
    REPO_ROOT / "backend" / "session_load.py",
)

# Hand-authored, and deliberately duplicated in backend/domain/model.py's own
# field comments. The gate exists to keep the two copies honest.
CONTENT_KINDS = {
    "artifact", "chat", "document", "harness", "html",
    "image", "note", "plan", "thinking", "web_research",
}
HISTORY_KINDS = {"artifact", "chat", "code_sandbox", "conversation", "gitlink", "html", "web_research"}


def _kinds_constructing_with(field_name: str) -> set[str]:
    """Kinds passed to a SceneNode(...) call that also passes `field_name`."""
    kinds: set[str] = set()
    for path in NODE_CREATING_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "SceneNode":
                continue
            passed = {kw.arg for kw in node.keywords if kw.arg}
            if field_name not in passed:
                continue
            for kw in node.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    kinds.add(str(kw.value.value))
    return kinds


def test_content_is_written_by_the_kinds_its_comment_names():
    assert _kinds_constructing_with("content") == CONTENT_KINDS


def test_history_is_written_by_the_kinds_its_comment_names():
    assert _kinds_constructing_with("history") == HISTORY_KINDS


def test_the_scan_finds_a_sane_population():
    """Guards the guard: a renamed class or a changed construction style would
    make both checks above compare two empty sets and pass."""
    assert len(_kinds_constructing_with("content")) >= 5
    assert len(_kinds_constructing_with("history")) >= 5
