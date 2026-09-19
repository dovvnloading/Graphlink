"""The per-node wire row - sent sparse.

Every node used to cross the wire as the FULL flat superset of every node
kind's fields: 157 keys, of which a chat node carries 13. Each new node kind
widened every node on the canvas, whatever its kind - measured against the
2026-08-11 perf baseline, the per-node payload had grown 32-47% and the
nightly perf check had failed every night since 2026-08-23.

A node now sends its five identity keys (id/x/y/title/kind) plus only the
fields whose value differs from the wire default. The default for every key
is the one contracts/graphlink_scene_payload.py's SceneNodeRow declares, and
the client restores every omitted key from that same declaration: codegen
emits it as hydrateSceneNodeRow() (web_ui/src/lib/bridge-core/generated/
scene-state.ts), which validateSceneState and the scene store's patch path
apply before anything else reads a row. The row the rest of the frontend sees
is therefore exactly the full row it always saw; only the bytes changed.

WIRE_DEFAULTS below restates those contract defaults because contracts/ is
deliberately not shipped in the wheel, so the running backend cannot import
it. backend/tests/test_node_wire.py pins the two tables equal - a default
changed on one side only fails that test, never silently diverges the wire.

"Differs from the default" is strict: same class AND equal. `0` and `False`
compare equal in Python but serialize differently, so a value of the wrong
type is always sent rather than folded into a default of another type.

Only the fields a node's own state class owns are even computed: the
per-state tables below replace 150 isinstance checks per node, per publish,
with one type lookup - the build cost the perf baseline tracks as
payload_build_ms, paid on every publish because take_dirty_patch_ops diffs
every node's row.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from backend.domain.content_codec import _content_codec
from backend.domain.model import SceneNode
from backend.domain.node_states import (
    ArtifactState,
    ChartState,
    ChatState,
    CodeReviewState,
    CodeSandboxState,
    CodeState,
    ContainerState,
    DocumentState,
    FrameState,
    GitlinkState,
    HarnessState,
    HtmlState,
    ImageState,
    NoteState,
    PlanState,
    WebResearchState,
)

# The wire value of every non-identity key when a node does not set it -
# SceneNodeRow's own defaults (see the module docstring), plus `contentParts`,
# a real wire field the contract deliberately does not declare (no frontend
# code reads it; see contracts/graphlink_scene_payload.py's docstring).
# Containers here are never sent or mutated - they are comparison targets only.
WIRE_DEFAULTS: dict[str, Any] = {
    "content": "",
    "isUser": False,
    "isCollapsed": False,
    "code": "",
    "language": "",
    "attachmentKind": "",
    "filePath": "",
    "mimeType": "",
    "durationSeconds": None,
    "byteSize": None,
    "previewLabel": "",
    "isDocked": False,
    "imageAssetId": "",
    "history": [],
    "pendingRequestId": None,
    "provider": None,
    "model": None,
    "overrideProvider": "",
    "overrideModelId": "",
    "indexIntoKnowledge": False,
    "isBranchSynthesis": False,
    "synthesisInstructions": "",
    "branchStatus": "active",
    "responseIncomplete": False,
    "promptTokens": None,
    "completionTokens": None,
    "estimatedCostUsd": None,
    "toolCalls": [],
    "isFinalDeliverable": False,
    "researchStage": "",
    "researchCompleted": 0,
    "researchTotal": 0,
    "researchActiveSourceId": None,
    "researchError": "",
    "researchResult": None,
    "researchRetainToKnowledge": False,
    "artifactContent": "",
    "artifactError": "",
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
    "codeReviewPrUrl": "",
    "codeReviewRepo": "",
    "codeReviewPrNumber": 0,
    "codeReviewPrTitle": "",
    "codeReviewPrState": "",
    "codeReviewPrHtmlUrl": "",
    "codeReviewBaseRef": "",
    "codeReviewHeadRef": "",
    "codeReviewAdditions": 0,
    "codeReviewDeletions": 0,
    "codeReviewChangedFiles": 0,
    "codeReviewFiles": [],
    "codeReviewFilesTruncated": False,
    "codeReviewDiffTruncated": False,
    "codeReviewDiffChars": 0,
    "codeReviewDiffVersion": 0,
    "codeReviewWalkthrough": [],
    "codeReviewFindings": [],
    "codeReviewErrors": [],
    "codeReviewDismissedIds": [],
    "codeReviewTitle": "",
    "codeReviewOverview": "",
    "codeReviewConfidence": "",
    "codeReviewScores": {},
    "codeReviewQualityScore": 0,
    "codeReviewVerdict": "none",
    "codeReviewRisk": "",
    "codeReviewQualitySummary": "",
    "codeReviewQa": [],
    "codeReviewState": "draft",
    "codeReviewError": "",
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
    "color": None,
    "headerColor": None,
    "isSystemPrompt": False,
    "isSummaryNote": False,
    "isBranchComparison": False,
    "itemIds": [],
    "isLocked": True,
    "groupWidth": None,
    "groupHeight": None,
    "chartType": "",
    "chartData": {},
    "chartError": "",
    "chartWidth": 480.0,
    "chartHeight": 340.0,
    "chartAspectLocked": True,
    "chartSourceNodeId": "",
    "htmlSplitterState": None,
    "chatScrollValue": 0.0,
    "contentParts": None,
    "planGoal": "",
    "planSteps": [],
    "builderActivity": [],
    "builderStatus": "",
    "builderMode": "",
    "builderRunId": "",
    "builderMaxSteps": 0,
    "builderMaxTokens": 0,
    "builderMaxWallSeconds": 0,
    "builderSpentSteps": 0,
    "builderSpentTokens": 0,
    "builderSpentWallSeconds": 0,
    "builderAwaitingToolApproval": False,
    "builderApprovalToolName": "",
    "builderApprovalSummary": "",
    "builderStatusDetail": "",
    "harnessGoal": "",
    "harnessReply": "",
    "harnessStatus": "",
    "harnessStatusDetail": "",
    "harnessRunId": "",
    "harnessActivity": [],
    "harnessAwaitingApproval": False,
    "harnessApprovalToolName": "",
    "harnessApprovalSummary": "",
    "harnessApprovalSessionOffered": False,
    "harnessPlan": [],
    "harnessAwaitingQuestion": False,
    "harnessQuestion": "",
    "harnessContextTokens": 0,
    "harnessMaxContextTokens": 0,
    "harnessCompactions": 0,
    "harnessWorkspacePath": "",
    "harnessWorkspaceActive": "",
    "harnessMaxTurns": 0,
    "harnessSpentTurns": 0,
    "harnessSpentTokens": 0,
    "pluginState": {},
}

# Always sent, never defaulted: SceneNodeRow's five fields with no default.
IDENTITY_KEYS = ("id", "x", "y", "title", "kind")


# -- Review Lens nested wire rows ------------------------------------------
#
# These five builders exist because the Review Lens node stores its nested
# rows as the review engine's own snake_case dicts (graphlink_plugins/
# review_lens/review_engine.py) while the wire contract - and the generated
# client validator built from it - is camelCase, like every other nested
# row on SceneNodeRow. scene_payload used to forward them with a bare
# `dict(row)`, which shipped `patch_truncated`/`previous_path`/
# `group_title` where CodeReviewFileRow and CodeReviewWalkthroughGroupRow
# declare `patchTruncated`/`previousPath`/`groupTitle`.
#
# That was not a cosmetic mismatch. validateSceneState treats a missing
# required field as a hard error, and web_ui/src/lib/api-contract/
# bindTopic.ts DROPS a snapshot that fails validation, so from the first
# successful PR fetch onward every scene snapshot for the whole session
# was rejected client-side - the canvas froze for every node, not just
# this one. Building each row explicitly (the `toolCalls` precedent
# below) fixes the casing AND guarantees each row carries exactly the
# contract's fields with the contract's types, so a row that reached the
# state from an old save file cannot put an unexpected key on the wire.
#
# The domain keeps snake_case on purpose: it is what the engine emits and
# what session_save.py has already written to every existing save file.
# The conversion belongs at the wire builder, the same place
# codeReviewScores is coerced to dict[str, str].


def _code_review_file_wire(row: dict[str, Any]) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "path": str(row.get("path", "")),
        "status": str(row.get("status", "modified")),
        "additions": _non_negative_wire_int(row.get("additions")),
        "deletions": _non_negative_wire_int(row.get("deletions")),
        # `patch` rides as "" on purpose. The per-file patches are capped at
        # MAX_FILE_PATCH_CHARS (6000) each and MAX_PR_FILES (100) of them, so
        # forwarding them put up to ~600KB of diff text on EVERY scene
        # republish - roughly ten times the 60KB codeReviewDiffText that was
        # excluded from this payload for exactly that reason (see
        # CodeReviewState's own comment). No frontend code reads it:
        # CodeReviewNodeView renders the unified diff it lazily fetches via
        # fetchCodeReviewDiffText, never these per-file patches. The field
        # stays on the row because the contract declares it required; the
        # engine still reads the real patches from node.state, which is where
        # the fallback pre-screen scans them.
        "patch": "",
        "patchTruncated": bool(row.get("patch_truncated", False)),
    }
    # Genuinely absent (not "") for every non-rename, which is why
    # CodeReviewFileRow.previousPath is the one Optional field on the row.
    previous = str(row.get("previous_path", "") or "")
    if previous:
        wire["previousPath"] = previous
    return wire


def _code_review_walkthrough_wire(row: dict[str, Any]) -> dict[str, Any]:
    raw_paths = row.get("paths")
    return {
        "groupTitle": str(row.get("group_title", "")),
        "paths": [str(path) for path in raw_paths] if isinstance(raw_paths, list) else [],
        "explanation": str(row.get("explanation", "")),
    }


def _code_review_finding_wire(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "severity": str(row.get("severity", "")),
        "tier": str(row.get("tier", "")),
        "category": str(row.get("category", "")),
        "path": str(row.get("path", "")),
        "line": _non_negative_wire_int(row.get("line")),
        "title": str(row.get("title", "")),
        "evidence": str(row.get("evidence", "")),
        "impact": str(row.get("impact", "")),
        "recommendation": str(row.get("recommendation", "")),
    }


def _code_review_error_wire(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id", "")),
        "severity": str(row.get("severity", "")),
        "tier": str(row.get("tier", "")),
        "kind": str(row.get("kind", "")),
        "path": str(row.get("path", "")),
        "line": _non_negative_wire_int(row.get("line")),
        "title": str(row.get("title", "")),
        "evidence": str(row.get("evidence", "")),
        "fix": str(row.get("fix", "")),
    }


def _code_review_qa_wire(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": str(row.get("question", "")),
        "answer": str(row.get("answer", "")),
    }


def _non_negative_wire_int(value: Any) -> int:
    """int for the wire, never raising. A row can reach the wire from a
    hand-edited save file as well as from the engine, and a ValueError
    here would fail the whole scene republish, not just one row."""
    try:
        return max(0, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return 0


def _content_parts_wire(parts: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """R6.3: the wire-side transform for ChatState's own content_parts
    (backend/domain/node_states.py) - a pure mapping function (same posture
    as backend/canvas.py's _research_result_wire). None stays None (never
    []), so "no multimodal content" and "multimodal content that happens to
    be empty" remain distinguishable on the wire. Any part that is a dict
    carrying raw bytes under its "data" key gets that key base64-encoded as
    a string via content_codec.encode_image_bytes - matching content_codec.
    process_content_for_serialization's own output shape exactly - while
    leaving every other key/part untouched. Builds fresh dicts throughout;
    never mutates the SceneNode's own in-memory parts (which must keep
    holding real bytes, per content_parts's own field contract)."""
    if parts is None:
        return None
    wire_parts: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("data"), (bytes, bytearray)):
            wire_part = dict(part)
            wire_part["data"] = _content_codec.encode_image_bytes(bytes(part["data"]))
            wire_parts.append(wire_part)
        elif isinstance(part, dict):
            wire_parts.append(dict(part))
        else:
            wire_parts.append(part)
    return wire_parts


# -- Other nested-row transforms --------------------------------------------


def _history_wire(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ADR-006 stage 6.4: the projection stays a strict allow-list (never
    # spread the raw dict - legacy entries can carry arbitrary keys), widened
    # by exactly one optional marker: "incomplete" flags a partial assistant
    # reply whose stream died (see append_conversation_assistant_message).
    return [
        {"role": m["role"], "content": m["content"], "incomplete": bool(m.get("incomplete", False))}
        for m in history
    ]


def _tool_calls_wire(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ADR-007 stage 7.4: see ChatState.tool_invocations' own comment and
    # ToolInvocationRow's docstring (contracts/graphlink_scene_payload.py)
    # for why `arguments` is JSON-encoded rather than passed as an object.
    return [
        {
            "id": str(call.get("id", "")),
            "name": str(call.get("name", "")),
            "argumentsJson": json.dumps(call.get("arguments") or {}, sort_keys=True),
            "result": str(call.get("result", "")),
            "isError": bool(call.get("is_error", False)),
        }
        for call in calls
    ]


def _dict_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _string_dict(values: dict[Any, Any]) -> dict[str, str]:
    # Scores ride the wire as dict[str, str] - the store_gitlink_context
    # str-coercion precedent (contracts only admit string-valued dicts on
    # SceneNodeRow).
    return {str(k): str(v) for k, v in values.items()}


def _plan_steps_wire(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ADR-008 stage 8.3: the Builder's checklist - see PlanState's docstring.
    return [
        {"id": s["id"], "title": s["title"], "status": s["status"], "detail": s["detail"]}
        for s in steps
    ]


def _builder_activity_wire(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ADR-008 stage 8.7: the run's own activity log - see PlanState's
    # docstring for why this is untouched by undo.
    return [
        {
            "tool": a["tool"], "summary": a["summary"],
            "outcome": a["outcome"], "stepId": a["stepId"],
            "elapsedMs": a["elapsedMs"],
        }
        for a in activity
    ]


def _harness_activity_wire(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"tool": a["tool"], "summary": a["summary"], "outcome": a["outcome"], "elapsedMs": a["elapsedMs"]}
        for a in activity
    ]


def _harness_plan_wire(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"text": s["text"], "status": s["status"]} for s in plan]


def _code_review_rows(builder: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[Any], Any]:
    return lambda rows: [builder(row) for row in rows]


# -- Field tables -------------------------------------------------------------
#
# (wire key, attribute, transform or None, default). Built once from compact
# specs; the default is looked up in WIRE_DEFAULTS so there is exactly one
# place a default is written down on this side.

_Entry = tuple[str, str, Callable[[Any], Any] | None, Any]


def _table(*specs: tuple[Any, ...]) -> tuple[_Entry, ...]:
    return tuple(
        (spec[0], spec[1], spec[2] if len(spec) > 2 else None, WIRE_DEFAULTS[spec[0]])
        for spec in specs
    )


# Fields on SceneNode itself, sent for every kind.
_NODE_FIELDS = _table(
    ("content", "content"),
    ("isCollapsed", "is_collapsed"),
    ("isDocked", "is_docked"),
    ("history", "history", _history_wire),
    # R4.3: generic across any kind with its own dispatch slot.
    ("pendingRequestId", "pending_request_id"),
    # R6.1: core fields on every kind (see SceneNodeRow's own comment).
    ("color", "color"),
    ("headerColor", "header_color"),
    ("itemIds", "item_ids", list),
)

_GROUP_SIZE_FIELDS = (("groupWidth", "group_width"), ("groupHeight", "group_height"))

# Each state class's own fields. Keyed by the exact classes the old flat
# builder tested with isinstance; _fields_for() resolves a state's type
# against these the same way, subclasses included.
_STATE_FIELDS: dict[type, tuple[_Entry, ...]] = {
    ChatState: _table(
        ("isUser", "is_user"),
        # ADR-002 Workstream 1 ("Synthesize Branches") - output provenance.
        ("provider", "provider"),
        ("model", "model"),
        # ADR-018 stage 18.3: the input routing pin, distinct from the pair
        # above - see ChatState's own comment.
        ("overrideProvider", "override_provider"),
        ("overrideModelId", "override_model_id"),
        ("indexIntoKnowledge", "index_into_knowledge"),
        ("isBranchSynthesis", "is_branch_synthesis"),
        ("synthesisInstructions", "synthesis_instructions"),
        ("branchStatus", "branch_status"),
        ("responseIncomplete", "response_incomplete"),
        ("promptTokens", "prompt_tokens"),
        ("completionTokens", "completion_tokens"),
        ("estimatedCostUsd", "estimated_cost_usd"),
        ("toolCalls", "tool_invocations", _tool_calls_wire),
        ("chatScrollValue", "chat_scroll_value"),
        ("contentParts", "content_parts", _content_parts_wire),
    ),
    CodeState: _table(("code", "code"), ("language", "language")),
    DocumentState: _table(
        ("attachmentKind", "attachment_kind"),
        ("filePath", "file_path"),
        ("mimeType", "mime_type"),
        ("durationSeconds", "duration_seconds"),
        ("byteSize", "byte_size"),
        ("previewLabel", "preview_label"),
    ),
    ImageState: _table(("imageAssetId", "image_asset_id")),
    WebResearchState: _table(
        ("researchStage", "research_stage"),
        ("researchCompleted", "research_completed"),
        ("researchTotal", "research_total"),
        ("researchActiveSourceId", "research_active_source_id"),
        ("researchError", "research_error"),
        ("researchResult", "research_result"),
        ("researchRetainToKnowledge", "research_retain_to_knowledge"),
    ),
    ArtifactState: _table(("artifactContent", "artifact_content"), ("artifactError", "artifact_error")),
    # gitlinkContextXml is DELIBERATELY absent - see GitlinkState's own
    # comment; served on demand via the fetchGitlinkContext intent.
    GitlinkState: _table(
        ("gitlinkRepo", "gitlink_repo"),
        ("gitlinkBranch", "gitlink_branch"),
        ("gitlinkScopeMode", "gitlink_scope_mode"),
        ("gitlinkLocalRoot", "gitlink_local_root"),
        ("gitlinkRepoFilePaths", "gitlink_repo_file_paths", list),
        ("gitlinkSelectedPaths", "gitlink_selected_paths", list),
        ("gitlinkTaskPrompt", "gitlink_task_prompt"),
        ("gitlinkContextStats", "gitlink_context_stats", dict),
        ("gitlinkContextSummary", "gitlink_context_summary"),
        # R5.3 post-review FIX 6: the lazy-fetch cache key - see
        # GitlinkState's own comment for why the summary alone cannot be.
        ("gitlinkContextVersion", "gitlink_context_version"),
        ("gitlinkProposalMarkdown", "gitlink_proposal_markdown"),
        ("gitlinkPendingChanges", "gitlink_pending_changes", _dict_rows),
        ("gitlinkPreviewText", "gitlink_preview_text"),
        ("gitlinkChangeFingerprint", "gitlink_change_fingerprint"),
        ("gitlinkChangeState", "gitlink_change_state"),
        ("gitlinkError", "gitlink_error"),
    ),
    # codeReviewDiffText is DELIBERATELY absent - see CodeReviewState's own
    # comment; served on demand via fetchCodeReviewDiffText.
    CodeReviewState: _table(
        ("codeReviewPrUrl", "code_review_pr_url"),
        ("codeReviewRepo", "code_review_repo"),
        ("codeReviewPrNumber", "code_review_pr_number"),
        ("codeReviewPrTitle", "code_review_pr_title"),
        ("codeReviewPrState", "code_review_pr_state"),
        ("codeReviewPrHtmlUrl", "code_review_pr_html_url"),
        ("codeReviewBaseRef", "code_review_base_ref"),
        ("codeReviewHeadRef", "code_review_head_ref"),
        ("codeReviewAdditions", "code_review_additions"),
        ("codeReviewDeletions", "code_review_deletions"),
        ("codeReviewChangedFiles", "code_review_changed_files"),
        ("codeReviewFiles", "code_review_files", _code_review_rows(_code_review_file_wire)),
        ("codeReviewFilesTruncated", "code_review_files_truncated"),
        ("codeReviewDiffTruncated", "code_review_diff_truncated"),
        ("codeReviewDiffChars", "code_review_diff_chars"),
        # The lazy-diff cache key, bumped by every successful fetch.
        ("codeReviewDiffVersion", "code_review_diff_version"),
        ("codeReviewWalkthrough", "code_review_walkthrough", _code_review_rows(_code_review_walkthrough_wire)),
        ("codeReviewFindings", "code_review_findings", _code_review_rows(_code_review_finding_wire)),
        ("codeReviewErrors", "code_review_errors", _code_review_rows(_code_review_error_wire)),
        ("codeReviewDismissedIds", "code_review_dismissed_ids", list),
        ("codeReviewTitle", "code_review_title"),
        ("codeReviewOverview", "code_review_overview"),
        ("codeReviewConfidence", "code_review_confidence"),
        ("codeReviewScores", "code_review_scores", _string_dict),
        ("codeReviewQualityScore", "code_review_quality_score"),
        ("codeReviewVerdict", "code_review_verdict"),
        ("codeReviewRisk", "code_review_risk"),
        ("codeReviewQualitySummary", "code_review_quality_summary"),
        ("codeReviewQa", "code_review_qa", _code_review_rows(_code_review_qa_wire)),
        ("codeReviewState", "code_review_state"),
        ("codeReviewError", "code_review_error"),
    ),
    # codeSandboxSandboxId is DELIBERATELY absent - pure internal
    # directory-naming key, see CodeSandboxState's own comment.
    CodeSandboxState: _table(
        ("codeSandboxRequirements", "code_sandbox_requirements"),
        ("codeSandboxPrompt", "code_sandbox_prompt"),
        ("codeSandboxCode", "code_sandbox_code"),
        ("codeSandboxOutput", "code_sandbox_output"),
        ("codeSandboxAnalysis", "code_sandbox_analysis"),
        ("codeSandboxAwaitingApproval", "code_sandbox_awaiting_approval"),
        # R5.4: the frozen-at-approval-time snapshot, distinct from the live
        # draft above - see CodeSandboxState's own comment.
        ("codeSandboxApprovalRequirements", "code_sandbox_approval_requirements"),
        ("codeSandboxApprovalAllowSourceBuilds", "code_sandbox_approval_allow_source_builds"),
        ("codeSandboxApprovalIsRepair", "code_sandbox_approval_is_repair"),
        ("codeSandboxError", "code_sandbox_error"),
    ),
    NoteState: _table(
        ("isSystemPrompt", "is_system_prompt"),
        ("isSummaryNote", "is_summary_note"),
        ("isBranchComparison", "is_branch_comparison"),
    ),
    # groupManualWidth/Height are DELIBERATELY absent - server-side
    # bookkeeping only (R6.1).
    FrameState: _table(("isLocked", "is_locked"), *_GROUP_SIZE_FIELDS),
    ContainerState: _table(*_GROUP_SIZE_FIELDS),
    ChartState: _table(
        ("chartType", "chart_type"),
        ("chartData", "chart_data", dict),
        ("chartError", "chart_error"),
        ("chartWidth", "chart_width"),
        ("chartHeight", "chart_height"),
        ("chartAspectLocked", "chart_aspect_locked"),
        ("chartSourceNodeId", "chart_source_node_id"),
    ),
    HtmlState: _table(("htmlSplitterState", "html_splitter_state")),
    PlanState: _table(
        ("planGoal", "plan_goal"),
        ("planSteps", "plan_steps", _plan_steps_wire),
        ("builderActivity", "builder_activity", _builder_activity_wire),
        ("builderStatus", "builder_status"),
        ("builderMode", "builder_mode"),
        ("builderRunId", "builder_run_id"),
        ("builderMaxSteps", "builder_max_steps"),
        ("builderMaxTokens", "builder_max_tokens"),
        ("builderMaxWallSeconds", "builder_max_wall_seconds"),
        ("builderSpentSteps", "builder_spent_steps"),
        ("builderSpentTokens", "builder_spent_tokens"),
        ("builderSpentWallSeconds", "builder_spent_wall_seconds"),
        ("builderAwaitingToolApproval", "builder_awaiting_tool_approval"),
        ("builderApprovalToolName", "builder_approval_tool_name"),
        ("builderApprovalSummary", "builder_approval_summary"),
        ("builderStatusDetail", "builder_status_detail"),
    ),
    # PLAN-2026-08-24 H1: the conversation history deliberately does NOT
    # cross the wire - the transcript lives in the workspace; only the render
    # surface does (see HarnessState's own docstring).
    HarnessState: _table(
        ("harnessGoal", "harness_goal"),
        ("harnessReply", "harness_reply"),
        ("harnessStatus", "harness_status"),
        ("harnessStatusDetail", "harness_status_detail"),
        ("harnessRunId", "harness_run_id"),
        ("harnessActivity", "harness_activity", _harness_activity_wire),
        ("harnessAwaitingApproval", "harness_awaiting_approval"),
        ("harnessApprovalToolName", "harness_approval_tool_name"),
        ("harnessApprovalSummary", "harness_approval_summary"),
        ("harnessApprovalSessionOffered", "harness_approval_session_offered"),
        ("harnessPlan", "harness_plan", _harness_plan_wire),
        ("harnessAwaitingQuestion", "harness_awaiting_question"),
        ("harnessQuestion", "harness_question"),
        ("harnessContextTokens", "harness_context_tokens"),
        ("harnessMaxContextTokens", "harness_max_context_tokens"),
        ("harnessCompactions", "harness_compactions"),
        ("harnessWorkspacePath", "harness_workspace_path"),
        ("harnessWorkspaceActive", "harness_workspace_active"),
        ("harnessMaxTurns", "harness_max_turns"),
        ("harnessSpentTurns", "harness_spent_turns"),
        ("harnessSpentTokens", "harness_spent_tokens"),
    ),
}

_FIELDS_BY_TYPE: dict[type, tuple[_Entry, ...]] = {}


def _fields_for(state_type: type) -> tuple[_Entry, ...]:
    """The state-owned entries for one concrete state type, resolved like the
    isinstance checks they replace (a subclass inherits its bases' fields)
    and cached per type. A plugin's own NodeState subclass matches nothing
    and gets the node-level fields only - exactly what the flat builder
    gave it."""
    fields = _FIELDS_BY_TYPE.get(state_type)
    if fields is None:
        fields = tuple(
            entry
            for owner, entries in _STATE_FIELDS.items()
            if issubclass(state_type, owner)
            for entry in entries
        )
        _FIELDS_BY_TYPE[state_type] = fields
    return fields


def _put_non_default(wire: dict[str, Any], source: object, entries: tuple[_Entry, ...]) -> None:
    for key, attr, transform, default in entries:
        value = getattr(source, attr)
        if transform is not None:
            value = transform(value)
        if value is default or (value.__class__ is default.__class__ and value == default):
            continue
        wire[key] = value


def node_wire(node: SceneNode, *, is_final_deliverable: bool, plugin_state: dict[str, str]) -> dict[str, Any]:
    """One node's sparse wire row: identity keys, then every field whose
    value differs from its wire default. See the module docstring for the
    contract this relies on and the tests that pin it."""
    wire: dict[str, Any] = {
        "id": node.id,
        "x": node.x,
        "y": node.y,
        "title": node.title,
        "kind": node.kind,
    }
    _put_non_default(wire, node, _NODE_FIELDS)
    if is_final_deliverable:
        wire["isFinalDeliverable"] = True
    if node.state is not None:
        _put_non_default(wire, node.state, _fields_for(type(node.state)))
    if plugin_state:
        # ADR-014 stage 14.2: the Plugin SDK's generic live-wire fallback -
        # see SceneDocument._plugin_state_wire.
        wire["pluginState"] = plugin_state
    return wire


def wire_keys() -> frozenset[str]:
    """Every key node_wire can ever emit - for the drift tests."""
    keys = set(IDENTITY_KEYS)
    keys.update(entry[0] for entry in _NODE_FIELDS)
    for entries in _STATE_FIELDS.values():
        keys.update(entry[0] for entry in entries)
    keys.update(("isFinalDeliverable", "pluginState"))
    return frozenset(keys)
