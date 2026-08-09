"""ADR-010 close-out: the classify-or-fail gate.

The user's mandate for this stage was explicit: every action in the app
must be explicitly classified undoable-or-not, WITH A REASON, and that
classification must be enforced going forward - "no more wandering or
patchwork... all doors closed on this matter." tests/undo_classification.py
is the hand-authored decision; this file is what makes it load-bearing
rather than a document nobody re-reads.

Same AST-scan philosophy as test_register_function_length.py (measure the
real thing, don't grep and hope) and the same "guard the guard" posture as
queueableClassificationGuard.test.ts (assert the scan finds a sane
population before trusting its silence). Four checks, each catching a
different way the table and the code could drift apart:

  1. every bus.register_intent() the app really has is in the table -
     a new intent with no entry fails the build (must be classified, not
     silently defaulted to "not undoable" by omission).
  2. every table entry names a real, still-registered intent - a removed/
     renamed intent leaves a stale entry (fails the build).
  3. every "A" entry's handler actually calls record_command somewhere in
     its reachable body (direct, or via a same-file helper closure it
     calls) - a claimed-A intent whose wrap was reverted or never written
     fails the build.
  4. every "B" entry's handler does NOT reach a record_command call - a "B"
     intent that quietly grew a wrap should have been reclassified "A";
     this catches that drift too, not just the one direction.

KNOWN LIMITATION, stated rather than papered over (same posture as
wireTypeCastGuard.test.ts's and queueableClassificationGuard.test.ts's own
documented gaps): checks 3/4's reachability walk is a same-file, name-based
BFS (a handler's own body, plus any locally-defined function it calls by
name, transitively) - not a real cross-module call graph. Every handler in
this codebase calls record_command (if at all) within its own file, so this
holds today; a future handler that delegates to a helper in a DIFFERENT
module would be invisible to this walk and could misclassify silently. If
that pattern is ever introduced, extend _reaches_record_command rather than
assuming green means safe.

Adversarial-review fixes (same increment, not a follow-up): a first version
of this gate had four real gaps, each mechanically reproduced and then
closed here - (1) an unresolvable handler shape made the "B" check silently
PASS while the "A" check correctly failed on the identical input (asymmetric
- a claim this gate can't verify must fail on BOTH sides, not quietly count
as "not undoable"); (2) local_funcs was a flat, file-wide name map with no
class-body exclusion, so a same-named class METHOD elsewhere in the file
(confirmed live today: NotificationState.dismiss shadows the registered
`dismiss` closure in backend/notifications.py) could silently resolve to the
wrong node; (3) a register_intent call using a keyword argument or a
non-literal topic/intent (a hoisted constant, say) was silently skipped
entirely rather than flagged, defeating the whole population count; (4) a
genuine cross-file (topic, intent) collision silently let the second file
scanned win with no diagnostic of its own (production's EventBus.
register_intent asserts against this at session-bring-up time, but this
static gate had no independent signal).
"""

from __future__ import annotations

import ast
from pathlib import Path

from undo_classification import CLASSIFICATION

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIR = REPO_ROOT / "backend"

_UNRESOLVED = object()  # sentinel: distinct from "resolved to a lambda/def"


def _iter_python_files():
    for path in sorted(SCAN_DIR.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        yield path


def _string_constant(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_non_method_functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Every FunctionDef/AsyncFunctionDef reachable WITHOUT descending into a
    ClassDef body - deliberately excludes methods, which live in a different
    namespace and must never be able to shadow a same-named module-level
    handler or helper closure (see the module docstring's fix (2))."""
    found: dict[str, ast.AST] = {}

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[child.name] = child
            visit(child)

    visit(tree)
    return found


def _call_topic_intent_handler(node: ast.Call):
    """Extracts (topic_node, intent_node, handler_node) from a
    register_intent call, accepting BOTH positional and keyword-argument
    forms (register_intent's real signature has no positional-only marker -
    see backend/events.py's register_intent). Returns None only when the
    call genuinely omits one of the three (a TypeError at runtime already,
    not this gate's concern)."""
    positional = list(node.args)
    by_kw = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}

    def _nth_or_kw(index: int, name: str):
        if index < len(positional):
            return positional[index]
        return by_kw.get(name)

    topic_node = _nth_or_kw(0, "topic")
    intent_node = _nth_or_kw(1, "intent")
    handler_node = _nth_or_kw(2, "handler")
    if topic_node is None or intent_node is None or handler_node is None:
        return None
    return topic_node, intent_node, handler_node


class _FileIntents:
    """One file's register_intent() call sites plus its local functions,
    both needed to resolve a handler expression back to a walkable body."""

    def __init__(self, path: Path, tree: ast.Module):
        self.path = path
        self.local_funcs: dict[str, ast.AST] = _collect_non_method_functions(tree)
        self.registrations: list[tuple[str, str, ast.AST]] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "register_intent":
                continue
            extracted = _call_topic_intent_handler(node)
            if extracted is None:
                continue
            topic_node, intent_node, handler_node = extracted
            topic = _string_constant(topic_node)
            intent = _string_constant(intent_node)
            if topic is None or intent is None:
                # A non-literal topic/intent would be silently invisible to
                # every check below (never counted as "real", so it can
                # never be flagged as missing OR classified) - exactly the
                # "new intent added, gate says nothing" failure mode this
                # whole file exists to prevent. Fail loud instead of
                # continuing past it.
                raise AssertionError(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}: register_intent() called with a "
                    "non-literal topic and/or intent argument - tests/test_undo_classification_gate.py "
                    "can only classify string-literal (topic, intent) pairs. Use literal strings, or "
                    "extend _call_topic_intent_handler/_string_constant to resolve this shape."
                )
            self.registrations.append((topic, intent, handler_node))

    def resolve_handler_root(self, handler_expr: ast.AST):
        """The AST node whose subtree should be walked for record_command -
        the handler function itself, the real function inside a wrapper
        call like `_serialize_mutating_intent(load_chat)`, or the lambda
        node itself. Returns the _UNRESOLVED sentinel (never None - see the
        module docstring's fix (1)) when the shape isn't recognized, so
        BOTH the "A" and "B" checks below can treat "could not verify" as a
        failure on either side, not a silent pass on one of them."""
        if isinstance(handler_expr, ast.Name):
            return self.local_funcs.get(handler_expr.id, _UNRESOLVED)
        if isinstance(handler_expr, ast.Lambda):
            return handler_expr
        if isinstance(handler_expr, ast.Call) and handler_expr.args:
            inner = handler_expr.args[0]
            if isinstance(inner, ast.Name):
                return self.local_funcs.get(inner.id, _UNRESOLVED)
        return _UNRESOLVED


def _reaches_record_command(root: ast.AST, local_funcs: dict[str, ast.AST]) -> bool:
    """BFS over `root`'s own body plus any same-file function it calls by
    name, transitively - handles both a direct call and the shared-helper
    pattern (e.g. generateKeyTakeaway -> _generate_note_from_node ->
    record_command, where the two are SIBLING closures, not nested)."""
    seen: set[int] = set()
    queue = [root]
    while queue:
        node = queue.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "record_command"
            ):
                return True
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                callee = local_funcs.get(child.func.id)
                if callee is not None and id(callee) not in seen:
                    queue.append(callee)
    return False


def _collect_real_registrations() -> dict[tuple[str, str], _FileIntents]:
    """Maps every real (topic, intent) to the _FileIntents that registered
    it - a dict, not a set, specifically so checks 3/4 below can resolve
    each table entry's handler back to a walkable body without re-parsing."""
    by_key: dict[tuple[str, str], _FileIntents] = {}
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        file_intents = _FileIntents(path, tree)
        for topic, intent, _handler in file_intents.registrations:
            existing = by_key.get((topic, intent))
            if existing is not None and existing.path != path:
                # SessionBus.register_intent asserts against this too (see
                # backend/events.py), but only at actual session bring-up -
                # this static gate should say so on its own, immediately,
                # rather than silently letting the alphabetically-later file
                # win and validating checks 3/4 against the wrong handler.
                raise AssertionError(
                    f'intent "{topic}/{intent}" is registered in both '
                    f"{existing.path.relative_to(REPO_ROOT)} and {path.relative_to(REPO_ROOT)} - "
                    "SessionBus keys handlers by (topic, intent), so this is a real collision, "
                    "not just a classification-table ambiguity."
                )
            by_key[(topic, intent)] = file_intents
    return by_key


def test_the_scan_finds_the_real_population_of_registered_intents():
    # Guards the guard: a broken predicate here would make every check below
    # vacuously pass. 145 is the exact count locked by ADR-010's close-out
    # recon (scene=89, app-settings=30, app-composer=6, app-chat-library=5,
    # grid-control=4, notification=3, app-plugins=1, system=1, diagnostics=2)
    # - app-settings went 27 -> 28 when ADR-006 stage 6.5 added
    # setProviderMode, 28 -> 29 when ADR-016 stage 16.1 added setLogLevel,
    # 138 -> 140 when ADR-016 stage 16.4 added the diagnostics topic's two
    # intents (exportDiagnosticBundle, openLogFolder), 140 -> 142 when
    # ADR-018 stage 18.3 added scene's own setModelOverride/
    # clearModelOverride, 142 -> 143 when ADR-018 stage 18.4 added
    # app-settings' own setAutoModelPolicy, and 143 -> 145 when ADR-017
    # stage 17.5 added the new "knowledge" topic's own search intent plus
    # scene's own setChatIndexIntoKnowledge.
    real = _collect_real_registrations()
    assert len(real) == 145, (
        f"expected exactly 145 real registered intents, found {len(real)} - "
        "either the scan broke, or the app's registered-intent surface "
        "genuinely changed and tests/undo_classification.py's own count "
        "comment (and this assertion) need a deliberate update alongside it"
    )


def test_every_registered_intent_is_classified():
    real = set(_collect_real_registrations().keys())
    classified = {(c.topic, c.intent) for c in CLASSIFICATION}
    missing = sorted(real - classified)
    assert not missing, (
        "these intents are registered but not in tests/undo_classification.py - "
        "every mutating action must be explicitly classified A (undoable, wraps "
        "record_command) or B (not undoable, WITH A REASON):\n"
        + "\n".join(f"  {topic}/{intent}" for topic, intent in missing)
    )


def test_every_classification_entry_names_a_real_intent():
    real = set(_collect_real_registrations().keys())
    classified = {(c.topic, c.intent) for c in CLASSIFICATION}
    stale = sorted(classified - real)
    assert not stale, (
        "tests/undo_classification.py classifies these intents, but they are "
        "no longer registered anywhere under backend/ - remove the stale "
        "entry (the intent was renamed or deleted):\n"
        + "\n".join(f"  {topic}/{intent}" for topic, intent in stale)
    )


def _handler_expr_for(file_intents: _FileIntents, topic: str, intent: str) -> ast.AST:
    return next(
        handler
        for reg_topic, reg_intent, handler in file_intents.registrations
        if reg_topic == topic and reg_intent == intent
    )


def test_every_a_classified_intent_actually_records_a_command():
    real = _collect_real_registrations()
    offenders = []
    for entry in CLASSIFICATION:
        if entry.call != "A":
            continue
        file_intents = real.get((entry.topic, entry.intent))
        if file_intents is None:
            continue  # already reported by the stale-entry check above
        handler_expr = _handler_expr_for(file_intents, entry.topic, entry.intent)
        root = file_intents.resolve_handler_root(handler_expr)
        # "A" claims the handler reaches record_command - an unresolvable
        # handler shape means that claim can't be verified either, which is
        # itself a failure (see the module docstring's fix (1)).
        if root is _UNRESOLVED or not _reaches_record_command(root, file_intents.local_funcs):
            offenders.append(f"{entry.topic}/{entry.intent} ({file_intents.path.relative_to(REPO_ROOT)})")
    assert not offenders, (
        "these intents are classified \"A\" (undoable) in tests/undo_classification.py "
        "but their handler never reaches a record_command(...) call (or its handler shape "
        "could not be resolved at all) - either the wrap was reverted/never written (fix the "
        "handler), the handler needs a resolvable shape (a plain same-file function/lambda, "
        "not e.g. a bound method or an aliased variable), or this should be reclassified "
        "\"B\" with a real reason:\n" + "\n".join(f"  {o}" for o in offenders)
    )


def test_every_b_classified_intent_never_records_a_command():
    real = _collect_real_registrations()
    offenders = []
    for entry in CLASSIFICATION:
        if entry.call != "B":
            continue
        file_intents = real.get((entry.topic, entry.intent))
        if file_intents is None:
            continue  # already reported by the stale-entry check above
        handler_expr = _handler_expr_for(file_intents, entry.topic, entry.intent)
        root = file_intents.resolve_handler_root(handler_expr)
        # "B" claims the handler does NOT reach record_command - an
        # unresolvable handler shape means that claim can't be verified
        # either, so it fails here too, symmetric with the "A" check above
        # (this is the exact asymmetry the adversarial review's fix (1)
        # closed: an unresolvable shape used to silently PASS this check).
        if root is _UNRESOLVED or _reaches_record_command(root, file_intents.local_funcs):
            offenders.append(f"{entry.topic}/{entry.intent} ({file_intents.path.relative_to(REPO_ROOT)})")
    assert not offenders, (
        "these intents are classified \"B\" (not undoable) in tests/undo_classification.py "
        "but their handler now calls record_command(...), or its handler shape could not be "
        "resolved at all (an unverifiable \"B\" claim is treated as a failure, not a pass) - "
        "if it now calls record_command, flip the entry to \"A\" with a real reason; if it's "
        "just unresolvable, give it a resolvable shape:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
