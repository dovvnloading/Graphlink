"""Qt-free session SAVE (Qt-removal plan R6.5) - the mirror path of R6.4's
backend/session_load.py.

Reimplements graphlink_session/serializers.py's SceneSerializer.
serialize_chat_data() against backend/canvas.py's SceneDocument - NOT an
import, same "reimplement, don't import" precedent as session_load.py:
serializers.py has no Qt imports of its own, but it is a module INSIDE the
Qt-tainted graphlink_session package (whose __init__.py eagerly imports
ChatSessionManager/SaveWorkerThread, which import PySide6.QtCore), and every
one of its methods reads live QGraphicsItem attributes (node.pos(),
node.text, chart.rect.width(), ...) that have no meaning on a SceneNode.

THE CORE DESIGN PROBLEM this file exists to solve: legacy's data model has
TWO SEPARATE relationship concepts that this backend's SceneEdge model
merged into one flat `document.edges` dict back in R3-R5:
  1. A node's own `children` list (a live Qt object-tree relationship,
     restored via children_indices/children_ids - see session_load.py's own
     _restore_children) - conceptually distinct from...
  2. Seven (now twelve, after R5's own plugin-connection additions)
     parallel `ConnectionItem`-family visual line objects, each its own
     scene-level list (scene.connections/content_connections/etc.).
...PLUS a THIRD concept, a node's own required structural parent
(parent_content_node_index/parent_node_index), which is never a
"connection" or a "child" at all in legacy - it is baked directly into the
child's OWN payload at construction time.
Since document.edges has no such distinction (an edge is just an edge -
document.connect() is the single primitive every one of these legacy
concepts long ago collapsed onto, R3.1 through R6.1), this serializer must
CLASSIFY every edge back into exactly one of these four buckets before it
can write a legacy-shaped payload. See _classify_edges below for the exact,
total (every edge accounted for exactly once), deterministic algorithm -
this is the single most important piece of logic in this file, the save-
side mirror of session_load.py's own bug #47 offset math.

GROUND TRUTH for every field name/shape below came from the same direct
reads used to build session_load.py: the current serializers.py (the 7
surviving node kinds, the 7 "always existed" connection lists, frame/note/
container/chart/pin serialization, and scene_index.py's own get_all_nodes/
get_all_serializable_items/CHILD_LINK_NODE_TYPES/NODE_LIST_NAMES), plus
`git show af72ffd~1:graphlink_app/graphlink_session/serializers.py` for the
5 plugin kinds (pycoder/code_sandbox/web/artifact/gitlink) R5-closeout later
deleted the branches for.

NESTED CONTAINERS: scene_index.py's get_all_serializable_items positions
containers LAST in the combined item-index space
(nodes+notes+charts+frames+containers), and create_container legitimately
allows a container to hold another container. _serialize_container mirrors
that index scheme exactly. backend/session_load.py resolves the matching
tail indices incrementally while restoring containers, including a deferred
pass for payloads that are not dependency-ordered, so nested membership now
round-trips instead of losing the outer group.

DELIBERATE SIMPLIFICATION (documented, not silent): legacy's own title
generation for a brand-new chat tries an LLM call first (title_generator.
generate_title, a 2-3-word-title prompt to Ollama/the API provider) before
falling back to a plain heuristic (first 5 words of the seed message,
regex-matched, truncated to 80 chars; else a timestamp). This module ports
ONLY the heuristic fallback (byte-for-byte identical to
SaveWorkerThread._fallback_title's own regex/truncation), never the LLM
call - titles are cosmetic, not correctness-critical, and skipping the LLM
call keeps Save synchronous and free of a new agent-dispatch surface for a
purely cosmetic quality difference.

TWO FURTHER ACCEPTED GAPS surfaced by an adversarial review of this
increment, both judged out of scope to fix here (each is a pre-existing
ambiguity/looseness in this backend's OWN live domain model, dating to
R3.1/R6.1 respectively - a genuine fix belongs in canvas.py, with its own
dedicated recon/design/test cycle, not bolted onto a serializer):

1. children_indices vs. an ordinary connection, for chat/conversation/html
   pairs specifically: _classify_edges' Step C cannot distinguish "this
   edge represents a genuine branch-continuation relationship" from "the
   user manually drew an ordinary connectNodes line between two pre-
   existing chat-family nodes" - this backend's own document.edges model
   has represented both identically, with zero distinguishing metadata,
   since R3.1 first unified them; nothing about R6.5 introduces the
   ambiguity, it just has to serialize whatever state already exists. The
   practical consequence: a manually-drawn link between two chat/
   conversation/html nodes, saved and then reloaded in the CURRENT legacy
   Qt app specifically, would be interpreted as a real children_indices
   branch relationship there (affecting that app's own delete_chat_node
   reparenting logic) - reloading through this project's own
   session_load.py is unaffected either way, since both readings resolve
   to the identical document.connect() call.
2. A note included in a frame's own members (create_frame has no kind
   restriction, unlike create_container, which explicitly documents the
   contrast) has no representable position in _serialize_frame's own
   frame_source_index (which spans only regular nodes + charts, matching
   deserialize_frame's own frame_source_map) - such a note is silently
   excluded from that frame's serialized item list on save (the note
   itself still serializes fine as its own independent notes_data entry;
   it just stops being grouped in that frame). Legacy's own frame concept
   never included notes as members in the first place (see
   session_load.py's own docstring: "frames may reference charts as
   members, never notes"), so this is closer to a self-correcting no-op
   than a data-loss bug - but it is not literally a no-op.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from typing import Any

from backend.canvas import SceneDocument, SceneNode, _content_codec
from backend.plugin_sdk import NodeKindSpec, PluginRegistry, discover_plugins
from graphlink_settings_store import SettingsManager

logger = logging.getLogger(__name__)

# ADR-009 stage 9.5: the asset store in effect for the CURRENT save. Same
# contextvar rationale as session_load.py's own _ACTIVE_ASSET_STORE - the
# per-kind serializer dispatch below is a table of (node, document)
# lambdas, and widening all of them for the one kind that needs a store
# would be churn. Set and reset by build_chat_data around a synchronous
# call, so it can never leak between sessions.
_ACTIVE_SAVE_ASSET_STORE: contextvars.ContextVar = contextvars.ContextVar(
    "graphlink_active_save_asset_store", default=None
)

# The 13 "regular" node kinds - everything that is NOT note/frame/container/
# chart. Mirrors scene_index.py's own NODE_LIST_NAMES (7 kinds, the current,
# post-R5-closeout surviving set) PLUS the 6 kinds R5-closeout deleted from
# the legacy app's own lists but which this backend still fully supports
# (see this module's own docstring: a NEW-app save containing one of these
# 6 will simply have that one node silently skipped if ever loaded back into
# the CURRENT legacy app, matching legacy's own tolerant unrecognized-
# node_type behavior - not a regression, since the legacy app's own load
# path already lost the ability to restore these 6 kinds when R5-closeout
# deleted their deserializer branches).
#
# "harness" (PLAN-2026-08-24) was missing from this tuple entirely until a
# technical-debt audit caught it: _serialize_harness_node/_restore_harness_
# payload (both below/in session_load.py) were fully implemented and wired
# into their own dispatch tables from the day the feature shipped, but
# `all_nodes` (below) never included a harness node in the first place - so
# every harness node a user created was silently dropped on the very next
# autosave or manual Save, with no error anywhere. Restoring it here is the
# entire fix; both serializer and restorer already existed and are correct.
_REGULAR_KINDS = (
    "chat", "code", "document", "image", "thinking", "conversation", "html",
    "web_research", "artifact", "gitlink", "code_review", "code_sandbox", "plan", "harness",
)


def _is_plugin_kind(kind: str) -> bool:
    """True for a Plugin SDK node kind, recognized by its namespacing alone
    (f"{plugin_id}.{kind}" - backend/plugin_sdk.py's HostContext.
    register_node_kind) rather than by membership in the CURRENT registry.

    That distinction is the whole point. `all_nodes` used to include a
    plugin node only when this save's own registry still recognized its
    kind, so a plugin that failed discovery at save time - an import error,
    an out-of-process worker that could not spawn, a directory renamed;
    discover_plugins swallows any of those per plugin - silently EXCLUDED
    every one of its live nodes from the written row, destroying them on the
    next autosave. It also made _serialize_plugin_node's own documented
    kind_spec-is-None branch ("this function itself never drops a node
    outright") unreachable dead code. A node that is sitting in the live
    document gets written out, whether or not the code that created it
    happens to be loadable this second; no built-in kind contains a dot, so
    this can never capture one."""
    return "." in kind

# Mirrors backend/session_load.py's own _PARENT_CONTENT_INDEX_KINDS /
# _PARENT_NODE_INDEX_KINDS split exactly - every regular kind except "chat"
# requires exactly one incoming edge (its structural parent), keyed to one
# of two legacy field names depending on kind.
_PARENT_CONTENT_INDEX_KINDS = {"code", "document", "image", "thinking"}
_PARENT_NODE_INDEX_KINDS = {
    "conversation", "html", "code_sandbox", "web_research", "artifact", "gitlink",
    "code_review",
}

# Mirrors scene_index.py's CHILD_LINK_NODE_TYPES = (ChatNode, ConversationNode,
# HtmlViewNode) exactly - see session_load.py's own _CHILD_LINK_KINDS.
_CHILD_LINK_KINDS = {"chat", "conversation", "html"}

_SNAKE_RE = re.compile(r"_([a-z0-9])")
_CAMEL_RE = re.compile(r"([A-Z])")


def _camel_to_snake(key: str) -> str:
    return _CAMEL_RE.sub(lambda m: "_" + m.group(1).lower(), key)


def _camel_to_snake_deep(value: Any) -> Any:
    """Inverse of session_load.py's _snake_to_camel_deep - reverses the
    research_result translation back into the snake_case shape
    ResearchResult.to_dict()-equivalent construction produces, which is what
    legacy's own WebNode.research_result_payload (and therefore a legacy
    reload) expects."""
    if isinstance(value, dict):
        return {_camel_to_snake(str(k)): _camel_to_snake_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camel_to_snake_deep(item) for item in value]
    return value


def _position(node: SceneNode) -> dict[str, float]:
    return {"x": float(node.x), "y": float(node.y)}


def _serialize_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _content_codec.serialize_history(history or [])


# -- per-kind node serialization ----------------------------------------
# Each function takes a SceneNode already known to be of the matching kind
# and returns its OWN payload dict - node_type/position/id are added by the
# caller uniformly (build_chat_data below), matching
# _serialize_node_with_identity's own "stamp id/children_ids outside the
# isinstance chain" posture. Parent-index fields are filled in later, once
# every node's own id has a resolved combined-space position (see
# _classify_edges) - these functions never reference `document` or other
# nodes at all, mirroring session_load.py's equivalent restorers.

def _serialize_chat_node(node: SceneNode) -> dict[str, Any]:
    if node.state.content_parts is not None:
        raw_content = _content_codec.process_content_for_serialization(node.state.content_parts)
    else:
        raw_content = node.content
    return {
        "node_type": "chat",
        "raw_content": raw_content,
        "is_user": bool(node.state.is_user),
        "conversation_history": _serialize_history(node.history),
        "scroll_value": node.state.chat_scroll_value,
        "is_collapsed": bool(node.is_collapsed),
        # ADR-002 Workstream 1 ("Branch status and lifecycle") - confirmed,
        # pre-existing gap fixed inline: provider/model/is_branch_synthesis/
        # synthesis_instructions/item_ids (Synthesize Branches - item_ids
        # here is the source branch node ids, the SAME generic-field reuse
        # Compare Branches' own note.item_ids already established) already
        # synced live to the frontend via scene_payload() but were never
        # written here, so a Save-then-Load cycle silently dropped a
        # synthesis result's provenance and its badge. branch_status is
        # this same pass's own new field, added alongside rather than in a
        # separate edit.
        "provider": node.state.provider,
        "model": node.state.model,
        "is_branch_synthesis": bool(node.state.is_branch_synthesis),
        "synthesis_instructions": node.state.synthesis_instructions,
        "item_ids": list(node.item_ids),
        "branch_status": node.state.branch_status,
        # ADR-006 stage 6.4: interrupted-reply marker survives save/load so
        # the retry affordance is still offered after a session reload.
        "response_incomplete": bool(node.state.response_incomplete),
        # ADR-006 stage 6.8: provider-reported usage stamped on the reply -
        # survives save/load like every other provenance field above.
        "prompt_tokens": node.state.prompt_tokens,
        "completion_tokens": node.state.completion_tokens,
        # ADR-016 stage 16.2: the cost snapshot taken when usage was stamped
        # - survives save/load like prompt_tokens/completion_tokens above.
        "estimated_cost_usd": node.state.estimated_cost_usd,
        # ADR-007 stage 7.4: a plain list-of-dicts copy - see ChatState.
        # tool_invocations' own comment for the exact shape. Domain-side
        # `arguments` stays a real dict here (only JSON-encoded at the wire
        # boundary in graph.py's scene_payload()), so the saved session file
        # keeps it structured too.
        "tool_invocations": [dict(call) for call in node.state.tool_invocations],
        # ADR-018 stage 18.3: the model pin - survives save/load like
        # provider/model above, but is the opposite direction (input
        # routing, not output provenance) - see ChatState's own comment.
        "override_provider": node.state.override_provider,
        "override_model_id": node.state.override_model_id,
    }


def _serialize_code_node(node: SceneNode) -> dict[str, Any]:
    return {"node_type": "code", "code": node.state.code, "language": node.state.language}


def _serialize_document_node(node: SceneNode) -> dict[str, Any]:
    return {
        "node_type": "document",
        "title": node.title,
        "content": node.content,
        "attachment_kind": node.state.attachment_kind,
        "file_path": node.state.file_path,
        "mime_type": node.state.mime_type,
        "duration_seconds": node.state.duration_seconds,
        "byte_size": node.state.byte_size,
        "preview_label": node.state.preview_label,
        "is_collapsed": bool(node.is_collapsed),
        "is_docked": bool(node.is_docked),
    }


def _serialize_image_node(
    node: SceneNode, document: SceneDocument, asset_store: Any | None = None
) -> dict[str, Any]:
    """ADR-009 stage 9.5: writes the image's bytes to the content-addressed
    asset store when one is supplied, emitting only a ref - so autosave
    stops rewriting megabytes of base64 on every 30-second tick for an
    image that has not changed.

    WRITE-NEW / READ-BOTH, not a destructive migration. With no store
    (every existing direct caller and test), this emits the historical
    inline `image_bytes` exactly as before. session_load.py reads either
    shape, so a chat saved by an older build keeps loading untouched and no
    row ever has to be rewritten to make this safe. The inline path is
    what a future cleanup deletes, once no old rows remain in the wild."""
    asset = document.image_assets.get(node.state.image_asset_id)
    image_bytes = asset[0] if asset is not None else b""
    mime_type = asset[1] if asset is not None else "image/png"

    if asset_store is not None and image_bytes:
        return {
            "node_type": "image",
            "asset_ref": asset_store.put(image_bytes),
            "mime_type": mime_type,
            "prompt": node.content,
        }
    # No bytes in hand, but this node was loaded from a row that named an
    # asset_ref we could not read at the time - write that ref straight back
    # out rather than replacing it with an empty inline payload. See
    # ImageState.unresolved_asset_ref: the asset file is very often still on
    # disk, and erasing the only pointer to it is what turned a transient
    # read failure into permanent loss on the next autosave.
    unresolved_ref = getattr(node.state, "unresolved_asset_ref", "")
    if not image_bytes and unresolved_ref:
        return {
            "node_type": "image",
            "asset_ref": unresolved_ref,
            "mime_type": getattr(node.state, "unresolved_asset_mime_type", "") or "image/png",
            "prompt": node.content,
        }
    return {
        "node_type": "image",
        "image_bytes": _content_codec.encode_image_bytes(image_bytes),
        "prompt": node.content,
    }


def _serialize_thinking_node(node: SceneNode) -> dict[str, Any]:
    return {"node_type": "thinking", "thinking_text": node.content, "is_docked": bool(node.is_docked)}


def _serialize_conversation_node(node: SceneNode) -> dict[str, Any]:
    return {
        "node_type": "conversation",
        "conversation_history": _serialize_history(node.history),
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_html_node(node: SceneNode) -> dict[str, Any]:
    return {
        "node_type": "html",
        "html_content": node.content,
        "splitter_state": node.state.html_splitter_state,
        "conversation_history": _serialize_history(node.history),
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_web_node(node: SceneNode) -> dict[str, Any]:
    research_result = _camel_to_snake_deep(node.state.research_result) if node.state.research_result else {}
    return {
        # R6.5 translation (inverse of R6.4's own): backend kind
        # "web_research" -> legacy node_type "web".
        "node_type": "web",
        "query": node.content,
        "research_result": research_result,
        # ADR-021 stage 21.5: a net-new field with no legacy counterpart -
        # additive, so a legacy reader simply ignores it and an older saved
        # row restores it as its False default.
        "retain_to_knowledge": bool(node.state.research_retain_to_knowledge),
        "conversation_history": _serialize_history(node.history),
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_artifact_node(node: SceneNode) -> dict[str, Any]:
    return {
        "node_type": "artifact",
        "instruction": node.content,
        "content": node.state.artifact_content,
        "conversation_history": _serialize_history(node.history),
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_gitlink_node(node: SceneNode) -> dict[str, Any]:
    return {
        "node_type": "gitlink",
        "task_prompt": node.state.gitlink_task_prompt,
        "repo_state": {
            "repo": node.state.gitlink_repo,
            "branch": node.state.gitlink_branch,
            "scope_mode": node.state.gitlink_scope_mode,
            "local_root": node.state.gitlink_local_root,
            "imported_root": node.state.gitlink_imported_root,
        },
        "repo_file_paths": list(node.state.gitlink_repo_file_paths),
        "selected_paths": list(node.state.gitlink_selected_paths),
        "context_xml": node.state.gitlink_context_xml,
        "context_stats": dict(node.state.gitlink_context_stats),
        # Inverse of R6.4's own proposal_data["files"] -> gitlink_pending_changes
        # unpack.
        "proposal_data": {"files": list(node.state.gitlink_pending_changes)},
        "preview_text": node.state.gitlink_preview_text,
        "conversation_history": _serialize_history(node.history),
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_code_review_node(node: SceneNode) -> dict[str, Any]:
    # NOTE (ADR-002 stage 2.5 gate): every field below is read as
    # node.state.<field>, never via a `state = node.state` alias -
    # tests/test_node_state_migration.py's bare-attribute ban only
    # recognizes the `X.state.<field>` shape, so an alias would fail the
    # build (the _serialize_gitlink_node precedent reads the same way).
    return {
        "node_type": "code_review",
        "pr_url": node.state.code_review_pr_url,
        "pr_state": {
            "repo": node.state.code_review_repo,
            "number": node.state.code_review_pr_number,
            "title": node.state.code_review_pr_title,
            "state": node.state.code_review_pr_state,
            "html_url": node.state.code_review_pr_html_url,
            "base_ref": node.state.code_review_base_ref,
            "head_ref": node.state.code_review_head_ref,
        },
        "additions": node.state.code_review_additions,
        "deletions": node.state.code_review_deletions,
        "changed_files": node.state.code_review_changed_files,
        "files": [dict(entry) for entry in node.state.code_review_files],
        "files_truncated": bool(node.state.code_review_files_truncated),
        "diff_text": node.state.code_review_diff_text,
        "diff_truncated": bool(node.state.code_review_diff_truncated),
        "diff_chars": node.state.code_review_diff_chars,
        "diff_version": node.state.code_review_diff_version,
        "review": {
            "walkthrough": [dict(group) for group in node.state.code_review_walkthrough],
            "findings": [dict(item) for item in node.state.code_review_findings],
            "errors": [dict(item) for item in node.state.code_review_errors],
            "dismissed_ids": list(node.state.code_review_dismissed_ids),
            "title": node.state.code_review_title,
            "overview": node.state.code_review_overview,
            "confidence": node.state.code_review_confidence,
            "scores": dict(node.state.code_review_scores),
            "quality_score": node.state.code_review_quality_score,
            "verdict": node.state.code_review_verdict,
            "risk": node.state.code_review_risk,
            "quality_summary": node.state.code_review_quality_summary,
        },
        "qa": [dict(entry) for entry in node.state.code_review_qa],
        "review_state": node.state.code_review_state,
        "error": node.state.code_review_error,
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_code_sandbox_node(node: SceneNode) -> dict[str, Any]:
    return {
        "node_type": "code_sandbox",
        "prompt": node.state.code_sandbox_prompt,
        "requirements": node.state.code_sandbox_requirements,
        "code": node.state.code_sandbox_code,
        "output": node.state.code_sandbox_output,
        "analysis": node.state.code_sandbox_analysis,
        "sandbox_id": node.state.code_sandbox_sandbox_id,
        "conversation_history": _serialize_history(node.history),
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_plan_node(node: SceneNode) -> dict[str, Any]:
    """ADR-008 stage 8.3: the Builder plan node. NEW-app-only kind (the
    legacy app never had a Builder) - a legacy load silently skips it, the
    same documented tolerant behavior every post-legacy kind gets. The
    LIVE-run fields (awaiting approval + its summary) are deliberately NOT
    persisted: they describe a RunHandle that cannot survive a restart;
    session_load's restorer likewise normalizes a non-terminal
    builder_status to "interrupted" (see PlanState's own docstring)."""
    return {
        "node_type": "plan",
        "goal": node.state.plan_goal,
        "steps": [dict(s) for s in node.state.plan_steps],
        # stage 8.7: the run's activity log - persisted like every other
        # PlanState field so it survives restart alongside the resume
        # point, and (unlike the LIVE-run fields below) it describes past
        # calls rather than an in-flight RunHandle, so nothing here needs
        # load-time normalization.
        "activity": [dict(a) for a in node.state.builder_activity],
        "builder_status": node.state.builder_status,
        "builder_mode": node.state.builder_mode,
        "builder_run_id": node.state.builder_run_id,
        "max_steps": node.state.builder_max_steps,
        "max_tokens": node.state.builder_max_tokens,
        "max_wall_seconds": node.state.builder_max_wall_seconds,
        "spent_steps": node.state.builder_spent_steps,
        "spent_tokens": node.state.builder_spent_tokens,
        "spent_wall_seconds": node.state.builder_spent_wall_seconds,
        "status_detail": node.state.builder_status_detail,
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_harness_node(node: SceneNode) -> dict[str, Any]:
    """PLAN-2026-08-24 H1: the harness node. NEW-app-only kind, same
    tolerant legacy-skip posture as the plan node. Deliberately small:
    conversation history lives in the workspace transcript, not here (see
    HarnessState's own docstring) - what persists is the render surface
    plus the two durable identities (workspace id, last run id).
    session_load normalizes a non-terminal harness_status to
    "interrupted", the exact PlanState treatment."""
    return {
        "node_type": "harness",
        "goal": node.state.harness_goal,
        "reply": node.state.harness_reply,
        "harness_status": node.state.harness_status,
        "status_detail": node.state.harness_status_detail,
        "harness_run_id": node.state.harness_run_id,
        "workspace_id": node.state.harness_workspace_id,
        # The user-dir request persists (re-checked against trust on the
        # next run); the resolved-active result does not - it is recomputed
        # per run, and a stale "active" from a machine that trusted the dir
        # must never imply trust on a machine that does not.
        "workspace_path": node.state.harness_workspace_path,
        "activity": [dict(a) for a in node.state.harness_activity],
        "max_turns": node.state.harness_max_turns,
        "spent_turns": node.state.harness_spent_turns,
        "spent_tokens": node.state.harness_spent_tokens,
        # H3: the context estimate and compaction count describe the
        # workspace transcript, which outlives the process - unlike the
        # live approval fields above, which deliberately do not persist.
        "context_tokens": node.state.harness_context_tokens,
        "max_context_tokens": node.state.harness_max_context_tokens,
        "compactions": node.state.harness_compactions,
        "is_collapsed": bool(node.is_collapsed),
    }


def _serialize_plugin_node(
    node: SceneNode,
    kind_spec: "NodeKindSpec | None",
    settings_manager: "SettingsManager | None" = None,
) -> dict[str, Any]:
    """ADR-014 stage 14.2: the generic persistence fallback for any node
    whose kind is not one of the built-ins above (i.e. not a key in
    _NODE_SERIALIZERS) - a Plugin SDK node kind, always namespaced as
    f"{plugin_id}.{kind}" (backend/plugin_sdk.py's HostContext.
    register_node_kind). Always persists the same universal fields
    SceneDocument.add_plugin_node/_node_wire already guarantee exist for
    EVERY node regardless of kind - node_type/title/content/is_collapsed -
    so a plugin with no NodeState subclass at all (like plugins/hello_node/,
    which fits its one string in `content`) still round-trips through
    save/reload with zero opt-in.

    `kind_spec` is `None` when this save's PluginRegistry doesn't currently
    recognize the node's kind at all (a plugin removed/renamed since this
    node was created) - the universal fields above still get written (a
    later reload's own registry-membership check decides whether to
    restore the node at all; see session_load.py's own _restore_node), this
    function itself never drops a node outright.

    When the plugin DID opt into HostContext.register_node_kind(...,
    serialize=...), that hook's own dict is nested under 'plugin_state' -
    deliberately isolated from the universal fields above so a raising or
    non-JSON-serializable plugin serializer can never corrupt title/content/
    is_collapsed sitting right next to it, and never abort the WHOLE save
    (every other node on the canvas) either - it just drops its own extra
    state for this one node, the same "one bad item never sinks everything"
    posture every other per-kind serializer/restorer in this file and
    session_load.py already keeps.

    ADR-014 review-fix: `settings_manager`, when given, gates the actual
    kind_spec.serialize(node) call on settings_manager.get_plugin_grants()
    - the SAME install-time consent check node creation/invokePluginIntent/
    register_plugin_tools already enforce before running a plugin's own
    code, extended to this save-path call site (this was the one place a
    revoked plugin's serializer kept running unconditionally, since a save
    happens outside any one session's live SceneDocument and previously had
    no access to Settings > Plugins' grant state at all). `None` (every
    call site this project's own test suite that predates this fix uses)
    preserves the exact prior ungated behavior - see build_chat_data's own
    docstring for why that default is safe. A denied/ungranted node still
    round-trips its universal title/content/is_collapsed fields; it just
    loses its custom plugin_state for this one save, matching the
    already-raising-serializer degrade path immediately below rather than
    inventing a new failure mode."""
    payload: dict[str, Any] = {
        "node_type": node.kind,
        "title": node.title,
        "content": node.content,
        "is_collapsed": bool(node.is_collapsed),
    }
    if kind_spec is not None and kind_spec.serialize is not None:
        granted = (
            settings_manager is None
            or settings_manager.get_plugin_grants().get(kind_spec.plugin_id, False)
        )
        if granted:
            try:
                extra = kind_spec.serialize(node)
            except Exception:
                # ADR-014 review-fix: logged, matching the wire-path
                # equivalent's own exact message shape (backend/domain/
                # graph.py's _plugin_state_wire) - previously this dropped a
                # raising plugin serializer with zero signal anywhere,
                # unlike every other place a plugin's own code runs.
                logger.warning(
                    "plugin node %s (%s): serialize hook raised", node.id, node.kind, exc_info=True,
                )
                extra = None
            if isinstance(extra, dict):
                try:
                    json.dumps(extra)  # validate JSON-safety NOW, isolated to this one node
                except (TypeError, ValueError):
                    extra = None
            if isinstance(extra, dict):
                payload["plugin_state"] = extra
    return payload


_NODE_SERIALIZERS = {
    "chat": lambda node, document: _serialize_chat_node(node),
    "code": lambda node, document: _serialize_code_node(node),
    "document": lambda node, document: _serialize_document_node(node),
    "image": lambda node, document: _serialize_image_node(node, document, _ACTIVE_SAVE_ASSET_STORE.get()),
    "thinking": lambda node, document: _serialize_thinking_node(node),
    "conversation": lambda node, document: _serialize_conversation_node(node),
    "html": lambda node, document: _serialize_html_node(node),
    "web_research": lambda node, document: _serialize_web_node(node),
    "artifact": lambda node, document: _serialize_artifact_node(node),
    "gitlink": lambda node, document: _serialize_gitlink_node(node),
    "code_review": lambda node, document: _serialize_code_review_node(node),
    "code_sandbox": lambda node, document: _serialize_code_sandbox_node(node),
    "plan": lambda node, document: _serialize_plan_node(node),
    "harness": lambda node, document: _serialize_harness_node(node),
}


def _serialize_note(node: SceneNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "content": node.content,
        "position": _position(node),
        # Notes have no manual-size concept in this backend at all (sized
        # purely from content, like legacy itself) - a fixed placeholder
        # size, matching the field's own already-documented gap in
        # session_load.py's own _restore_notes (width/height are read back
        # by NOTHING on load; this exists only because legacy's own SQL
        # schema declares the columns NOT NULL).
        "size": {"width": 220.0, "height": 140.0},
        "color": node.color,
        "header_color": node.header_color,
        "is_system_prompt": bool(node.state.is_system_prompt),
        "is_summary_note": bool(node.state.is_summary_note),
        # Note provenance fields (role/source_ids/operation_id/
        # source_revisions/provider_snapshot) - dead even in legacy's own
        # persisted format (no SQL column for them ever existed; see
        # session_load.py's own docstring), so there is nothing to write
        # back regardless of whether this backend tracked them.
        #
        # ADR-002 Workstream 1 ("Branch status and lifecycle") - confirmed,
        # pre-existing gap fixed inline: is_branch_comparison/item_ids
        # (Compare Branches) already synced live to the frontend via
        # scene_payload() but were never written here, so a Save-then-Load
        # cycle silently dropped a comparison note's badge and its
        # source-branch references. Unlike the dead legacy fields above,
        # this is a real, currently-populated field this backend itself
        # introduced - genuinely missing, not deliberately excluded.
        "is_branch_comparison": bool(node.state.is_branch_comparison),
        "item_ids": list(node.item_ids),
    }


def _serialize_pin(record) -> dict[str, Any]:
    return {
        "pin_id": record.pin_id,
        "title": record.title,
        "note": record.note,
        "position": {"x": float(record.position[0]), "y": float(record.position[1])},
        "anchor_item_id": record.anchor_item_id,
        "sort_order": record.sort_order,
        "created_at": record.created_at,
    }


def _serialize_frame(node: SceneNode, frame_source_index: dict[str, int]) -> dict[str, Any]:
    item_indices = [frame_source_index[i] for i in node.item_ids if i in frame_source_index]
    # Technical-debt audit finding: group_width/group_height is the frame's
    # CURRENT effective size, and reading it unconditionally is CORRECT
    # while the frame is expanded - it already reflects the real union of
    # any manual override with the live members' bbox, which is exactly
    # what should be persisted. It is ONLY wrong while is_collapsed, when
    # backend/domain/groups.py's _recompute_group_bounds has temporarily
    # overwritten it with the fixed collapsed-pill size, discarding the
    # real size entirely. group_manual_width/height is the SEPARATE, never-
    # auto-populated field that survives a collapse/expand round-trip
    # untouched (FrameState's own docstring) - the real, stable source of
    # truth to fall back to ONLY in that one state. Saving while a
    # manually-resized frame happened to be collapsed used to write the
    # tiny pill size into "rect"/"size", which session_load.py's
    # _restore_frames then applies via resize_frame() as the frame's new
    # PERMANENT manual size - destroying the user's real size the moment
    # they next expand it. An auto-fit frame (no manual override) is
    # unaffected either way, since resize_frame's own bbox-minimum clamp
    # already recovers a sane auto-fit size from a stale pill value.
    if node.is_collapsed and node.state.group_manual_width is not None:
        width = node.state.group_manual_width
    else:
        width = node.state.group_width if node.state.group_width is not None else 0.0
    if node.is_collapsed and node.state.group_manual_height is not None:
        height = node.state.group_manual_height
    else:
        height = node.state.group_height if node.state.group_height is not None else 0.0
    return {
        "id": node.id,
        "items": item_indices,
        "item_ids": list(node.item_ids),
        "position": _position(node),
        "note": node.content,
        "size": {"width": width, "height": height},
        # R6.5 mirrors deserialize_frame's own reading exactly: "rect"
        # (x/y/width/height) is what actually drives frame._user_resized on
        # load, and session_load.py's own _restore_frames reads it (falling
        # back to "size" only for an older shape) - x/y here match the
        # frame's own live position since this backend has no separate
        # "local rect origin" concept distinct from the node's own x/y
        # (unlike legacy's QGraphicsItem coordinate model).
        "rect": {"x": node.x, "y": node.y, "width": width, "height": height},
        "expanded_rect": {"x": node.x, "y": node.y, "width": width, "height": height},
        "is_locked": bool(node.state.is_locked),
        "is_collapsed": bool(node.is_collapsed),
        "color": node.color,
        "header_color": node.header_color,
    }


def _serialize_container(node: SceneNode, all_items_index: dict[str, int]) -> dict[str, Any]:
    item_indices = [all_items_index[i] for i in node.item_ids if i in all_items_index]
    width = node.state.group_width if node.state.group_width is not None else 0.0
    height = node.state.group_height if node.state.group_height is not None else 0.0
    return {
        "id": node.id,
        "items": item_indices,
        "item_ids": list(node.item_ids),
        "position": _position(node),
        "title": node.content,
        "is_collapsed": bool(node.is_collapsed),
        "color": node.color,
        "header_color": node.header_color,
        "expanded_rect": {"x": node.x, "y": node.y, "width": width, "height": height},
        "rect": {"x": node.x, "y": node.y, "width": width, "height": height},
    }


def _serialize_chart(
    node: SceneNode, nodes_index: dict[str, int], parent_id: str | None,
) -> dict[str, Any]:
    parent_index = nodes_index.get(parent_id) if parent_id is not None else None
    return {
        "id": node.id,
        "data": dict(node.state.chart_data),
        "position": _position(node),
        "size": {"width": node.state.chart_width, "height": node.state.chart_height},
        "aspect_ratio_locked": bool(node.state.chart_aspect_locked),
        "parent_node_index": parent_index,
        "parent_node_id": parent_id,
        # chart_source_node_id is always derived from parent_id in this
        # backend (add_chart_node's own contract - see ChartState's own
        # docstring, backend/domain/node_states.py) - legacy's rarer
        # differs-from-parent case was already documented there as an
        # accepted simplification, so source_node_id is simply the same
        # id, never a genuinely distinct value.
        "source_node_id": parent_id,
        "data_error": node.state.chart_error or None,
    }


# -- edge classification -------------------------------------------------

def _basic_connection_entry(source_id: str, target_id: str, nodes_index: dict[str, int]) -> dict[str, Any]:
    return {
        "start_node_index": nodes_index.get(source_id),
        "end_node_index": nodes_index.get(target_id),
        "start_node_id": source_id,
        "end_node_id": target_id,
    }


def _classify_edges(
    document: SceneDocument,
    nodes_index: dict[str, int],
    kind_by_id: dict[str, str],
) -> dict[str, Any]:
    """The save-side mirror of session_load.py's own multi-step restore
    algorithm - see this module's own docstring for why a flat edges dict
    needs this at all. Returns a dict with keys: parent_by_child (node id ->
    parent node id, for every non-chat regular kind, chart parents handled
    SEPARATELY by the caller since charts aren't in `kind_by_id`),
    children_by_parent (node id -> ordered list of child node ids, for
    chat/conversation/html sources only), system_prompt_connections,
    group_summary_connections (each a list of (source_id, target_id) pairs),
    and connections (the catch-all list of (source_id, target_id) pairs for
    every edge not claimed by an earlier rule)."""
    parent_by_child: dict[str, str] = {}
    children_by_parent: dict[str, list[str]] = {}
    system_prompt_pairs: list[tuple[str, str]] = []
    group_summary_pairs: list[tuple[str, str]] = []
    connections: list[tuple[str, str]] = []

    consumed: set[str] = set()

    # Step A: note<->regular-node edges (system-prompt / group-summary),
    # resolved purely by direction + endpoint kind - this backend has no
    # separate "connection subtype" to consult, unlike legacy's own parallel
    # scene.system_prompt_connections/scene.group_summary_connections lists.
    for edge_id, edge in document.edges.items():
        source_kind = kind_by_id.get(edge.source)
        target_kind = kind_by_id.get(edge.target)
        if source_kind == "note" and target_kind in _REGULAR_KINDS:
            system_prompt_pairs.append((edge.source, edge.target))
            consumed.add(edge_id)
        elif source_kind in _REGULAR_KINDS and target_kind == "note":
            group_summary_pairs.append((edge.source, edge.target))
            consumed.add(edge_id)

    # Step B: each non-chat regular kind's ONE required structural parent,
    # PLUS a chart's own (optional) parent - the first (in document.edges
    # insertion/creation order) not-yet-consumed incoming edge. Charts are
    # included here (not just the 11 "regular" parent-requiring kinds)
    # because _serialize_chart reads its own parent from this SAME
    # parent_by_child map - a chart's parent edge is structurally identical
    # to any other kind's (add_chart_node's own self.connect(parent_id,
    # node_id) call), it just gets written to a dedicated
    # parent_node_index/parent_node_id pair on the chart's OWN payload
    # (chart is not part of the "nodes" list at all) rather than onto a
    # sibling "nodes" list entry. Any FURTHER incoming edge to the same
    # target (a user manually drew a second connection into an already-
    # parented node via the live connectNodes intent) falls through to the
    # catch-all below instead of overwriting the first - documented,
    # bounded simplification; this backend never creates more than one
    # incoming edge to these kinds through its own add_X_node methods, so
    # this only matters for a manually-drawn extra connection, an edge case
    # with no legacy analog to be faithful to in the first place.
    for edge_id, edge in document.edges.items():
        if edge_id in consumed:
            continue
        target_kind = kind_by_id.get(edge.target)
        if target_kind in _PARENT_CONTENT_INDEX_KINDS or target_kind in _PARENT_NODE_INDEX_KINDS or target_kind == "chart":
            if edge.target not in parent_by_child:
                parent_by_child[edge.target] = edge.source
                consumed.add(edge_id)

    # Step C: children_indices/ids - chat/conversation/html source AND
    # target (legacy's own children concept is scoped to the branch-tree of
    # chat-family nodes specifically, never "every node this one spawned" -
    # a chat's generated code/image/document node is ALREADY fully captured
    # via that child's own parent_content_node_index from Step B, and was
    # never additionally listed in the parent's children_indices in legacy
    # either).
    for edge_id, edge in document.edges.items():
        if edge_id in consumed:
            continue
        if kind_by_id.get(edge.source) in _CHILD_LINK_KINDS and kind_by_id.get(edge.target) in _CHILD_LINK_KINDS:
            children_by_parent.setdefault(edge.source, []).append(edge.target)
            consumed.add(edge_id)

    # Step D: everything else - the generic/basic "connections" catch-all,
    # covering user-drawn connectNodes edges between any two nodes that
    # steps A-C didn't already claim. Total coverage: no edge is ever
    # silently dropped.
    for edge_id, edge in document.edges.items():
        if edge_id in consumed:
            continue
        connections.append((edge.source, edge.target))

    return {
        "parent_by_child": parent_by_child,
        "children_by_parent": children_by_parent,
        "system_prompt_pairs": system_prompt_pairs,
        "group_summary_pairs": group_summary_pairs,
        "connections": connections,
    }


def build_chat_data(
    document: SceneDocument,
    asset_store: Any | None = None,
    plugin_registry: "PluginRegistry | None" = None,
    settings_manager: "SettingsManager | None" = None,
) -> dict[str, Any]:
    """ADR-009 stage 9.5 entry point. Publishes `asset_store` for the
    duration of this save so _serialize_image_node can externalize bytes
    instead of inlining base64, then delegates. Reset in a finally so a
    failed save cannot leave a stale store visible to the next one.

    ADR-014 stage 14.2: `plugin_registry` mirrors backend/plugins.py's own
    register_plugins(..., plugin_registry=None) precedent exactly - None
    (every real call site: backend/chat_library.py, backend/autosave.py)
    triggers a real discover_plugins() call, memoized by resolved path so
    repeat saves within one process pay no rescan cost; tests inject a
    specific registry for isolation, the same reason register_plugins
    itself takes this same parameter.

    ADR-014 review-fix: `settings_manager`, when given, is threaded down to
    _serialize_plugin_node so a plugin's own serialize hook is gated on its
    current Settings > Plugins grant, the same install-time consent check
    node creation already enforces - see that function's own docstring for
    the exact contract. `None` (this parameter's own default, and every
    call site in this project's test suite that predates this fix) means
    "no grant store available" and preserves the exact prior ungated
    behavior - real production callers (backend/chat_library.py's saveChat,
    backend/autosave.py's tick) pass a real one."""
    token = _ACTIVE_SAVE_ASSET_STORE.set(asset_store)
    try:
        return _build_chat_data(document, plugin_registry, settings_manager)
    finally:
        _ACTIVE_SAVE_ASSET_STORE.reset(token)


def _build_chat_data(
    document: SceneDocument,
    plugin_registry: "PluginRegistry | None" = None,
    settings_manager: "SettingsManager | None" = None,
) -> dict[str, Any]:
    """The top-level orchestrator - ports SceneSerializer.serialize_chat_
    data()'s own exact top-level shape. notes_data/pins_data are nested
    INSIDE this single returned dict (matching legacy's own
    serialize_chat_data exactly) - the caller (backend/chat_library.py's
    saveChat intent) pops them back out before writing, mirroring
    SaveWorkerThread.run()'s own `self.chat_data.get("notes_data", [])`
    pattern precisely."""
    if plugin_registry is None:
        plugin_registry = discover_plugins()
    # ADR-014 stage 14.2: a plugin-registered node kind (namespaced
    # "plugin_id.kind", so it can never collide with _REGULAR_KINDS) is
    # now ALSO a "regular" node for save purposes - included in the exact
    # same `all_nodes` list/index/parent-tracking machinery below rather
    # than a parallel code path, so every one of the invariants this
    # module's own docstring documents (nodes_index, the edge classifier,
    # children_indices, ...) already covers it too. Adding a NEW plugin
    # later needs zero changes here: this membership check is generic
    # against whatever discover_plugins() finds, not a per-plugin branch.
    plugin_kinds = plugin_registry.node_kinds
    all_nodes = [
        n for n in document.nodes.values()
        if n.kind in _REGULAR_KINDS or n.kind in plugin_kinds or _is_plugin_kind(n.kind)
    ]
    notes = [n for n in document.nodes.values() if n.kind == "note"]
    charts = [n for n in document.nodes.values() if n.kind == "chart"]
    frames = [n for n in document.nodes.values() if n.kind == "frame"]
    containers = [n for n in document.nodes.values() if n.kind == "container"]

    nodes_index = {n.id: i for i, n in enumerate(all_nodes)}
    notes_index = {n.id: i for i, n in enumerate(notes)}
    kind_by_id = {n.id: n.kind for n in document.nodes.values()}

    node_slot_count = len(all_nodes)
    note_slot_count = len(notes)
    chart_slot_count = len(charts)
    frame_slot_count = len(frames)

    # "frame source index" = all_nodes + charts, at offset node_slot_count -
    # mirrors deserialize_frame's own frame_source_map exactly (frames may
    # reference charts as members, never notes).
    frame_source_index = dict(nodes_index)
    for i, n in enumerate(charts):
        frame_source_index[n.id] = node_slot_count + i

    # "all items index" = nodes + notes + charts + frames + containers at
    # the tail - mirrors get_all_serializable_items exactly. The loader adds
    # restored container ids back at these same tail indices so nesting can
    # resolve on the way in as well.
    all_items_index = dict(nodes_index)
    for i, n in enumerate(notes):
        all_items_index[n.id] = node_slot_count + i
    for i, n in enumerate(charts):
        all_items_index[n.id] = node_slot_count + note_slot_count + i
    for i, n in enumerate(frames):
        all_items_index[n.id] = node_slot_count + note_slot_count + chart_slot_count + i
    for i, n in enumerate(containers):
        all_items_index[n.id] = node_slot_count + note_slot_count + chart_slot_count + frame_slot_count + i

    edges = _classify_edges(document, nodes_index, kind_by_id)
    parent_by_child = edges["parent_by_child"]
    children_by_parent = edges["children_by_parent"]

    node_payloads: list[dict[str, Any]] = []
    for node in all_nodes:
        serializer = _NODE_SERIALIZERS.get(node.kind)
        if serializer is not None:
            payload = serializer(node, document)
        else:
            # ADR-014 stage 14.2: not a built-in kind - the `all_nodes`
            # filter above already guarantees this is a currently-
            # recognized plugin kind, so the generic fallback below (never
            # a per-plugin branch) is what persists it.
            payload = _serialize_plugin_node(node, plugin_kinds.get(node.kind), settings_manager)
        payload["position"] = _position(node)
        payload["id"] = node.id

        parent_id = parent_by_child.get(node.id)
        if node.kind in _PARENT_CONTENT_INDEX_KINDS and parent_id is not None:
            payload["parent_content_node_index"] = nodes_index.get(parent_id)
        elif node.kind in _PARENT_NODE_INDEX_KINDS and parent_id is not None:
            payload["parent_node_index"] = nodes_index.get(parent_id)

        children = children_by_parent.get(node.id)
        if children:
            payload["children_indices"] = [nodes_index[c] for c in children if c in nodes_index]
            payload["children_ids"] = list(children)

        node_payloads.append(payload)

    chart_payloads = []
    for chart_node in charts:
        chart_parent_id = parent_by_child.get(chart_node.id)
        chart_payloads.append(_serialize_chart(chart_node, nodes_index, chart_parent_id))

    frame_payloads = [_serialize_frame(f, frame_source_index) for f in frames]
    container_payloads = [_serialize_container(c, all_items_index) for c in containers]

    basic_connections = [_basic_connection_entry(s, t, nodes_index) for s, t in edges["connections"]]

    system_prompt_connections = []
    for note_id, chat_id in edges["system_prompt_pairs"]:
        system_prompt_connections.append({
            "start_note_index": notes_index.get(note_id),
            "end_node_index": nodes_index.get(chat_id),
            "end_node_id": chat_id,
        })

    group_summary_connections = []
    for chat_id, note_id in edges["group_summary_pairs"]:
        group_summary_connections.append({
            "start_node_index": nodes_index.get(chat_id),
            "end_note_index": notes_index.get(note_id),
            "start_node_id": chat_id,
        })

    return {
        # Matches scene_index.py's own CURRENT_CHAT_SCHEMA_VERSION exactly
        # (confirmed directly, not assumed) - this format has not changed
        # shape in a way that would warrant bumping it.
        "schema_version": 1,
        "nodes": node_payloads,
        # ADR-009 stage 9.6: the AUTHORITATIVE edge list. Every edge in the
        # document, written flat, by persistent node id - exactly the shape
        # the document itself holds. session_load.py prefers this key and
        # ignores the legacy buckets entirely when it is present.
        #
        # The 12 legacy connection lists below are still written because
        # older builds (and the legacy app's own SQL reader) only know how
        # to read those; a file this build writes therefore still opens
        # everywhere it used to. That is the ONLY reason they survive - the
        # classification pass that fills them exists to reconstruct a
        # distinction this backend no longer makes, and reading it back is
        # now dead weight rather than the source of truth. Dropping the
        # write side is a separate, later decision with a real
        # compatibility cost; dropping the READ side, which is where the
        # lossiness actually bit, is done.
        "edges": [
            {"source": edge.source, "target": edge.target}
            for edge in document.edges.values()
        ],
        "system_prompt_connections": system_prompt_connections,
        "group_summary_connections": group_summary_connections,
        "frames": frame_payloads,
        "containers": container_payloads,
        "charts": chart_payloads,
        "total_session_tokens": document.total_session_tokens,
        "view_state": {
            "zoom_factor": document.zoom_factor,
            "scroll_position": {"x": document.scroll_x, "y": document.scroll_y},
        },
        # ADR-002 Workstream 1 ("Branch status and lifecycle"): a document-
        # level singular pointer, same "own top-level key, not per-node"
        # shape as total_session_tokens/view_state above - see
        # SceneDocument.final_deliverable_node_id's own comment for why
        # this is not a per-node field.
        "final_deliverable_node_id": document.final_deliverable_node_id,
        "notes_data": [_serialize_note(n) for n in notes],
        "pins_data": [_serialize_pin(r) for r in document.pins.records],
        # The 12 basic connection lists all draw from the SAME classified
        # "connections" bucket - this backend has no way to tell which of
        # the 12 legacy visual-line CLASSES a user-drawn connection would
        # have been, since they all collapsed onto one document.connect()
        # primitive years before this increment. Writing every catch-all
        # edge into the single original "connections" list (rather than
        # guessing a subtype, or fanning the same edge out into all 12)
        # is the only defensible choice: it's the one list every era of
        # both apps has always read unconditionally, and it round-trips
        # perfectly through this project's own session_load.py regardless.
        "connections": basic_connections,
        "content_connections": [],
        "document_connections": [],
        "image_connections": [],
        "thinking_connections": [],
        "conversation_connections": [],
        "html_connections": [],
        "pycoder_connections": [],
        "code_sandbox_connections": [],
        "web_connections": [],
        "artifact_connections": [],
        "gitlink_connections": [],
    }
