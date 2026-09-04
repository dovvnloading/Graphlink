"""The posix-permissions CI job has to actually cover the marked tests.

That job (.github/workflows/ci.yml) runs `-m posix_permissions` against an
EXPLICIT list of four files rather than all of backend/tests, because pytest
imports every module it collects and roughly 18 tests elsewhere in that
directory genuinely assume Windows. Narrowing collection is what keeps the
job about file permissions instead of about porting the suite to Linux.

The cost of narrowing is drift: a tenth `@pytest.mark.posix_permissions`
test landing in a fifth file would be selected by nobody and run nowhere,
which is the exact condition this job was added to end - and it would look
green. So the two are tied together here.

Same posture as tests/test_doc_currency.py (assert the document still
describes the code) and test_undo_classification_gate.py (assert the
hand-authored table still matches the registered intents): the check is
cheap, and the failure it prevents is silent.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MARKER = "posix_permissions"


def _files_with_marked_tests() -> set[str]:
    """Every file under backend/tests holding a @pytest.mark.posix_permissions
    test, as a repo-relative posix path."""
    found: set[str] = set()
    for path in (REPO_ROOT / "backend" / "tests").rglob("test_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken test file fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                # @pytest.mark.posix_permissions - bare, never called
                if isinstance(decorator, ast.Attribute) and decorator.attr == MARKER:
                    found.add(path.relative_to(REPO_ROOT).as_posix())
    return found


def _files_the_job_runs() -> set[str]:
    """The backend/tests paths named in the posix-permissions job's own step."""
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("  posix-permissions:")
    return set(re.findall(r"(backend/tests/test_[a-z0-9_]+\.py)", text[start:]))


def test_the_job_runs_every_file_that_has_a_marked_test():
    marked = _files_with_marked_tests()
    listed = _files_the_job_runs()
    missing = sorted(marked - listed)
    assert not missing, (
        "these files hold @pytest.mark.posix_permissions tests the posix-permissions "
        f"CI job does not run: {missing}. Add them to that job's pytest invocation in "
        ".github/workflows/ci.yml, or the assertions run on no machine at all."
    )


def test_the_job_does_not_list_files_with_nothing_to_run():
    marked = _files_with_marked_tests()
    listed = _files_the_job_runs()
    stale = sorted(listed - marked)
    assert not stale, (
        "the posix-permissions CI job runs these files, but none of them holds a "
        f"@pytest.mark.posix_permissions test any more: {stale}. Drop them from the "
        "job rather than paying to collect them."
    )


def test_the_marker_is_actually_in_use():
    """Guards the guard: if the marker were renamed or dropped, both checks
    above would pass vacuously on two empty sets."""
    assert len(_files_with_marked_tests()) >= 4
    assert len(_files_the_job_runs()) >= 4
