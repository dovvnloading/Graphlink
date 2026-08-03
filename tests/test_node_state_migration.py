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
