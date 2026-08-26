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
shape-based allowlist for the rare migrated field names (PR4's "code",
PR6's "byte_size"/"mime_type"/"duration_seconds", PR7's "provider") that
collide with an unrelated attribute on a genuinely different type
elsewhere in the codebase - see its own comment for exactly which shapes
and why.

test_scene_node_core_field_count is this stage's exit gate: it locks
SceneNode's dataclass field count at exactly 14 (down from the original
~95), the value PR10b (code_sandbox shim removal, the last kind with
per-kind fields) actually reached - see backend/domain/node_states.py's
own docstring for the full kind list and which 3 kinds need no state class
at all.

test_scene_payload_key_set_is_unchanged_by_the_migration is the wire-
compat tripwire this whole stage's backend-only constraint depends on:
scene_payload() is one flat dict literal (backend/domain/graph.py) that
emits the SAME set of keys for every node regardless of kind - a golden,
hardcoded snapshot of that sorted key list (121 as of PLAN-2026-08-24
H5's Py-Coder retirement), updated deliberately whenever a kind's own
fields are added, renamed, or removed. As long as this test keeps
passing between such updates, no migration PR has silently added,
renamed, or dropped a wire key while moving where a field lives in
memory.
"""

from __future__ import annotations

import ast
from dataclasses import fields as dataclass_fields
from pathlib import Path

from backend.domain.graph import SceneDocument
from backend.domain.model import SceneNode

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
    "document": [
        "attachment_kind", "file_path", "mime_type", "duration_seconds", "byte_size", "preview_label",
    ],
    "web_research": [
        "research_stage", "research_completed", "research_total",
        "research_active_source_id", "research_error", "research_result",
    ],
    "chart": [
        "chart_type", "chart_data", "chart_error",
        "chart_width", "chart_height", "chart_aspect_locked", "chart_source_node_id",
    ],
    # is_locked/group_manual_* are frame-only; group_width/group_height are
    # shared with "container" below (both entries list them - harmless
    # duplication, _all_migrated_fields() unions into one set either way).
    "frame": [
        "is_locked", "group_manual_width", "group_manual_height",
        "group_manual_x", "group_manual_y", "group_width", "group_height",
    ],
    "container": ["group_width", "group_height"],
    "chat": [
        "is_user", "chat_scroll_value", "content_parts", "provider", "model",
        "is_branch_synthesis", "synthesis_instructions", "branch_status",
    ],
    "gitlink": [
        "gitlink_repo", "gitlink_branch", "gitlink_scope_mode", "gitlink_local_root",
        "gitlink_imported_root", "gitlink_repo_file_paths", "gitlink_selected_paths",
        "gitlink_task_prompt", "gitlink_context_xml", "gitlink_context_stats",
        "gitlink_context_summary", "gitlink_context_version", "gitlink_proposal_markdown",
        "gitlink_pending_changes", "gitlink_preview_text", "gitlink_change_fingerprint",
        "gitlink_change_local_root", "gitlink_change_state", "gitlink_error",
    ],
    "code_sandbox": [
        "code_sandbox_sandbox_id", "code_sandbox_requirements", "code_sandbox_prompt",
        "code_sandbox_code", "code_sandbox_output", "code_sandbox_analysis",
        "code_sandbox_awaiting_approval", "code_sandbox_approval_requirements",
        "code_sandbox_approved_fingerprint", "code_sandbox_error",
    ],
    # ADR-008 stage 8.3: the Builder plan node - born state-typed (never a
    # bare-SceneNode field era to migrate FROM), listed here so the bare-
    # attribute ban covers it from day one. Every name carries a plan_/
    # builder_ prefix specifically so this table needs no
    # _KNOWN_NON_NODE_FIELD_ACCESS_SHAPES exemptions (the generic
    # candidates - goal/steps/mode/run_id - collide with Command.run_id
    # and friends all over the command layer).
    "plan": [
        "plan_goal", "plan_steps", "builder_activity", "builder_status", "builder_mode",
        "builder_run_id", "builder_max_steps", "builder_max_tokens",
        "builder_max_wall_seconds", "builder_spent_steps",
        "builder_spent_tokens", "builder_spent_wall_seconds",
        "builder_awaiting_tool_approval", "builder_approval_tool_name",
        "builder_approval_summary", "builder_status_detail",
    ],
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
# distinctively-prefixed field names (gitlink_*, code_sandbox_*, etc.), which
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
        # ADR-014 stage 14.3's H3 test-coverage work: RequestCancelled/
        # ResearchFailure (graphlink_plugins/web_research/domain.py) are
        # exception classes with their OWN .code error-code string
        # (e.g. "cancelled", "research_failed") - never a SceneNode -
        # confined to backend/tests/test_web_research_domain.py, which
        # exercises exactly these two classes directly.
        {"root": "RequestCancelled", "file": "test_web_research_domain.py"},
        {"root": "exc", "file": "test_web_research_domain.py"},
    ),
    "language": (),
    # PR6's "document" kind reuses byte_size/mime_type/duration_seconds,
    # names that collide with backend/attachments.py's own, wholly
    # unrelated StagedAttachment dataclass (a composer-staging concept,
    # never a SceneNode) - confirmed via grep that every "staged = ..."
    # binding repo-wide holds a StagedAttachment, never a SceneNode.
    #
    # ADR-021 stage 21.5 adds one more shape of the SAME collision: the
    # attachment-promotion helper iterates the staged list into a local
    # named `attachment`, which is a StagedAttachment for exactly the same
    # reason `staged` itself is. File-scoped to intents_chat.py so the
    # generic name cannot quietly excuse a real SceneNode access elsewhere.
    "byte_size": (
        {"root": "staged", "file": None},
        {"root": "self", "file": "attachments.py"},
        {"root": "attachment", "file": "intents_chat.py"},
    ),
    "mime_type": (
        {"root": "staged", "file": None},
        {"root": "attachment", "file": "intents_chat.py"},
    ),
    "duration_seconds": ({"root": "staged", "file": None},),
    # PR7's "chat" kind reuses "provider", which collides with two wholly
    # unrelated types: a ResearchSource's own
    # .provider (backend/canvas.py's _research_result_wire, iterating
    # result.sources) and a ModelDescriptor's own .provider (ADR-002 stage
    # 2.7 relocated this from backend/settings.py to
    # backend/api/intents_settings_api_provider.py's load_api_models,
    # iterating a get_available_model_descriptors() list) - neither is
    # ever a SceneNode.
    #
    # ADR-018 stage 18.1: graphlink_model_catalog.ModelDescriptor is now a
    # standing type (not just an inline iteration variable), so a THIRD
    # shape joins these two - backend/tests/test_model_routing.py's own
    # `descriptor` loop variable over a `unified_catalog()` result, same
    # non-SceneNode type as the intents_settings_api_provider.py entry
    # above, just a different file/root pair.
    "provider": (
        {"root": "s", "file": "canvas.py"},
        {"root": "descriptor", "file": "intents_settings_api_provider.py"},
        {"root": "descriptor", "file": "test_model_routing.py"},
        # ADR-018 stage 18.5: _thread_on_fallback's own `fallback_ref` param
        # (backend/agent_dispatch/core.py, inside AgentDispatcher._dispatch -
        # relocated there from backend/agents.py by the god-file
        # decomposition) is a graphlink_model_catalog.ModelRef -
        # api_provider's fallback-chain wrapper hands it the model it just
        # substituted in, never a SceneNode.
        {"root": "fallback_ref", "file": "core.py"},
        # ADR-018 stage 18.5 review-fix regression test: `catalog[0].provider`
        # is a unified_catalog() result (list[ModelDescriptor]), never a
        # SceneNode collection - test_model_routing.py is entirely about
        # model catalogs, so "catalog" never means anything else there.
        {"root": "catalog", "file": "test_model_routing.py"},
    ),
    # ADR-014 stage 14.3's H3 test-coverage work: WebResearchService's own
    # .model (its ApiResearchModel/FakeModel dependency, e.g.
    # `service.model`) is never a SceneNode - confined to
    # backend/tests/test_web_research_domain.py, which constructs and
    # asserts against WebResearchService directly.
    "model": ({"root": "service", "file": "test_web_research_domain.py"},),
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


# The stage's exit shape: id/x/y/title/kind (identity+position+display),
# content/is_collapsed/is_docked/history (the few fields still genuinely
# shared across several kinds, not worth their own 1-field state classes),
# pending_request_id (generic in-flight marker), color/header_color/
# item_ids (note/frame/container's shared group fields), and state itself
# (the per-kind payload). Order matches declaration order in
# backend/domain/model.py.
_EXPECTED_SCENE_NODE_CORE_FIELDS = [
    "id", "x", "y", "title", "kind", "content", "is_collapsed", "is_docked",
    "history", "pending_request_id", "color", "header_color", "item_ids", "state",
]


def test_scene_node_core_field_count():
    """ADR-002 stage 2.5 exit gate (backend-only half - see the ADR's own
    status table for why the wire-payload shrink is a separate, deferred
    stage): SceneNode's dataclass field count is locked at exactly 14.
    Asserts the exact field NAME set in order, not just len(...) == 14, so
    a future PR that swaps one field for a different unrelated one (same
    count, silently different shape) still fails this gate instead of
    passing it by coincidence."""
    actual = [f.name for f in dataclass_fields(SceneNode)]
    assert actual == _EXPECTED_SCENE_NODE_CORE_FIELDS


# Captured from SceneDocument.scene_payload()'s real output pre-migration
# (a single chat node, ADR-002 stage 2.5 PR1/image baseline) - sorted, 91
# keys (87 pre-6.8 + promptTokens/completionTokens, ADR-006 stage 6.8 +
# toolCalls, ADR-007 stage 7.4 + estimatedCostUsd, ADR-016 stage 16.2).
# scene_payload emits this SAME key set for every node regardless of kind
# (one flat dict literal, no per-kind branching), so one representative node
# is sufficient; the point is the KEY SET, not per-kind values.
_EXPECTED_SCENE_NODE_WIRE_KEYS = sorted([
    "artifactContent", "attachmentKind", "branchStatus", "byteSize",
    "chartAspectLocked", "chartData",
    "chartError", "chartHeight", "chartSourceNodeId", "chartType", "chartWidth",
    "chatScrollValue", "code", "codeSandboxAnalysis",
    "codeSandboxApprovalAllowSourceBuilds", "codeSandboxApprovalIsRepair",
    "codeSandboxApprovalRequirements",
    "codeSandboxAwaitingApproval", "codeSandboxCode", "codeSandboxError",
    "codeSandboxOutput", "codeSandboxPrompt", "codeSandboxRequirements", "color",
    "content", "contentParts", "durationSeconds", "estimatedCostUsd", "filePath",
    "gitlinkBranch",
    "gitlinkChangeFingerprint", "gitlinkChangeState", "gitlinkContextStats",
    "gitlinkContextSummary", "gitlinkContextVersion", "gitlinkError",
    "gitlinkLocalRoot", "gitlinkPendingChanges", "gitlinkPreviewText",
    "gitlinkProposalMarkdown", "gitlinkRepo", "gitlinkRepoFilePaths",
    "gitlinkScopeMode", "gitlinkSelectedPaths", "gitlinkTaskPrompt", "groupHeight",
    "groupWidth", "headerColor", "history", "htmlSplitterState", "id",
    "imageAssetId", "indexIntoKnowledge", "isBranchComparison", "isBranchSynthesis", "isCollapsed",
    "isDocked", "isFinalDeliverable", "isLocked", "isSummaryNote",
    "isSystemPrompt", "isUser", "itemIds", "kind", "language", "mimeType",
    "model", "overrideModelId", "overrideProvider",
    "pendingRequestId", "pluginState", "previewLabel", "provider",
    "researchActiveSourceId", "researchCompleted", "researchError",
    "completionTokens", "promptTokens",
    "researchResult", "researchRetainToKnowledge", "researchStage", "researchTotal",
    "responseIncomplete",
    "synthesisInstructions", "title", "toolCalls", "x", "y",
    # ADR-008 stage 8.3: the Builder plan node's 15 fields, +1 (builderActivity)
    # when stage 8.7 added the run's own activity log.
    "planGoal", "planSteps", "builderActivity", "builderStatus", "builderMode",
    "builderRunId", "builderMaxSteps", "builderMaxTokens", "builderMaxWallSeconds",
    "builderSpentSteps", "builderSpentTokens", "builderSpentWallSeconds",
    "builderAwaitingToolApproval", "builderApprovalToolName",
    "builderApprovalSummary", "builderStatusDetail",
    # PLAN-2026-08-24 H1: the harness node's render surface (history stays
    # in the workspace transcript, never on the wire).
    "harnessGoal", "harnessReply", "harnessStatus", "harnessStatusDetail",
    "harnessRunId", "harnessActivity", "harnessMaxTurns", "harnessSpentTurns",
    "harnessSpentTokens", "harnessAwaitingApproval", "harnessApprovalToolName",
    "harnessApprovalSummary", "harnessContextTokens", "harnessMaxContextTokens",
    "harnessCompactions", "harnessWorkspacePath", "harnessWorkspaceActive",
    # PLAN-2026-08-24 H6: graded consent (§2.4) plus the plan.update /
    # user.ask interaction surfaces (§2.3).
    "harnessApprovalSessionOffered", "harnessPlan",
    "harnessAwaitingQuestion", "harnessQuestion",
])


def _non_owning_kind_node(doc):
    """A node whose kind owns NONE of the currently-migrated fields, so
    every scene_payload() isinstance guard hits its else-branch - the
    right representative for both tests below. "thinking" is used
    deliberately, not "chat": PR7 migrated chat's own 8 fields, so a chat
    node can no longer serve this role - it would always hit the
    isinstance-true branch for its OWN fields, silently skipping exactly
    the class of bug these two tests exist to catch. "thinking" has zero
    kind-specific fields (node.state stays None for it permanently, per
    node_states.py's own docstring) and always will, since there is
    nothing left to migrate off it - a stable choice regardless of which
    kind migrates next."""
    parent = doc.add_node(0, 0)
    return doc.add_thinking_node(0, 0, "reasoning", parent_id=parent.id)


def test_scene_payload_key_set_is_unchanged_by_the_migration():
    doc = SceneDocument()
    _non_owning_kind_node(doc)
    row = doc.scene_payload()["nodes"][-1]
    assert sorted(row.keys()) == _EXPECTED_SCENE_NODE_WIRE_KEYS


# Every migrated field's wire VALUE for a node of a kind that does NOT own
# it - captured from each field's own former bare-SceneNode-field default,
# verified against git history at that field's own pre-migration commit,
# NOT from the per-kind state class's own "zero value" default (those
# usually, but do not always, coincide). This is the direct regression net
# for a real bug an adversarial review caught on this exact migration
# (ADR-002 stage 2.5 PR6): scene_payload()'s isinstance-guarded fallback
# for isLocked/chartWidth/chartHeight/chartAspectLocked was written as
# False/0.0/0.0/False (the type's zero value) instead of the field's real
# original default True/680.0/500.0/True - a wire-compat regression the
# key-set-only test above cannot see, since the key was always present,
# only its fallback VALUE for a non-owning node's row was wrong. Grows one
# entry per migrated field, alongside MIGRATED_KIND_FIELDS above.
_EXPECTED_NON_OWNING_KIND_WIRE_DEFAULTS = {
    "imageAssetId": "",
    "htmlSplitterState": None,
    "artifactContent": "",
    "code": "",
    "language": "",
    "isSystemPrompt": False,
    "isSummaryNote": False,
    "isBranchComparison": False,
    "attachmentKind": "",
    "filePath": "",
    "mimeType": "",
    "durationSeconds": None,
    "byteSize": None,
    "previewLabel": "",
    "researchStage": "",
    "researchCompleted": 0,
    "researchTotal": 0,
    "researchActiveSourceId": None,
    "researchError": "",
    "researchResult": None,
    "chartType": "",
    "chartData": {},
    "chartError": "",
    "chartWidth": 680.0,
    "chartHeight": 500.0,
    "chartAspectLocked": True,
    "chartSourceNodeId": "",
    "isLocked": True,
    "groupWidth": None,
    "groupHeight": None,
    "isUser": False,
    "chatScrollValue": 0.0,
    "contentParts": None,
    "provider": None,
    "model": None,
    "isBranchSynthesis": False,
    "synthesisInstructions": "",
    "branchStatus": "active",
    # ADR-002 stage 2.5 PR8a: gitlink's 16 wire keys, moved onto GitlinkState
    # behind a transitional property shim (see SceneNode's own comment,
    # backend/domain/model.py) - "gitlink" is deliberately NOT yet added to
    # MIGRATED_KIND_FIELDS above (that AST gate can't tell shimmed property
    # access from bare field access, and would false-positive on every
    # still-unconverted external call site the shim exists to leave alone
    # until the shim-removal PR). This dict is independent of that gate -
    # it checks scene_payload()'s actual runtime VALUES, which are already
    # fully correct in this PR, so the values are captured here now rather
    # than deferred alongside MIGRATED_KIND_FIELDS.
    "gitlinkRepo": "",
    "gitlinkBranch": "",
    "gitlinkScopeMode": "selected",
    "gitlinkLocalRoot": "",
    "gitlinkRepoFilePaths": [],
    "gitlinkSelectedPaths": [],
    "gitlinkTaskPrompt": "",
    "gitlinkContextStats": {},
    "gitlinkContextSummary": "",
    "gitlinkContextVersion": 0,
    "gitlinkProposalMarkdown": "",
    "gitlinkPendingChanges": [],
    "gitlinkPreviewText": "",
    "gitlinkChangeFingerprint": None,
    "gitlinkChangeState": "draft",
    "gitlinkError": "",
    # ADR-002 stage 2.5 PR10a: code_sandbox's 8 wire keys (code_sandbox_
    # sandbox_id/code_sandbox_approved_fingerprint are excluded from
    # scene_payload(), same posture as gitlink_context_xml), moved onto
    # CodeSandboxState behind the same transitional property shim as
    # gitlink - "code_sandbox" is likewise deliberately NOT yet
    # added to MIGRATED_KIND_FIELDS above, for the identical AST-gate
    # reason.
    "codeSandboxRequirements": "",
    "codeSandboxPrompt": "",
    "codeSandboxCode": "",
    "codeSandboxOutput": "",
    "codeSandboxAnalysis": "",
    "codeSandboxAwaitingApproval": False,
    "codeSandboxApprovalRequirements": "",
    "codeSandboxApprovalAllowSourceBuilds": False,
    "codeSandboxApprovalIsRepair": False,
    "codeSandboxError": "",
    # ADR-006 stage 6.4: interrupted-reply marker; non-chat kinds fall back
    # to False, same as the ChatState dataclass default.
    "responseIncomplete": False,
    # ADR-006 stage 6.8: provider-reported usage; non-chat kinds fall back
    # to None, same as the ChatState dataclass defaults.
    "promptTokens": None,
    "completionTokens": None,
}


def test_migrated_field_wire_fallbacks_match_pre_migration_defaults():
    doc = SceneDocument()
    _non_owning_kind_node(doc)
    row = doc.scene_payload()["nodes"][-1]
    mismatches = [
        f"{key}: got {row[key]!r}, expected {expected!r}"
        for key, expected in _EXPECTED_NON_OWNING_KIND_WIRE_DEFAULTS.items()
        if row.get(key) != expected
    ]
    assert not mismatches, (
        "ADR-002 stage 2.5: migrated field's non-owning-kind wire fallback "
        "does not match its pre-migration SceneNode default:\n" + "\n".join(mismatches)
    )
