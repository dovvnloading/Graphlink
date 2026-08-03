"""ADR-002 stage 2.5 (backend-only) migration gate. Grows one entry per
kind as each kind's fields move from SceneNode directly onto
backend/domain/node_states.py's per-kind state classes.

AST-based, same philosophy as tests/test_domain_purity.py: for every field
name a kind has already migrated, no code anywhere in backend/ or its own
tests may access that name as a BARE attribute (node.image_asset_id) -
every access must go through .state (node.state.image_asset_id, or an
arbitrarily-nested .state.image_asset_id, e.g.
document.nodes[id].state.image_asset_id). This is the direct proof that a
migration PR actually finished moving every call site, not just that the
domain model compiles. Checks both dot-attribute syntax (ast.Attribute)
and the string-literal equivalent (getattr(node, "image_asset_id", ...) /
setattr(node, "image_asset_id", ...)) - an adversarial-review finding on
this same gate's own PR: a walk over ast.Attribute alone can't see a field
name smuggled in as a getattr/setattr string argument.

_KNOWN_NON_NODE_FIELD_ACCESS_SHAPES carves out a small, explicit,
shape-based allowlist for the rare migrated field name (PR4's "code") that
collides with an unrelated attribute on a genuinely different type
elsewhere in the codebase - see its own comment for exactly which shapes
and why.

test_scene_node_core_field_count (asserting the final <=15-field core
exactly) is deferred to the stage's own exit PR, once every kind that has
kind-specific fields has migrated - see backend/domain/node_states.py's
own docstring for the full kind list and which 3 kinds need no state class
at all.

test_scene_payload_key_set_is_unchanged_by_the_migration is the wire-
compat tripwire this whole stage's backend-only constraint depends on:
scene_payload() is one flat dict literal (backend/domain/graph.py) that
emits the SAME 86 keys for every node regardless of kind - a golden,
hardcoded snapshot of that sorted key list, captured pre-migration. As
long as this test keeps passing, no migration PR has silently added,
renamed, or dropped a wire key while moving where a field lives in
memory.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.domain.graph import SceneDocument

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (REPO_ROOT / "backend", REPO_ROOT / "tests")

# Grows by one entry per migration PR. Field names here must NEVER appear
# as a bare `X.<field>` attribute access anywhere under SCAN_DIRS - only
# `X.state.<field>` (or a longer chain ending in `.state.<field>`).
MIGRATED_KIND_FIELDS = {
    "image": ["image_asset_id"],
    "html": ["html_splitter_state"],
    "artifact": ["artifact_content"],
    "code": ["code", "language"],
    "note": ["is_system_prompt", "is_summary_note", "is_branch_comparison"],
}


def _all_migrated_fields() -> set[str]:
    fields: set[str] = set()
    for names in MIGRATED_KIND_FIELDS.values():
        fields.update(names)
    return fields


def _source_files():
    for scan_dir in SCAN_DIRS:
        for path in sorted(scan_dir.rglob("*.py")):
            yield path


def _is_via_state(value_node: ast.expr) -> bool:
    """True if `value_node` is itself an attribute access ending in
    `.state` (arbitrarily deep, e.g. `document.nodes[id].state`) - the one
    legitimate shape for a migrated field access, whether reached via dot
    syntax or getattr/setattr's own first argument."""
    return isinstance(value_node, ast.Attribute) and value_node.attr == "state"


def _root_name(expr: ast.expr) -> str | None:
    """Unwraps arbitrarily-nested Subscript/Attribute layers down to the
    base Name, e.g. `failures[0]` -> "failures"."""
    while isinstance(expr, (ast.Subscript, ast.Attribute)):
        expr = expr.value
    return expr.id if isinstance(expr, ast.Name) else None


# PR4's "code" kind reuses two field names ("code", "language") common
# enough to collide with unrelated attributes on genuinely different
# types elsewhere in this codebase - unlike every other kind's
# distinctively-prefixed field names (gitlink_*, pycoder_*, etc.), which
# need no such allowance. Listed by exact SHAPE (and, where the shape
# alone isn't distinctive enough, the one file that shape's real usage is
# confined to), not by line number (which would rot on the next unrelated
# edit to either file) - each entry here must be hand-verified against its
# real type before being added; this is a liability, not a convenience,
# kept as small and as tightly scoped as the name collision forces it to
# be. A "file" of None applies repo-wide; a real filename restricts the
# exemption to that one file, so e.g. a FUTURE test elsewhere naming an
# unrelated list of real SceneNode/CodeState objects `failures` is still
# caught, not silently exempted just because the root name matches.
_KNOWN_NON_NODE_FIELD_ACCESS_SHAPES = {
    "code": (
        # pytest.raises(...) as exc_info: exc_info.value.code is
        # ExceptionInfo's own wrapped-exception .code (websocket close
        # codes in test_ws_origin.py/test_crash_recovery_notice.py),
        # never SceneNode's - the ".value" immediately inside the access
        # is the tell, regardless of what exc_info itself is named or
        # which file uses the idiom, so this one is repo-wide.
        {"root": "__attr_value__", "file": None},
        # on_failure=failures.append callbacks collect exception-like
        # ResearchFailure/etc objects (never SceneNode) into a list
        # literally named `failures` - but ONLY in
        # backend/tests/test_agents.py; failures[i].code there is that
        # object's own .code, e.g. "watchdog_timeout".
        {"root": "failures", "file": "test_agents.py"},
    ),
    "language": (),
}


def _is_known_non_node_field_access(attribute_node: ast.Attribute, path: Path) -> bool:
    for shape in _KNOWN_NON_NODE_FIELD_ACCESS_SHAPES.get(attribute_node.attr, ()):
        if shape["file"] is not None and path.name != shape["file"]:
            continue
        if shape["root"] == "__attr_value__":
            if isinstance(attribute_node.value, ast.Attribute) and attribute_node.value.attr == "value":
                return True
            continue
        if _root_name(attribute_node.value) == shape["root"]:
            return True
    return False


def _string_constant(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def test_migrated_kind_fields_accessed_only_via_state():
    migrated_fields = _all_migrated_fields()
    offenders = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr not in migrated_fields or _is_via_state(node.value):
                    continue
                if _is_known_non_node_field_access(node, path):
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} bare .{node.attr} access")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id not in ("getattr", "setattr") or len(node.args) < 2:
                    continue
                field_name = _string_constant(node.args[1])
                if field_name not in migrated_fields or _is_via_state(node.args[0]):
                    continue
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} bare "
                    f"{node.func.id}(..., {field_name!r}, ...) access"
                )
    assert not offenders, (
        "ADR-002 stage 2.5: migrated field accessed without going through "
        "node.state:\n" + "\n".join(offenders)
    )


# Captured from SceneDocument.scene_payload()'s real output pre-migration
# (a single chat node, ADR-002 stage 2.5 PR1/image baseline) - sorted, 86
# keys. scene_payload emits this SAME key set for every node regardless of
# kind (one flat dict literal, no per-kind branching), so one representative
# node is sufficient; the point is the KEY SET, not per-kind values.
_EXPECTED_SCENE_NODE_WIRE_KEYS = sorted([
    "artifactContent", "attachmentKind", "branchStatus", "byteSize",
    "chartAspectLocked", "chartAssetId", "chartAssetVersion", "chartData",
    "chartError", "chartHeight", "chartSourceNodeId", "chartType", "chartWidth",
    "chatScrollValue", "code", "codeSandboxAnalysis", "codeSandboxApprovalRequirements",
    "codeSandboxAwaitingApproval", "codeSandboxCode", "codeSandboxError",
    "codeSandboxOutput", "codeSandboxPrompt", "codeSandboxRequirements", "color",
    "content", "contentParts", "durationSeconds", "filePath", "gitlinkBranch",
    "gitlinkChangeFingerprint", "gitlinkChangeState", "gitlinkContextStats",
    "gitlinkContextSummary", "gitlinkContextVersion", "gitlinkError",
    "gitlinkLocalRoot", "gitlinkPendingChanges", "gitlinkPreviewText",
    "gitlinkProposalMarkdown", "gitlinkRepo", "gitlinkRepoFilePaths",
    "gitlinkScopeMode", "gitlinkSelectedPaths", "gitlinkTaskPrompt", "groupHeight",
    "groupWidth", "headerColor", "history", "htmlSplitterState", "id",
    "imageAssetId", "isBranchComparison", "isBranchSynthesis", "isCollapsed",
    "isDocked", "isFinalDeliverable", "isLocked", "isSummaryNote",
    "isSystemPrompt", "isUser", "itemIds", "kind", "language", "mimeType",
    "model", "pendingRequestId", "previewLabel", "provider", "pycoderAnalysis",
    "pycoderAwaitingApproval", "pycoderCode", "pycoderError",
    "pycoderLastRunFailed", "pycoderMode", "pycoderOutput", "pycoderPrompt",
    "researchActiveSourceId", "researchCompleted", "researchError",
    "researchResult", "researchStage", "researchTotal", "synthesisInstructions",
    "title", "x", "y",
])


def test_scene_payload_key_set_is_unchanged_by_the_migration():
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "hi", True)
    row = doc.scene_payload()["nodes"][0]
    assert sorted(row.keys()) == _EXPECTED_SCENE_NODE_WIRE_KEYS
