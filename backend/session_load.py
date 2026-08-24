"""Qt-free session LOAD (Qt-removal plan R6.4).

Reimplements graphlink_session/deserializers.py's SceneDeserializer.
restore_chat() algorithm against backend/canvas.py's SceneDocument - NOT an
import, matching this project's established "reimplement, don't import"
precedent (backend/chat_library.py, backend/composer.py, backend/plugins.py):
deserializers.py itself does `from PySide6.QtCore import QPointF, QRectF` at
module scope, and every `deserialize_*` method body constructs real
QGraphicsItem-family objects (scene.addItem, node.setPos,
node.prompt_input.setPlainText, ...) with no backend analog at all - unlike
graphlink_session/content_codec.py (already 100% Qt-free, loaded directly via
importlib in backend/canvas.py), there is no leaf module here worth loading
as-is. The file's actual VALUE is its ID/index-resolution ALGORITHM, ported
faithfully below; none of its executable Qt-widget code is reused.

Ground truth for every field/key name below was pulled directly from the
CURRENT (still on disk, still Qt-tainted, never imported) deserializers.py/
serializers.py for the 7 surviving node kinds, and from
`git show af72ffd~1:graphlink_app/graphlink_session/{de,}serializers.py` +
`graphlink_app/graphlink_web.py` (the commit immediately before R5-closeout
deleted the Web/Artifact/Gitlink/PyCoder/CodeSandbox branches) for the 5
plugin kinds - never from paraphrase or memory.

THE ALGORITHM (ported from deserializers.py's restore_chat):
1. Every node in the payload's node list is restored in a SINGLE pass, in
   payload order - NOT nodes-then-edges in two separate phases. This matters:
   every kind except "chat" REQUIRES an already-resolved parent to be
   restored at all.
     - chat: no parent, always restored, never connected to anything by this
       step (legacy passes parent_node=None unconditionally).
     - code/document/image/thinking: `parent_content_node_index` (a payload
       LIST POSITION, not persistent_id - no id-fallback exists for this
       specific reference), resolved against all_nodes_map AS BUILT SO FAR
       (i.e. only positions strictly earlier in the list can resolve, since
       later ones haven't been added yet - this exactly matches legacy's own
       single forward pass). If it doesn't resolve, legacy's own
       `if parent_node:` guard means the node is not created at all - ported
       verbatim: no id/edge is registered for that payload index.
     - conversation/html/pycoder/code_sandbox/web/artifact/gitlink:
       `parent_node_index`, same resolution/skip-if-missing rule, different
       key name (this split was verified directly against both the current
       file and the pre-deletion recovery, not assumed).
   Whenever a parent DOES resolve, the freshly-restored node is connected to
   it immediately (document.connect(parent_id, node_id)) - this is the exact
   structural edge legacy's own `scene.add_code_node(..., parent_node)`-style
   constructors create as a side effect of construction; there is no separate
   "basic connection" payload entry for it (those 7+5 connection lists below
   are visual/branch connections between UNRELATED nodes, never the
   node<->its own required parent link).
   Two lookup maps are built as this happens: `all_nodes_map` (payload list
   POSITION -> new backend node id) and `nodes_by_id` (payload persistent
   "id" -> new backend node id, for every kind, used later by connections/
   frames/containers that DO support id-preferred resolution). A third,
   `chat_nodes_map`, is keyed by an ordinal counted ONLY across "chat"-type
   payload entries (not their position in the full node list) - bug #47's own
   fix: system-prompt/group-summary connections reference chat nodes by this
   save-side ordinal specifically, so a skipped node elsewhere can never
   shift it.
2. _restore_children: children_indices/children_ids (id-preferred + index-
   fallback per position) are restored next, but only for kinds the CURRENT
   (post-R5-closeout) CHILD_LINK_NODE_TYPES actually still covers - see
   _CHILD_LINK_KINDS below. Some now-deleted kinds (pycoder/web/artifact/
   gitlink/code_sandbox) also once wrote children_indices, but there is no
   live concept left to restore it into, so they're correctly excluded.
3. Notes, then charts (each in its own try/except - one bad chart must never
   abort the whole load, matching deserializers.py's own per-chart catch),
   are restored next, each building a position -> new-id map of their own.
4. Frames are restored against a "frame source map" = all_nodes_map PLUS
   charts placed at offset node_slot_count+chart_index (frames may reference
   charts as members, never notes). Frame membership is PURE POSITIONAL
   (payload key "items", legacy fallback "nodes") - despite frames also
   carrying an "item_ids" list in the payload, deserialize_frame never reads
   it; only the index list is ever consulted.
5. Containers are restored against a "full item map" = all_nodes_map PLUS
   notes at node_slot_count+note_index, PLUS charts at
   node_slot_count+note_slot_count+chart_index, PLUS frames at
   node_slot_count+note_slot_count+chart_slot_count+frame_index. Also PURE
   POSITIONAL (payload key "items" - required in legacy, tolerated-missing
   here).
   CRITICAL: every one of these offsets is computed from the ORIGINAL
   PAYLOAD COUNTS (node_slot_count = len(node_payloads), etc.), never from
   how many nodes/notes/charts actually survived restoration - using
   survivor counts would shift every later slot whenever anything was
   skipped, silently misattributing frame/container membership. This is
   deserializers.py's own bug #47 fix, re-confirmed by direct reading - the
   single most important invariant in this file.
6. The basic connection lists (the 7 shared by every era:
   connections/content_connections/document_connections/image_connections/
   thinking_connections/conversation_connections/html_connections, PLUS 5
   that existed only before R5-closeout deleted their node kinds:
   pycoder_connections/code_sandbox_connections/web_connections/
   artifact_connections/gitlink_connections - restoring these 5 from an OLD
   save is still meaningful even though the node kinds they usually joined
   are gone, since a connection can link any two nodes) share IDENTICAL
   shape (start/end_node_index + start/end_node_id) and are all resolved the
   same way, against all_nodes_map/nodes_by_id only (never notes/charts/
   frames/containers) - one generic helper handles all 12.
7. system_prompt_connections (note -> chat node) and group_summary_connections
   (chat node -> note) resolve their NODE side id-preferred against the
   GENERAL nodes_by_id map (a node's payload id is unique across every kind)
   and index-fallback against chat_nodes_map specifically (verified directly
   against the call sites in restore_chat: both pass chat_nodes_map, not
   all_nodes_map, as the "nodes_map" positional argument) - the note side is
   always plain positional against notes_map.
8. Pins, view state, and total_session_tokens are restored last.

CONFIRMED, DOCUMENTED GAPS (fields with no backend/canvas.py destination at
all - silently dropped on load, never a crash):
- Note: size (width/height - Notes have no manual-size concept in this
  backend, sized purely from content like legacy itself),
  role/source_ids/operation_id/source_revisions/provider_snapshot (summary-
  note provenance - dead weight even in legacy's own persisted format, since
  no SQL column for these ever existed, so real saved chats never actually
  carry them).
- Web: include_branch_context, warnings, the free-text `status` string (no
  destination distinct from the enum-like `research_stage`).
- Artifact: `local_history` (a second, separate history list from
  conversation_history), `chat_html_cache` (a rendered-HTML cache, not
  source data).
- Code sandbox: the free-text `status` string (backend only has an
  awaiting_approval bool + an error string, a coarser model).
- Chart: `source_node_id` when it legitimately differs from parent_node_id -
  add_chart_node has no parameter for a distinct source id at all (always
  derives it from parent_id); already documented as an accepted
  simplification on chart_source_node_id's own field comment in canvas.py.
- Connections: per-connection waypoint/bend-point pins (legacy's
  serialize_connection alone, of the 12, carries a `pins` list - SceneEdge
  has no field for this at all). A chart's rarer chart-as-parent-of-another-
  chart id-reference (_resolve_chart_ref's own _charts_by_id fallback) - only
  index-based chart-parent resolution is ported here, matching every other
  reference's own "index is the common case, id is the safety net" posture,
  but for this one narrow id-shaped edge case falling back to charts_by_id
  was judged not worth a second parameter on the shared _resolve_ref helper.
- Pycoder/code_sandbox/web/artifact live in-flight-request markers: never
  restored - a loaded node always starts settled (not awaiting approval, not
  mid-run), matching that these are in-memory-only concepts even in legacy.

DELIBERATE RESILIENCE IMPROVEMENT OVER LEGACY (not a gap - a fix): legacy's
own restore_chat has exactly ONE outer try/except around the ENTIRE method
(bar the per-chart loop) - a single malformed node payload (a missing
required key, e.g. `data["code"]`) raises all the way out and aborts the
WHOLE chat load via _handle_load_error, discarding every other node that
would otherwise have restored fine. Every per-node/per-frame/per-container
restore step here is wrapped in its own try/except instead, so one corrupted
entry is skipped rather than sinking the entire session - matches this
increment's already-established "never let one bad item abort everything"
posture (charts already had this in legacy itself; this extends the same
posture everywhere else).

TRANSLATIONS APPLIED (not gaps - legacy and backend just use different
literal values for the same concept):
- node_type "web" -> kind "web_research".
- pycoder `mode` is the persisted enum MEMBER NAME (`node.mode.name`, e.g.
  "AI_DRIVEN"/"MANUAL", uppercase) -> backend `pycoder_mode` wants
  "ai_driven"/"manual" (lowercase).
- gitlink's flat `repo_state` dict (repo/branch/scope_mode/local_root/
  imported_root) -> the 5 discrete gitlink_repo/branch/scope_mode/
  local_root/imported_root fields, a direct 1:1 unpack.
- gitlink's `proposal_data["files"]` -> gitlink_pending_changes, direct copy.
  gitlink_proposal_markdown is DERIVED in legacy (a method on the deleted
  GitlinkNode class, not recoverable as a pure display string with no
  functional role - Apply/regenerate act on gitlink_pending_changes, never on
  the markdown) - synthesized here as a simple, honest list of changed file
  paths instead. Documented simplification, not a silent gap.
- gitlink's `_approved_fingerprint`/change_state are never persisted in
  legacy either (always reset to None / recomputed as
  previewed-if-pending-else-draft on restore there too) - matches backend's
  own complete_gitlink_run contract exactly, so this recomputes the same way
  rather than reading any (nonexistent) payload key.
- research_result: legacy persists `WebNode.research_result_payload`, a
  snake_case dict produced by `ResearchResult.to_dict()`-equivalent
  construction (confirmed directly:
  `graphlink_web.py`'s own `restore_research_result` reads
  request_id/original_query/effective_query/answer_markdown/sources/
  citations/warnings/provider_snapshot - all snake_case), while backend's own
  `research_result` field is CAMEL-CASE (`_research_result_wire`'s own output
  shape, canvas.py - requestId/originalQuery/sources[...]/etc). A generic,
  reversible snake_case -> camelCase key transform (_snake_to_camel_deep
  below) is applied on load.
"""

from __future__ import annotations

import contextvars
import logging
import math
import re
import uuid
from typing import Any

from backend.canvas import (
    ArtifactState,
    ChatState,
    CodeSandboxState,
    CodeState,
    DocumentState,
    GitlinkState,
    HarnessState,
    HtmlState,
    ImageState,
    PlanState,
    PycoderState,
    SceneDocument,
    SceneNode,
    SUPPORTED_CHART_TYPES,
    WebResearchState,
    _content_codec,
    _placeholder_chart_data,
)
from backend.plugin_sdk import NodeKindSpec, PluginRegistry, discover_plugins
from graphlink_chart_data import ChartDataError, canonicalize_chart_data
from graphlink_navigation_pins import NavigationPinRecord
from graphlink_settings_store import SettingsManager

logger = logging.getLogger(__name__)

# ADR-009 stage 9.5: the asset store in effect for the CURRENT restore.
#
# Threaded as a contextvar rather than as a parameter because the per-kind
# restorer dispatch table below is ~20 lambdas all sharing the signature
# (payload, document); widening every one of them to carry a store only
# _restore_image_payload reads would be churn for no gain. A contextvar,
# not a bare module global, so the value can never leak between concurrently
# restoring sessions - restore_chat_into_document sets it, and resets it in
# a finally, around a fully synchronous call (no awaits inside), so the
# window is atomic with respect to the event loop.
_ACTIVE_ASSET_STORE: contextvars.ContextVar = contextvars.ContextVar(
    "graphlink_active_asset_store", default=None
)


# -- small, generic helpers --------------------------------------------------

_SNAKE_RE = re.compile(r"_([a-z0-9])")


def _snake_to_camel(key: str) -> str:
    return _SNAKE_RE.sub(lambda m: m.group(1).upper(), key)


def _snake_to_camel_deep(value: Any) -> Any:
    """Recursively re-keys dicts from snake_case to camelCase. Used only for
    `research_result` - see this module's own docstring for why. A pure,
    side-effect-free mapping function, not a SceneDocument method (same
    posture as canvas.py's own _research_result_wire/_content_parts_wire
    helpers)."""
    if isinstance(value, dict):
        return {_snake_to_camel(str(k)): _snake_to_camel_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snake_to_camel_deep(item) for item in value]
    return value


def _flatten_multimodal_text(parts: list[dict[str, Any]]) -> str:
    """Derives the plain-text mirror every existing piece of code expects in
    SceneNode.content, from a decoded multimodal parts list - joins text-type
    parts, and stands in a plain placeholder for any non-text part, matching
    content_parts's own field comment on SceneNode ("a placeholder like
    '[Image]' for non-text parts")."""
    pieces: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            pieces.append(part["text"])
        elif part.get("type") == "image_bytes":
            pieces.append("[Image]")
    return "\n".join(pieces)


def _restore_history(raw_history: Any) -> list[dict[str, Any]]:
    """SceneNode.history is typed list[dict[str, str]] - a PLAIN-TEXT mirror,
    unlike content_parts (which has its own dedicated wire-side base64 re-
    encoder, _content_parts_wire in canvas.py). A legacy conversation_history
    entry's "content" can legitimately be a multimodal parts list too (an
    old pasted-image turn) - calling content_codec.deserialize_history on it
    would decode that into raw Python bytes, which then has nowhere safe to
    go: history has no equivalent wire re-encoder, so those bytes would hit
    json.dumps() directly and crash the ENTIRE scene publish (found via a
    live drive against a real ~/.graphlink/chats.db entry - "TypeError:
    Object of type bytes is not JSON serializable", not a hypothetical).
    Flattening multimodal entries to the same "[Image]"-placeholder text
    mirror _flatten_multimodal_text already uses for the node's own content
    field keeps this field's own str contract intact instead."""
    if not isinstance(raw_history, list):
        return []
    restored: list[dict[str, Any]] = []
    for message in raw_history:
        if not isinstance(message, dict):
            continue
        new_message = dict(message)
        content = new_message.get("content")
        if isinstance(content, list):
            # _flatten_multimodal_text only ever reads a part's "type"/"text"
            # keys, never "data" - flattening straight from the still-
            # base64-encoded parts (no need to decode_image_bytes first just
            # to immediately discard the bytes).
            new_message["content"] = _flatten_multimodal_text(content)
        restored.append(new_message)
    return restored


def _resolve_node_payload_list(chat_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Ports restore_chat's own 3-level fallback: current shape
    (`chat_data["nodes"]`), legacy shape (`chat_data["items"]`), or a
    double-nested legacy shape (`chat_data["data"]["nodes"/"items"]`) -
    tolerating every shape a real saved chat could actually be in, not just
    the current one. Always returns a list (empty if nothing resolves)."""
    for key in ("nodes", "items"):
        candidate = chat_data.get(key)
        if isinstance(candidate, list):
            return candidate
    nested = chat_data.get("data")
    if isinstance(nested, dict):
        for key in ("nodes", "items"):
            candidate = nested.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


def _resolve_ref(
    payload: dict[str, Any],
    id_key: str,
    index_key: str,
    nodes_by_id: dict[str, str],
    position_map: dict[int, str],
) -> str | None:
    """Ports _resolve_node_ref: ID-preferred, index-fallback. Both a missing
    id AND a missing/unresolved index legitimately return None (caller drops
    the reference silently, matching legacy's own tolerance for a
    partially-resolvable payload)."""
    ref_id = payload.get(id_key)
    # REVIEW-FIX: ref_id comes straight from untrusted payload JSON and can
    # legitimately be a list/dict (valid JSON, wrong shape) in a hand-edited
    # or legacy save file - `x in dict`/`dict[x]` raise TypeError for an
    # unhashable key, which would abort the ENTIRE chat load instead of just
    # dropping this one reference. nodes_by_id is itself always populated
    # with str(payload_id) keys (see the node-restore loop above), so
    # str()-casting the lookup here is both crash-safe (str() is always
    # hashable) and consistent with every other nodes_by_id lookup in this
    # module (e.g. final_deliverable_node_id's identical str(...) guard).
    if ref_id and str(ref_id) in nodes_by_id:
        return nodes_by_id[str(ref_id)]
    ref_index = payload.get(index_key)
    if isinstance(ref_index, int) and ref_index in position_map:
        return position_map[ref_index]
    return None


def _finite_float(value: Any, default: float = 0.0) -> float:
    """SECURITY-FIX: float() accepts the non-standard JSON literals NaN,
    Infinity and -Infinity (json.loads parses them by default), and a
    saved chat row or imported archive is hostile-data-on-disk. A
    non-finite coordinate/size/zoom restored into the live SceneDocument
    then rides scene_payload() into starlette's send_json (allow_nan=True),
    which emits literal `NaN`/`Infinity` tokens - invalid JSON the SPA's
    JSON.parse rejects, so it silently DROPS every scene frame from then
    on: the canvas freezes for the whole session (the "bricks the scene
    channel" DoS). Coercing any non-finite value to a safe default here
    lets the chat still load, just with the poisoned number replaced,
    instead of either crashing or wedging the wire."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _position(payload: dict[str, Any]) -> tuple[float, float]:
    position = payload.get("position")
    if isinstance(position, dict):
        return _finite_float(position.get("x", 0.0)), _finite_float(position.get("y", 0.0))
    return 0.0, 0.0


# -- per-kind node restoration ------------------------------------------------
# Each function takes ONE payload dict (already known to be `node_type`-
# appropriate) and returns a plain SceneNode with every field this backend
# can represent set - NO id (register_restored_node assigns a fresh one).
# Parent resolution/skip-if-missing/connect is handled uniformly by
# _restore_node below, NOT by these functions - clean separation between
# "build this node's own fields" and "does it have a valid parent".

def _restore_chat_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    raw_content = payload.get("raw_content", payload.get("text", ""))
    content_parts: list[dict[str, Any]] | None = None
    content_text = ""
    if isinstance(raw_content, list):
        content_parts = _content_codec.process_content_for_deserialization(raw_content)
        content_text = _flatten_multimodal_text(content_parts)
    else:
        content_text = str(raw_content or "")
    return SceneNode(
        id="", x=x, y=y, title="Chat", kind="chat",
        content=content_text,
        is_collapsed=bool(payload.get("is_collapsed", False)),
        history=_restore_history(payload.get("conversation_history")),
        # ADR-002 Workstream 1 ("Branch status and lifecycle") - confirmed,
        # pre-existing gap fixed inline: provider/model/is_branch_synthesis/
        # synthesis_instructions (Synthesize Branches) already synced live
        # to the frontend via scene_payload() but were silently dropped on
        # load (session_save.py's own docstring at the matching fix has the
        # full story). item_ids (also part of a synthesis node's real
        # shape) is deliberately NOT set here - it references OTHER nodes
        # by their original id, which isn't resolvable until every node has
        # a new id; see _restore_branch_provenance_item_ids's own second-
        # pass restoration below. branch_status is this same pass's own
        # new field; an unrecognized/future value downgrades to "active"
        # rather than crashing the load, the same defensive posture this
        # function already uses for every other field.
        state=ChatState(
            content_parts=content_parts,
            # Legacy's own restore-time default is True (data.get("is_user",
            # True)) - deliberately NOT False, unlike every other bool field
            # on this dataclass. Getting this backwards would silently
            # relabel every AI response in an old save as if the user had
            # typed it.
            is_user=bool(payload.get("is_user", True)),
            chat_scroll_value=_finite_float(payload.get("scroll_value", 0.0) or 0.0),
            provider=payload.get("provider"),
            model=payload.get("model"),
            is_branch_synthesis=bool(payload.get("is_branch_synthesis", False)),
            synthesis_instructions=str(payload.get("synthesis_instructions", "") or ""),
            branch_status=(
                payload.get("branch_status")
                if payload.get("branch_status") in SceneDocument.BRANCH_STATUS_VALUES
                else "active"
            ),
            # ADR-006 stage 6.4: absent in every pre-6.4 save -> False,
            # matching the dataclass default.
            response_incomplete=bool(payload.get("response_incomplete", False)),
            # ADR-006 stage 6.8: absent in every pre-6.8 save -> None,
            # matching the dataclass default ("not reported").
            prompt_tokens=payload.get("prompt_tokens"),
            completion_tokens=payload.get("completion_tokens"),
            # ADR-016 stage 16.2: absent in every pre-16.2 save -> None,
            # matching the dataclass default.
            estimated_cost_usd=payload.get("estimated_cost_usd"),
            # ADR-007 stage 7.4: absent in every pre-7.4 save -> [], matching
            # the dataclass default. Each item is validated only loosely
            # (dict(...) below tolerates hand-edited/legacy entries missing a
            # key - scene_payload()'s own .get()-based wire projection is
            # what actually protects the frontend from a malformed one).
            tool_invocations=[
                dict(call) for call in (payload.get("tool_invocations") or []) if isinstance(call, dict)
            ],
            # ADR-018 stage 18.3: absent in every pre-18.3 save -> "",
            # matching the dataclass default ("no pin").
            override_provider=str(payload.get("override_provider", "") or ""),
            override_model_id=str(payload.get("override_model_id", "") or ""),
        ),
    )


def _restore_code_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title="Code", kind="code",
        state=CodeState(
            code=str(payload.get("code", "")),
            language=str(payload.get("language", "")),
        ),
    )


def _restore_document_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title=str(payload.get("title", "") or "Document"), kind="document",
        content=str(payload.get("content", "")),
        state=DocumentState(
            attachment_kind=str(payload.get("attachment_kind", "document")),
            file_path=str(payload.get("file_path", "")),
            mime_type=str(payload.get("mime_type", "")),
            duration_seconds=payload.get("duration_seconds"),
            byte_size=payload.get("byte_size"),
            preview_label=str(payload.get("preview_label", "") or ""),
        ),
        is_collapsed=bool(payload.get("is_collapsed", False)),
        is_docked=bool(payload.get("is_docked", False)),
    )


def _restore_image_payload(payload: dict[str, Any], document: SceneDocument) -> SceneNode:
    """Unlike every other kind, an image node's bytes live in
    document.image_assets (R3.21's existing pattern), addressed by an opaque
    image_asset_id - not a field the payload has a 1:1 name for (legacy
    stores raw base64 `image_bytes` directly on the node payload). Decode
    once here and mint a fresh asset id, mirroring add_image_node's own
    asset-registration shape."""
    import uuid as _uuid

    x, y = _position(payload)
    node = SceneNode(id="", x=x, y=y, title="Image", kind="image", state=ImageState())
    asset_store = _ACTIVE_ASSET_STORE.get()

    # ADR-009 stage 9.5: READ BOTH SHAPES. A chat saved with an asset store
    # in play carries only `asset_ref`; every chat saved before that (and
    # any saved without a store) still carries inline base64 `image_bytes`.
    # Both are read here so no stored row ever has to be rewritten - which
    # is what lets the externalization roll out without a destructive
    # migration over real user data.
    image_bytes = b""
    mime_type = "image/png"
    asset_ref = payload.get("asset_ref")
    if asset_store is not None and isinstance(asset_ref, str) and asset_ref:
        stored = asset_store.get(asset_ref)
        if stored is not None:
            image_bytes = stored
            mime_type = str(payload.get("mime_type") or "image/png")
        else:
            # A ref the store has never seen degrades to "this image does not
            # render", never to "this chat will not load" - the conversation is
            # worth far more than one of its pictures. But the ref itself is
            # REMEMBERED (see ImageState.unresolved_asset_ref): the read may
            # have failed transiently while the asset file is still perfectly
            # present, and dropping the ref here is what used to let the very
            # next save overwrite it with an empty inline payload and lose the
            # picture for good.
            node.state.unresolved_asset_ref = asset_ref
            node.state.unresolved_asset_mime_type = str(payload.get("mime_type") or "image/png")

    if not image_bytes:
        raw_b64 = payload.get("image_bytes")
        if isinstance(raw_b64, str) and raw_b64:
            try:
                image_bytes = _content_codec.decode_image_bytes(raw_b64)
            except Exception:
                image_bytes = b""

    if image_bytes:
        asset_id = f"img{_uuid.uuid4().hex}"
        document.image_assets[asset_id] = (image_bytes, mime_type)
        node.state.image_asset_id = asset_id
    node.content = str(payload.get("prompt", ""))
    return node


def _restore_thinking_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title="Thinking", kind="thinking",
        content=str(payload.get("thinking_text", "")),
        is_docked=bool(payload.get("is_docked", False)),
    )


def _restore_conversation_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title="Conversation", kind="conversation",
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


def _restore_html_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title="HTML", kind="html",
        content=str(payload.get("html_content", "")),
        state=HtmlState(html_splitter_state=payload.get("splitter_state")),
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


def _restore_web_payload(payload: dict[str, Any]) -> SceneNode:
    # R6.4 translation: legacy node_type "web" -> backend kind
    # "web_research" (confirmed distinct strings, not a typo).
    x, y = _position(payload)
    node = SceneNode(
        id="", x=x, y=y, title="Web Research", kind="web_research",
        content=str(payload.get("query", "")),
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
        state=WebResearchState(
            # ADR-021 stage 21.5: absent in every pre-21.5 row, which is
            # exactly the False default Web Research has always behaved as.
            research_retain_to_knowledge=bool(payload.get("retain_to_knowledge", False)),
        ),
    )
    research_result = payload.get("research_result")
    if isinstance(research_result, dict) and research_result:
        node.state.research_result = _snake_to_camel_deep(research_result)
    else:
        summary = payload.get("summary")
        sources = payload.get("sources")
        if isinstance(summary, str) and summary:
            # Mirrors WebNode.restore_research_result's own else-branch
            # (`elif summary: node.set_result(summary, sources)`) - an
            # older-shape session predating the research_result field
            # entirely. Synthesized as a minimal, best-effort
            # ResearchResult-shaped dict from the flat summary/sources pair
            # that IS present.
            node.state.research_result = _snake_to_camel_deep({
                "request_id": "legacy",
                "original_query": payload.get("query", ""),
                "effective_query": payload.get("query", ""),
                "answer_markdown": summary,
                "sources": sources if isinstance(sources, list) else [],
                "citations": [],
                "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
                "provider_snapshot": {},
            })
    return node


def _restore_artifact_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title="Artifact", kind="artifact",
        # R6.4 judgment call: legacy's "instruction" field has no dedicated
        # backend destination distinct from the generic `content` field,
        # which every other kind already reuses for "the node's primary
        # editable text" - reused the same way here, not left to drop.
        content=str(payload.get("instruction", "")),
        state=ArtifactState(artifact_content=str(payload.get("content", ""))),
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


def _build_simple_proposal_markdown(pending_changes: list) -> str:
    if not pending_changes:
        return ""
    lines = ["**Proposed changes:**", ""]
    for change in pending_changes:
        if isinstance(change, dict):
            path = change.get("path") or change.get("file_path") or "unknown file"
        else:
            path = str(change)
        lines.append(f"- `{path}`")
    return "\n".join(lines)


def _restore_gitlink_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    repo_state = payload.get("repo_state")
    repo_state = repo_state if isinstance(repo_state, dict) else {}
    proposal_data = payload.get("proposal_data")
    proposal_data = proposal_data if isinstance(proposal_data, dict) else {}
    pending_changes = proposal_data.get("files")
    pending_changes = pending_changes if isinstance(pending_changes, list) else []
    context_stats = payload.get("context_stats")
    context_stats = context_stats if isinstance(context_stats, dict) else {}

    return SceneNode(
        id="", x=x, y=y, title="Gitlink", kind="gitlink",
        state=GitlinkState(
            gitlink_task_prompt=str(payload.get("task_prompt", "")),
            gitlink_repo=str(repo_state.get("repo", "")),
            gitlink_branch=str(repo_state.get("branch", "")),
            gitlink_scope_mode=str(repo_state.get("scope_mode", "selected")),
            gitlink_local_root=str(repo_state.get("local_root", "")),
            gitlink_imported_root=str(repo_state.get("imported_root", "")),
            gitlink_repo_file_paths=list(payload.get("repo_file_paths") or []),
            gitlink_selected_paths=list(payload.get("selected_paths") or []),
            gitlink_context_xml=str(payload.get("context_xml", "")),
            gitlink_context_stats={str(k): str(v) for k, v in context_stats.items()},
            gitlink_pending_changes=pending_changes,
            # Derived, not persisted - see this module's own docstring for
            # why the exact legacy rendering can't be recovered, and why a
            # simple honest summary is the documented substitute.
            gitlink_proposal_markdown=_build_simple_proposal_markdown(pending_changes),
            gitlink_preview_text=str(payload.get("preview_text", "")),
            # Never persisted in legacy either - always reset on restore
            # there too, matching backend's own complete_gitlink_run
            # contract.
            gitlink_change_fingerprint=None,
            gitlink_change_local_root=None,
            gitlink_change_state="previewed" if pending_changes else "draft",
        ),
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


def _restore_pycoder_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    raw_mode = str(payload.get("mode", "AI_DRIVEN") or "AI_DRIVEN")
    return SceneNode(
        id="", x=x, y=y, title="Py-Coder", kind="pycoder",
        state=PycoderState(
            # R6.4 translation: legacy persists the enum MEMBER NAME
            # ("AI_DRIVEN"/"MANUAL", uppercase, via node.mode.name); backend
            # wants "ai_driven"/"manual" (lowercase).
            pycoder_mode=raw_mode.lower(),
            pycoder_prompt=str(payload.get("prompt", "")),
            pycoder_code=str(payload.get("code", "")),
            pycoder_output=str(payload.get("output", "")),
            pycoder_analysis=str(payload.get("analysis", "")),
            # ADR-005 stage 5.3 (review-fix): self-healing, not a blank
            # fallback - a payload missing this field (predates this fix,
            # or is otherwise malformed) mints a FRESH stable id here
            # rather than defaulting to "", which would route every such
            # node's REPL scratch dir to the same shared "default" bucket
            # (see graphlink_scratch_dirs.remove_scratch_dir_for_id's own
            # docstring for why that is actively dangerous, not just an
            # untidy fallback, once node-delete GC can rmtree it).
            pycoder_repl_id=str(payload.get("pycoder_repl_id") or uuid.uuid4().hex[:12]),
        ),
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


def _restore_code_sandbox_payload(payload: dict[str, Any]) -> SceneNode:
    x, y = _position(payload)
    return SceneNode(
        id="", x=x, y=y, title="Virtual Environment Runner", kind="code_sandbox",
        state=CodeSandboxState(
            code_sandbox_requirements=str(payload.get("requirements", "")),
            code_sandbox_prompt=str(payload.get("prompt", "")),
            code_sandbox_code=str(payload.get("code", "")),
            code_sandbox_output=str(payload.get("output", "")),
            code_sandbox_analysis=str(payload.get("analysis", "")),
            # ADR-005 stage 5.3 (review-fix): self-healing, same reasoning
            # as pycoder_repl_id above - a blank/missing sandbox_id used to
            # fall back to "" (routing to the shared "default" bucket);
            # minting a fresh id here instead means two nodes can no
            # longer collide on load, even from a malformed payload.
            code_sandbox_sandbox_id=str(payload.get("sandbox_id") or uuid.uuid4().hex[:12]),
        ),
        history=_restore_history(payload.get("conversation_history")),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


_BUILDER_TERMINAL_STATUSES = ("done", "failed", "stopped", "interrupted")


def _restore_plan_payload(payload: dict[str, Any]) -> SceneNode:
    """ADR-008 stage 8.3. The one load-time normalization: a NON-terminal
    builder_status ("running"/"awaiting_approval"/...) describes a
    RunHandle that cannot survive a restart - restoring it verbatim would
    render a spinner/approval panel no run backs. It lands as
    "interrupted": terminal, honest, and resumable (the plan node is the
    resume point - PlanState's own docstring)."""
    x, y = _position(payload)
    goal = str(payload.get("goal", ""))
    raw_status = str(payload.get("builder_status", "draft") or "draft")
    status = raw_status if raw_status in _BUILDER_TERMINAL_STATUSES + ("draft",) else "interrupted"
    steps = []
    for raw in payload.get("steps") or []:
        if isinstance(raw, dict) and raw.get("title"):
            steps.append({
                "id": str(raw.get("id", f"s{len(steps) + 1}")),
                "title": str(raw.get("title", "")),
                # A step caught mid-flight ("running") is normalized the
                # same way the whole build is - it did not finish.
                "status": (
                    str(raw.get("status", "pending"))
                    if str(raw.get("status", "pending")) != "running" else "failed"
                ),
                "detail": str(raw.get("detail", "")),
            })
    mode = str(payload.get("builder_mode", "copilot") or "copilot")
    activity = []
    for raw in payload.get("activity") or []:
        if isinstance(raw, dict) and raw.get("tool"):
            # review-fix: elapsedMs is untrusted input from a saved file
            # (hand-edited, or written by an older/different format) - a
            # non-numeric value must degrade to 0, the same tolerance every
            # other field on this row already gets via str(), not crash the
            # whole session load the way a bare int() would.
            try:
                elapsed_ms = int(raw.get("elapsedMs", 0) or 0)
            except (TypeError, ValueError):
                elapsed_ms = 0
            activity.append({
                "tool": str(raw.get("tool", "")),
                "summary": str(raw.get("summary", "")),
                "outcome": str(raw.get("outcome", "ok")),
                "stepId": str(raw.get("stepId", "")),
                "elapsedMs": elapsed_ms,
            })
    return SceneNode(
        id="", x=x, y=y,
        title=f"Build: {goal[:40]}" if goal else "Build",
        kind="plan",
        content=goal,
        state=PlanState(
            plan_goal=goal,
            plan_steps=steps,
            builder_activity=activity,
            builder_status=status,
            builder_mode=mode if mode in ("copilot", "autopilot") else "copilot",
            builder_run_id=str(payload.get("builder_run_id", "")),
            builder_max_steps=int(payload.get("max_steps", 12) or 12),
            builder_max_tokens=int(payload.get("max_tokens", 150_000) or 150_000),
            builder_max_wall_seconds=int(payload.get("max_wall_seconds", 900) or 900),
            builder_spent_steps=int(payload.get("spent_steps", 0) or 0),
            builder_spent_tokens=int(payload.get("spent_tokens", 0) or 0),
            builder_spent_wall_seconds=int(payload.get("spent_wall_seconds", 0) or 0),
            builder_status_detail=(
                str(payload.get("status_detail", ""))
                if raw_status == status
                else "Interrupted by an app restart - resume to continue from the plan."
            ),
        ),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


_HARNESS_TERMINAL_STATUSES = ("done", "failed", "stopped", "interrupted")


def _restore_harness_payload(payload: dict[str, Any]) -> SceneNode:
    """PLAN-2026-08-24 H1. Same load-time normalization as the plan node: a
    non-terminal harness_status describes a RunHandle that cannot survive a
    restart, so it lands as "interrupted" (a follow-up message resumes
    against the workspace transcript). A missing/blank workspace_id is
    self-healed with a fresh mint - the pycoder_repl_id precedent at line
    ~700 above, closing the shared-"default"-bucket collision the scratch
    GC refuses to delete through."""
    x, y = _position(payload)
    goal = str(payload.get("goal", ""))
    raw_status = str(payload.get("harness_status", "idle") or "idle")
    status = raw_status if raw_status in _HARNESS_TERMINAL_STATUSES + ("idle",) else "interrupted"
    activity = []
    for raw in payload.get("activity") or []:
        if isinstance(raw, dict) and raw.get("tool"):
            try:
                elapsed_ms = int(raw.get("elapsedMs", 0) or 0)
            except (TypeError, ValueError):
                elapsed_ms = 0
            activity.append({
                "tool": str(raw.get("tool", "")),
                "summary": str(raw.get("summary", "")),
                "outcome": str(raw.get("outcome", "ok")),
                "elapsedMs": elapsed_ms,
            })

    def _int(key: str, default: int) -> int:
        try:
            return int(payload.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    return SceneNode(
        id="", x=x, y=y,
        title=f"Agent: {goal[:40]}" if goal else "Agent",
        kind="harness",
        content=goal,
        state=HarnessState(
            harness_goal=goal,
            harness_reply=str(payload.get("reply", "")),
            harness_status=status,
            harness_status_detail=(
                str(payload.get("status_detail", ""))
                if raw_status == status
                else "Interrupted by an app restart - send a follow-up to continue."
            ),
            harness_run_id=str(payload.get("harness_run_id", "")),
            harness_workspace_id=str(payload.get("workspace_id") or uuid.uuid4().hex[:12]),
            harness_activity=activity,
            harness_max_turns=_int("max_turns", 16),
            harness_spent_turns=_int("spent_turns", 0),
            harness_spent_tokens=_int("spent_tokens", 0),
        ),
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


def _restore_plugin_payload(
    payload: dict[str, Any],
    kind_spec: "NodeKindSpec",
    settings_manager: "SettingsManager | None" = None,
) -> SceneNode:
    """ADR-014 stage 14.2: the load-side mirror of session_save.py's
    _serialize_plugin_node - restores the universal title/content/
    is_collapsed fields that generic fallback always wrote, unconditionally
    (every plugin-kind payload this project's own save path ever produced
    carries them, with no opt-in required), then - only when THIS SAME
    kind is still registered with a `deserialize` hook today - reconstructs
    the plugin's own NodeState subclass from the isolated 'plugin_state'
    dict serialize(node) most recently produced.

    A plugin that dropped its serialize/deserialize opt-in since this node
    was saved (or never had one) degrades to a plain title/content node
    rather than losing it outright - matches every other per-kind
    restorer's own "a missing/unusable piece of state is not fatal" posture
    (e.g. _restore_gitlink_payload's repo_state/proposal_data isinstance
    guards). The caller (_restore_node) is the one that decides whether to
    call this at all - it requires `kind_spec` (i.e. the kind is currently
    registered in the discovered PluginRegistry) BEFORE reaching here; an
    unregistered plugin kind is treated exactly like any other unrecognized
    node_type (skipped, not resurrected from a bare payload string with no
    validation).

    ADR-014 review-fix: `settings_manager`, when given, gates the
    kind_spec.deserialize(plugin_state) call on settings_manager.
    get_plugin_grants() - the load-side mirror of session_save.py's
    _serialize_plugin_node's own new grant check, see that function's
    docstring for the full contract. `None` (this parameter's own default)
    preserves the exact prior ungated behavior. A denied/ungranted node
    still restores with its universal title/content fields; it just comes
    back without its custom state for this one load, the same degrade this
    function already applies when deserialize itself raises or the kind
    dropped its opt-in entirely."""
    x, y = _position(payload)
    kind = str(payload.get("node_type", "") or "")
    title = str(payload.get("title", "") or kind)
    content = str(payload.get("content", "") or "")
    state = None
    granted = (
        settings_manager is None
        or settings_manager.get_plugin_grants().get(kind_spec.plugin_id, False)
    )
    if granted and kind_spec.deserialize is not None:
        plugin_state = payload.get("plugin_state")
        if isinstance(plugin_state, dict):
            try:
                state = kind_spec.deserialize(plugin_state)
            except Exception:
                state = None
    return SceneNode(
        id="", x=x, y=y, title=title, kind=kind, content=content, state=state,
        is_collapsed=bool(payload.get("is_collapsed", False)),
    )


_NODE_RESTORERS = {
    "chat": lambda payload, document: _restore_chat_payload(payload),
    "code": lambda payload, document: _restore_code_payload(payload),
    "document": lambda payload, document: _restore_document_payload(payload),
    "image": lambda payload, document: _restore_image_payload(payload, document),
    "thinking": lambda payload, document: _restore_thinking_payload(payload),
    "conversation": lambda payload, document: _restore_conversation_payload(payload),
    "html": lambda payload, document: _restore_html_payload(payload),
    "web": lambda payload, document: _restore_web_payload(payload),
    "artifact": lambda payload, document: _restore_artifact_payload(payload),
    "gitlink": lambda payload, document: _restore_gitlink_payload(payload),
    "pycoder": lambda payload, document: _restore_pycoder_payload(payload),
    "code_sandbox": lambda payload, document: _restore_code_sandbox_payload(payload),
    "plan": lambda payload, document: _restore_plan_payload(payload),
    "harness": lambda payload, document: _restore_harness_payload(payload),
}

# Verified directly against both serializers.py (which field each isinstance
# branch writes) and deserializers.py (which field each elif branch reads):
# "chat" needs no parent at all (absent from this map); every other kind
# requires ONE of these two positional (index-only, no id-fallback) parent
# references, and is skipped entirely - no node created - when it fails to
# resolve, exactly matching legacy's own `if parent_node:` guard.
_PARENT_CONTENT_INDEX_KINDS = {"code", "document", "image", "thinking"}
_PARENT_NODE_INDEX_KINDS = {
    "conversation", "html", "pycoder", "code_sandbox", "web", "artifact", "gitlink",
}

# Kinds whose live children_indices/children_ids relationship the CURRENT
# backend model actually tracks anything for - mirrors
# graphlink_session/scene_index.py's own CHILD_LINK_NODE_TYPES (currently
# (ChatNode, ConversationNode, HtmlViewNode) post-R5-closeout, confirmed by
# direct reading). Some now-deleted kinds (pycoder/web/artifact/gitlink/
# code_sandbox) once wrote children_indices too, but there is no live class
# left to restore that relationship into, so they are correctly excluded
# here rather than silently mishandled.
_CHILD_LINK_KINDS = {"chat", "conversation", "html"}


def _restore_node(
    document: SceneDocument,
    node_type: str,
    payload: dict[str, Any],
    all_nodes_map: dict[int, str],
    plugin_registry: "PluginRegistry | None" = None,
    settings_manager: "SettingsManager | None" = None,
) -> tuple[SceneNode | None, str | None]:
    """Ports deserialize_node's own dispatch: default to "chat" for an
    untagged payload (old-old sessions predating the node_type tag), and
    silently skip - never raise - for anything unrecognized OR whose
    required parent doesn't resolve, exactly matching legacy's own graceful
    fall-through / `if parent_node:` guard. Returns (node_or_None,
    resolved_parent_new_id_or_None) - the caller connects the edge itself
    once the node is registered, since register_restored_node needs to run
    first to mint the child's own id.

    ADR-014 stage 14.2: a node_type not in _NODE_RESTORERS at all is no
    longer AUTOMATICALLY unrecognized - it now ALSO falls back to
    `plugin_registry.node_kinds`, generic against whatever discover_
    plugins() found (never a per-plugin branch here). This path returns
    parent_new_id=None unconditionally (a plugin kind is never in
    _PARENT_CONTENT_INDEX_KINDS/_PARENT_NODE_INDEX_KINDS - it has no legacy
    index-based parent field to read at all) - its parent edge is restored
    separately, generically, by _restore_flat_edges below (session_save.py
    ALWAYS writes the authoritative flat `edges` list for any file this
    project's own build produces, so this is not a gap for a plugin kind,
    which only this project's own build could ever have written in the
    first place)."""
    restorer = _NODE_RESTORERS.get(node_type or "chat")
    if restorer is None:
        if plugin_registry is not None:
            kind_spec = plugin_registry.node_kinds.get(node_type)
            if kind_spec is not None:
                try:
                    plugin_node = _restore_plugin_payload(payload, kind_spec, settings_manager)
                except Exception:
                    logger.exception(
                        "session load: plugin node of kind %r failed to restore - skipping it", node_type,
                    )
                    return None, None
                return document.register_restored_node(plugin_node), None
        # A saved node whose kind nothing currently registers. It is left out
        # of the restored document (rendering an unknown kind is a frontend
        # decision this layer cannot make), but it is NOT silently forgotten:
        # the row on disk still holds it - session_save._is_plugin_kind keeps
        # writing any plugin-namespaced node back out - so fixing or
        # reinstalling the plugin and reloading brings it back. Logged
        # because, before this, a plugin that failed discovery made its
        # nodes disappear from the canvas with zero signal anywhere.
        if node_type:
            logger.warning(
                "session load: no restorer registered for node kind %r - the node stays in the "
                "saved row but is not on this canvas (a plugin that failed to load?)", node_type,
            )
        return None, None

    parent_new_id: str | None = None
    if node_type in _PARENT_CONTENT_INDEX_KINDS or node_type in _PARENT_NODE_INDEX_KINDS:
        parent_key = "parent_content_node_index" if node_type in _PARENT_CONTENT_INDEX_KINDS else "parent_node_index"
        parent_index = payload.get(parent_key)
        parent_new_id = all_nodes_map.get(parent_index) if isinstance(parent_index, int) else None
        if parent_new_id is None:
            return None, None

    try:
        node = restorer(payload, document)
    except Exception:
        return None, None
    return document.register_restored_node(node), parent_new_id


def _restore_children(
    node_payloads: list[dict[str, Any]],
    all_nodes_map: dict[int, str],
    nodes_by_id: dict[str, str],
    kind_by_new_id: dict[str, str],
    document: SceneDocument,
) -> None:
    """Ports deserializers.py's _restore_children: only meaningful for kinds
    in _CHILD_LINK_KINDS. children_indices/children_ids are parallel lists
    (same position in both = the same original child) - id-preferred,
    index-fallback per position. The CURRENT backend model has no explicit
    "children" field on SceneNode at all (branch structure lives entirely in
    edges) - so a restored child relationship here becomes a real
    document.connect(parent, child) edge, the same structural relationship
    the "connections" lists also establish elsewhere; this is deliberately
    idempotent (document.connect is itself idempotent for a duplicate pair),
    so restoring the same edge from two different legacy sources is
    harmless, not a double-edge bug."""
    for index, payload in enumerate(node_payloads):
        new_id = all_nodes_map.get(index)
        if new_id is None or kind_by_new_id.get(new_id) not in _CHILD_LINK_KINDS:
            continue
        child_ids = payload.get("children_ids")
        child_ids = child_ids if isinstance(child_ids, list) else []
        child_indices = payload.get("children_indices")
        child_indices = child_indices if isinstance(child_indices, list) else []
        if not child_ids and not child_indices:
            continue
        for position in range(max(len(child_ids), len(child_indices))):
            child_new_id = None
            # REVIEW-FIX: child_ids[position]/child_indices[position] come
            # straight from untrusted payload JSON and can legitimately be a
            # list/dict (valid JSON, wrong shape) instead of the expected
            # scalar id/index - `x in dict`/`dict.get(x)` raise TypeError for
            # an unhashable x, which would abort the entire chat load instead
            # of skipping just this one child. nodes_by_id is always keyed by
            # str(payload_id) (see _resolve_ref's identical str(...) guard),
            # so str()-cast the id lookup; all_nodes_map is int-keyed, so
            # isinstance-guard the index lookup the same way _resolve_ref
            # guards its own index fallback.
            if position < len(child_ids) and str(child_ids[position]) in nodes_by_id:
                child_new_id = nodes_by_id[str(child_ids[position])]
            elif position < len(child_indices) and isinstance(child_indices[position], int):
                child_new_id = all_nodes_map.get(child_indices[position])
            if child_new_id is not None:
                try:
                    document.connect(new_id, child_new_id)
                except Exception:
                    continue


def _restore_notes(document: SceneDocument, notes_data: list) -> dict[int, str]:
    notes_map: dict[int, str] = {}
    if not isinstance(notes_data, list):
        return notes_map
    for index, note_payload in enumerate(notes_data):
        if not isinstance(note_payload, dict):
            continue
        try:
            x, y = _position(note_payload)
            note = document.add_note(
                x, y,
                is_system_prompt=bool(note_payload.get("is_system_prompt", False)),
                is_summary_note=bool(note_payload.get("is_summary_note", False)),
            )
            document.set_note_content(note.id, str(note_payload.get("content", "")))
            document.set_group_color(note.id, note_payload.get("color"), note_payload.get("header_color"))
            # ADR-002 Workstream 1 ("Branch status and lifecycle") -
            # confirmed, pre-existing gap fixed inline: is_branch_comparison
            # (Compare Branches) already synced live to the frontend via
            # scene_payload() but was silently dropped on load. A plain
            # scalar poke, not a document method call, matching this
            # function's own established posture for fields that don't
            # need a dedicated setter (_restore_web_payload/_restore_image_
            # payload already assign SceneNode fields directly the same
            # way). item_ids is deliberately NOT set here - same
            # not-yet-resolvable-reference reasoning as the chat-kind case;
            # see _restore_branch_provenance_item_ids below.
            note.state.is_branch_comparison = bool(note_payload.get("is_branch_comparison", False))
        except Exception:
            continue
        notes_map[index] = note.id
    return notes_map


def _restore_charts(
    document: SceneDocument, charts_data: list, all_nodes_map: dict[int, str], nodes_by_id: dict[str, str],
) -> tuple[dict[int, str], dict[str, str]]:
    """Each chart restored in its own try/except - one bad chart must never
    abort the whole load (deserializers.py's own posture, ported exactly)."""
    charts_map: dict[int, str] = {}
    charts_by_id: dict[str, str] = {}
    if not isinstance(charts_data, list):
        return charts_map, charts_by_id
    for index, chart_payload in enumerate(charts_data):
        if not isinstance(chart_payload, dict):
            continue
        try:
            raw_data = chart_payload.get("data")
            raw_data = raw_data if isinstance(raw_data, dict) else {}
            chart_type = str(raw_data.get("type", "") or "").strip().lower()
            if chart_type not in SUPPORTED_CHART_TYPES:
                continue
            try:
                # Mirrors generate_chart's own _on_success contract exactly:
                # persisted chart data is NOT trusted to already be
                # canonical (an old save could predate a
                # canonicalize_chart_data shape change), so this runs it
                # through the same canonicalizer a live generation would,
                # falling back to the same placeholder-with-chart_error
                # shape on failure rather than dropping the chart.
                chart_data = canonicalize_chart_data(raw_data, chart_type)
                chart_error = ""
            except ChartDataError as exc:
                chart_data = _placeholder_chart_data(chart_type)
                chart_error = f"This chart's saved data could not be validated: {exc}"
            parent_id = _resolve_ref(
                chart_payload, "parent_node_id", "parent_node_index", nodes_by_id, all_nodes_map,
            )
            x, y = _position(chart_payload)
            chart = document.add_chart_node(x, y, parent_id, chart_type, chart_data, chart_error=chart_error)
            # Aspect-lock MUST be applied before any resize: resize_chart's
            # own aspect-preserving re-derivation reads chart_aspect_locked
            # at call time, and a freshly-created chart always starts locked
            # (the dataclass default) - toggling it afterward would be too
            # late, since the requested (width, height) would already have
            # been silently overridden to preserve the default 680x500 ratio.
            aspect_locked = bool(chart_payload.get("aspect_ratio_locked", True))
            if aspect_locked != chart.state.chart_aspect_locked:
                document.toggle_chart_aspect_lock(chart.id)
            size = chart_payload.get("size")
            if isinstance(size, dict) and "width" in size and "height" in size:
                document.resize_chart(chart.id, _finite_float(size["width"]), _finite_float(size["height"]))
        except Exception:
            continue
        charts_map[index] = chart.id
        chart_payload_id = chart_payload.get("id")
        if chart_payload_id:
            charts_by_id[str(chart_payload_id)] = chart.id
    return charts_map, charts_by_id


def _restore_frames(
    document: SceneDocument, frames_data: list, frame_source_map: dict[int, str],
) -> dict[int, str]:
    frames_map: dict[int, str] = {}
    if not isinstance(frames_data, list):
        return frames_map
    for index, frame_payload in enumerate(frames_data):
        if not isinstance(frame_payload, dict):
            continue
        try:
            # Pure positional membership - despite frames also carrying an
            # "item_ids" list, deserialize_frame never reads it; "nodes" is
            # an older fallback key name for the same list.
            item_indices = frame_payload.get("items", frame_payload.get("nodes", []))
            item_indices = item_indices if isinstance(item_indices, list) else []
            member_ids = [frame_source_map[i] for i in item_indices if i in frame_source_map]
            if not member_ids:
                continue
            frame = document.create_frame(member_ids)
            document.set_group_label(frame.id, str(frame_payload.get("note", "") or ""))
            document.set_group_color(frame.id, frame_payload.get("color"), frame_payload.get("header_color"))
            if bool(frame_payload.get("is_locked", True)) != frame.state.is_locked:
                document.toggle_frame_lock(frame.id)
            # R6.4 translation, NOT a gap: deserialize_frame sets
            # frame._user_resized = True whenever the payload's "rect" key
            # is truthy, and serialize_frame ALWAYS writes a fully-populated
            # "rect" dict - so every legacy load marks every frame as
            # manually-sized, regardless of whether the user ever actually
            # dragged a resize handle. The backend's analogous mechanism is
            # group_manual_width/height being non-None. Getting this wrong
            # (leaving them None "because it wasn't manually resized") would
            # make a freshly-loaded frame silently auto-resnap to its
            # content bbox the moment any member moves - so this ALWAYS
            # populates them from the saved size when present, matching
            # legacy's own unconditional behavior. "size" (width/height
            # only, no x/y) is the older fallback shape.
            rect = frame_payload.get("rect")
            size = frame_payload.get("size")
            width_height = None
            if isinstance(rect, dict) and "width" in rect and "height" in rect:
                width_height = (_finite_float(rect["width"]), _finite_float(rect["height"]))
            elif isinstance(size, dict) and "width" in size and "height" in size:
                width_height = (_finite_float(size["width"]), _finite_float(size["height"]))
            if width_height is not None:
                document.resize_frame(frame.id, width_height[0], width_height[1])
            # The POSITION half of that same override, and for the identical
            # reason. create_frame above placed this frame at its live
            # bbox-of-members position, which _bbox_of_members derives from
            # GROUP_MEMBER_DEFAULT_WIDTH/HEIGHT ESTIMATES (220x120,
            # backend/domain/model.py) - no real rendered node is that size,
            # so that placement is never where the user actually left the
            # frame. Restoring the saved x/y through move_node (rather than
            # assigning node.x/y directly) is what makes it STICK: move_node
            # is the same call a live frame drag commits, and pinning
            # group_manual_x/y is the only thing _recompute_group_bounds
            # honors - a bare x/y assignment would be silently recomputed
            # away by the very next member move. Union-with-live-content
            # still applies exactly as it does for a drag, so a restored
            # anchor can no more clip a member than a dragged one can.
            # Ordered AFTER resize_frame (whose own trailing recompute
            # re-centers on the member bbox) and BEFORE the collapse toggle
            # below, so a collapsed frame's pill lands on the saved
            # position too - that branch snaps the size and leaves x/y
            # untouched.
            position_xy = None
            if isinstance(rect, dict) and "x" in rect and "y" in rect:
                position_xy = (_finite_float(rect["x"]), _finite_float(rect["y"]))
            elif isinstance(frame_payload.get("position"), dict):
                position_xy = _position(frame_payload)
            if position_xy is not None:
                document.move_node(frame.id, position_xy[0], position_xy[1])
            if bool(frame_payload.get("is_collapsed", False)):
                document.toggle_group_collapsed(frame.id)
        except Exception:
            continue
        frames_map[index] = frame.id
    return frames_map


def _restore_containers(
    document: SceneDocument,
    containers_data: list,
    all_items_map: dict[int, str],
    container_base_index: int,
) -> dict[int, str]:
    if not isinstance(containers_data, list):
        return {}

    # Containers occupy the tail of the serialized all-items index and may
    # themselves be container members. Restore dependency-ready payloads
    # first and add each new id to the same map immediately, so an outer
    # container can resolve the inner container created earlier in this pass.
    # The deferred loop also tolerates hand-edited/legacy payloads whose
    # container order is not dependency-first. A cycle cannot be represented
    # by the live model; if no pass makes progress, the unresolved payloads
    # are left out instead of creating silently incomplete groups.
    container_slots = set(range(container_base_index, container_base_index + len(containers_data)))
    pending = [
        (index, payload)
        for index, payload in enumerate(containers_data)
        if isinstance(payload, dict)
    ]
    containers_map: dict[int, str] = {}

    while pending:
        deferred: list[tuple[int, dict[str, Any]]] = []
        made_progress = False
        for index, container_payload in pending:
            item_indices = container_payload.get("items", [])
            item_indices = item_indices if isinstance(item_indices, list) else []
            try:
                if any(i in container_slots and i not in all_items_map for i in item_indices):
                    deferred.append((index, container_payload))
                    continue
            except (TypeError, ValueError):
                # Preserve the loader's tolerant posture for malformed item
                # lists: the creation block below will skip this payload.
                pass

            container = None
            try:
                member_ids = [all_items_map[i] for i in item_indices if i in all_items_map]
                if not member_ids:
                    continue
                container = document.create_container(member_ids)
                # "Container" matches deserialize_container's own restore-time
                # default (data.get("title", "Container")) - NOT
                # create_container's own fresh-creation default ("New
                # Container"), a different code path with a different default.
                document.set_group_label(container.id, str(container_payload.get("title", "") or "Container"))
                document.set_group_color(
                    container.id,
                    container_payload.get("color"),
                    container_payload.get("header_color"),
                )
                if bool(container_payload.get("is_collapsed", False)):
                    document.toggle_group_collapsed(container.id)
            except Exception:
                continue

            serialized_index = container_base_index + index
            containers_map[index] = container.id
            all_items_map[serialized_index] = container.id
            made_progress = True

        if not made_progress:
            break
        pending = deferred

    return containers_map


# The 7 kinds every era has always had, plus 5 that only existed before
# R5-closeout deleted their node kinds - restoring THESE 5 from an old save
# is still meaningful even though the node kinds they usually joined are
# gone, since a connection can link any two still-restorable nodes.
_BASIC_CONNECTION_KEYS = (
    "connections", "content_connections", "document_connections",
    "image_connections", "thinking_connections", "conversation_connections", "html_connections",
    "pycoder_connections", "code_sandbox_connections", "web_connections",
    "artifact_connections", "gitlink_connections",
)


def _restore_basic_connections(
    document: SceneDocument, chat_data: dict[str, Any],
    all_nodes_map: dict[int, str], nodes_by_id: dict[str, str],
) -> None:
    """All 12 lists share IDENTICAL shape (_serialize_basic_connection:
    start/end_node_index + start/end_node_id) - one generic pass handles all
    of them; the specific node KIND at either end is irrelevant to edge
    creation."""
    for key in _BASIC_CONNECTION_KEYS:
        entries = chat_data.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_id = _resolve_ref(entry, "start_node_id", "start_node_index", nodes_by_id, all_nodes_map)
            target_id = _resolve_ref(entry, "end_node_id", "end_node_index", nodes_by_id, all_nodes_map)
            if source_id is None or target_id is None or source_id == target_id:
                continue
            try:
                # connect_unchecked, not connect(): this is the pre-stage-9.6
                # per-kind bucket restore, where two connections between the
                # same node pair in opposite directions (e.g. one from
                # pycoder_connections, another from gitlink_connections)
                # legitimately encoded two DIFFERENT semantic relationship
                # kinds under the old classification scheme, not a mistake -
                # connect()'s own cycle rejection (correct for a live edit)
                # would silently drop real historical data here. See
                # SceneDocument.connect_unchecked's own docstring.
                document.connect_unchecked(source_id, target_id)
            except Exception:
                continue


def _restore_system_prompt_and_summary_connections(
    document: SceneDocument, chat_data: dict[str, Any],
    notes_map: dict[int, str], chat_nodes_map: dict[int, str], nodes_by_id: dict[str, str],
) -> None:
    """The chat-node-side reference resolves id-preferred against the
    GENERAL nodes_by_id map (a node's payload id is unique across every
    kind) and index-fallback against chat_nodes_map specifically (verified
    directly against restore_chat's own call sites: both
    deserialize_system_prompt_connection and deserialize_group_summary_
    connection are called with chat_nodes_map bound to the "nodes_map"
    parameter, not all_nodes_map) - the save-side ordinal counted only
    across chat-type payload entries (bug #47's own fix)."""
    sp_entries = chat_data.get("system_prompt_connections")
    if isinstance(sp_entries, list):
        for entry in sp_entries:
            if not isinstance(entry, dict):
                continue
            # REVIEW-FIX: start_note_index is an untrusted payload value and
            # can legitimately be a list/dict instead of the expected int -
            # notes_map.get(x) raises TypeError for an unhashable x, which
            # would abort the entire chat load. notes_map is int-keyed, so
            # guard with the same isinstance(..., int) check _resolve_ref
            # above already uses for its own index fallback.
            start_note_index = entry.get("start_note_index")
            note_id = notes_map.get(start_note_index) if isinstance(start_note_index, int) else None
            target_id = _resolve_ref(entry, "end_node_id", "end_node_index", nodes_by_id, chat_nodes_map)
            if note_id is not None and target_id is not None:
                try:
                    # connect_unchecked - see _restore_basic_connections' own
                    # comment just above for why this legacy bucket restore
                    # must not go through connect()'s cycle rejection.
                    document.connect_unchecked(note_id, target_id)
                except Exception:
                    continue

    gs_entries = chat_data.get("group_summary_connections")
    if isinstance(gs_entries, list):
        for entry in gs_entries:
            if not isinstance(entry, dict):
                continue
            source_id = _resolve_ref(entry, "start_node_id", "start_node_index", nodes_by_id, chat_nodes_map)
            # REVIEW-FIX: same unhashable-JSON-value risk as start_note_index
            # above - guard end_note_index the same way before using it as a
            # notes_map key.
            end_note_index = entry.get("end_note_index")
            note_id = notes_map.get(end_note_index) if isinstance(end_note_index, int) else None
            if source_id is not None and note_id is not None:
                try:
                    # connect_unchecked - same reasoning as the two restore
                    # sites above.
                    document.connect_unchecked(source_id, note_id)
                except Exception:
                    continue


def _restore_flat_edges(
    document: SceneDocument,
    chat_data: dict[str, Any],
    by_payload_id: dict[str, str],
) -> bool:
    """ADR-009 stage 9.6: restore edges from the flat `edges` list.

    Returns True if that list was present and used, False to tell the
    caller to fall back to the legacy bucket reconstruction below.

    Why this exists at all: the 12 legacy connection lists plus the
    system-prompt/group-summary pair lists are a *classification* of edges
    invented by an app that had 14 separate visual edge types. This backend
    has exactly one - document.connect() - so save-side classification is a
    guess (see session_save._classify_edges' own comments) and load-side
    reconstruction has to unpick that guess through index-vs-id fallbacks
    that differ per bucket. A flat list of (source, target) by persistent
    id is what the document actually holds, so it round-trips exactly and
    needs no reconstruction.

    Endpoints resolve against a map spanning nodes, notes AND charts,
    because an edge in this document may legitimately end on any of them -
    the legacy path needed three different maps for the same reason. An
    endpoint that no longer resolves (hand-edited file, a node dropped by
    an earlier restore step) skips that one edge; same tolerant posture as
    every other reference restored here, since a missing line must never
    cost the user the whole conversation."""
    entries = chat_data.get("edges")
    if not isinstance(entries, list):
        return False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_id = by_payload_id.get(str(entry.get("source")))
        target_id = by_payload_id.get(str(entry.get("target")))
        if source_id is None or target_id is None or source_id == target_id:
            continue
        try:
            document.connect(source_id, target_id)
        except Exception:
            continue
    return True


def _restore_branch_provenance_item_ids(
    document: SceneDocument,
    node_payloads: list,
    all_nodes_map: dict[int, str],
    nodes_by_id: dict[str, str],
    notes_data: list,
    notes_map: dict[int, str],
) -> None:
    """ADR-002 Workstream 1 ("Branch status and lifecycle") - confirmed,
    pre-existing gap fixed inline: a Synthesize Branches chat node's or a
    Compare Branches note's own item_ids (the source branch node ids - see
    SceneNode.item_ids's own comment on backend/canvas.py) reference OTHER
    nodes by their ORIGINAL saved id, which is re-minted on load - the same
    "translate through nodes_by_id, once every node has a resolved new id"
    idiom _restore_system_prompt_and_summary_connections above already
    uses. Runs as its own pass AFTER both the main node-restore loop and
    _restore_notes have already completed (not inline inside
    _restore_chat_payload/_restore_notes themselves, which run too early -
    a synthesis/comparison result's own sources may not have a resolved new
    id yet at that point). An id no longer present in nodes_by_id (a source
    deleted before save, or a genuinely stale/corrupt reference) is
    silently dropped from the restored list rather than aborting the load -
    matching this file's own established defensive posture elsewhere."""
    for index, node_payload in enumerate(node_payloads):
        if not isinstance(node_payload, dict) or not node_payload.get("is_branch_synthesis"):
            continue
        new_id = all_nodes_map.get(index)
        if new_id is None or new_id not in document.nodes:
            continue
        raw_item_ids = node_payload.get("item_ids")
        if isinstance(raw_item_ids, list):
            document.nodes[new_id].item_ids = [
                nodes_by_id[str(i)] for i in raw_item_ids if str(i) in nodes_by_id
            ]

    if not isinstance(notes_data, list):
        return
    for index, note_payload in enumerate(notes_data):
        if not isinstance(note_payload, dict) or not note_payload.get("is_branch_comparison"):
            continue
        new_id = notes_map.get(index)
        if new_id is None or new_id not in document.nodes:
            continue
        raw_item_ids = note_payload.get("item_ids")
        if isinstance(raw_item_ids, list):
            document.nodes[new_id].item_ids = [
                nodes_by_id[str(i)] for i in raw_item_ids if str(i) in nodes_by_id
            ]


def _restore_pins(document: SceneDocument, pins_data: list) -> None:
    if not isinstance(pins_data, list) or not pins_data:
        return
    records = []
    for pin_payload in pins_data:
        if not isinstance(pin_payload, dict):
            continue
        try:
            records.append(NavigationPinRecord.from_mapping(pin_payload, fallback_order=len(records)))
        except Exception:
            continue
    if records:
        try:
            document.pins.reset(records)
        except Exception:
            pass


def _restore_view_state(document: SceneDocument, chat_data: dict[str, Any]) -> None:
    view_state = chat_data.get("view_state")
    if not isinstance(view_state, dict):
        return
    zoom = view_state.get("zoom_factor", document.zoom_factor)
    scroll = view_state.get("scroll_position")
    scroll_x = document.scroll_x
    scroll_y = document.scroll_y
    if isinstance(scroll, dict):
        scroll_x = scroll.get("x", scroll_x)
        scroll_y = scroll.get("y", scroll_y)
    try:
        document.set_view_state(_finite_float(zoom), _finite_float(scroll_x), _finite_float(scroll_y))
    except Exception:
        pass


def restore_chat_into_document(
    document: SceneDocument,
    chat: dict[str, Any],
    notes_data: list,
    pins_data: list,
    asset_store: Any | None = None,
    plugin_registry: "PluginRegistry | None" = None,
    settings_manager: "SettingsManager | None" = None,
) -> None:
    """ADR-009 stage 9.5 entry point. Publishes `asset_store` for the
    duration of this restore (see _ACTIVE_ASSET_STORE's own comment for why
    a contextvar and not a parameter), then delegates. The reset is in a
    finally so a failed restore can never leave a stale store visible to
    the next one.

    ADR-014 stage 14.2: `plugin_registry` mirrors backend/session_save.py's
    build_chat_data(..., plugin_registry=None) precedent exactly - None
    (every real call site: backend/chat_library.py) triggers a real
    discover_plugins() call, memoized by resolved path; tests inject a
    specific registry for isolation.

    ADR-014 review-fix: `settings_manager`, when given, is threaded down to
    _restore_plugin_payload so a plugin's own deserialize hook is gated on
    its current Settings > Plugins grant - the load-side mirror of
    build_chat_data's own new `settings_manager` parameter. `None` (this
    parameter's own default) preserves the exact prior ungated behavior."""
    token = _ACTIVE_ASSET_STORE.set(asset_store)
    try:
        _restore_chat_into_document(document, chat, notes_data, pins_data, plugin_registry, settings_manager)
    finally:
        _ACTIVE_ASSET_STORE.reset(token)


def _restore_chat_into_document(
    document: SceneDocument,
    chat: dict[str, Any],
    notes_data: list,
    pins_data: list,
    plugin_registry: "PluginRegistry | None" = None,
    settings_manager: "SettingsManager | None" = None,
) -> None:
    """The top-level orchestrator - ports SceneDeserializer.restore_chat()'s
    own exact ordering, reimplemented against SceneDocument. See this
    module's own docstring for the full algorithm and every confirmed gap/
    translation. Raises only for a genuinely unusable `chat` argument (not a
    mapping at all) - every per-item failure inside is already swallowed
    locally; the caller (backend/chat_library.py's loadChat intent) is
    responsible for the OUTER safety net matching legacy's own
    _handle_load_error (a notification, not a crash) for anything that still
    escapes."""
    if not isinstance(chat, dict):
        raise ValueError("chat payload must be a mapping")

    if plugin_registry is None:
        plugin_registry = discover_plugins()

    document.clear_for_load()

    chat_data = chat.get("data")
    chat_data = chat_data if isinstance(chat_data, dict) else {}

    node_payloads = _resolve_node_payload_list(chat_data)

    all_nodes_map: dict[int, str] = {}
    nodes_by_id: dict[str, str] = {}
    kind_by_new_id: dict[str, str] = {}
    chat_nodes_map: dict[int, str] = {}
    chat_payload_position = 0

    for index, node_payload in enumerate(node_payloads):
        if not isinstance(node_payload, dict):
            continue
        # SECURITY-FIX: node_type feeds _NODE_RESTORERS.get(node_type) - a
        # dict-key lookup that raises TypeError('unhashable type') uncaught
        # for a non-hashable JSON value (a list or dict), aborting the
        # ENTIRE chat load over one malformed node in a hostile or
        # hand-corrupted saved chat, the same class of bug already fixed
        # elsewhere in this file for other unhashable-lookup sites. A
        # non-string node_type is simply unrecognized, exactly like any
        # other unknown kind string - str() coercion keeps it hashable so
        # the existing "skip, don't raise" fallback in _restore_node
        # actually runs instead of crashing before it's reached.
        raw_node_type = node_payload.get("node_type", "chat")
        node_type = raw_node_type if isinstance(raw_node_type, str) else str(raw_node_type)
        node, parent_new_id = _restore_node(
            document, node_type, node_payload, all_nodes_map, plugin_registry, settings_manager,
        )
        if node is not None:
            all_nodes_map[index] = node.id
            kind_by_new_id[node.id] = node.kind
            payload_id = node_payload.get("id")
            if payload_id:
                nodes_by_id[str(payload_id)] = node.id
            if parent_new_id is not None:
                document.connect(parent_new_id, node.id)
        if node_type == "chat":
            if node is not None:
                chat_nodes_map[chat_payload_position] = node.id
            chat_payload_position += 1

    _restore_children(node_payloads, all_nodes_map, nodes_by_id, kind_by_new_id, document)

    notes_map = _restore_notes(document, notes_data)

    # ADR-002 Workstream 1 ("Branch status and lifecycle") - confirmed,
    # pre-existing gap fixed inline: must run AFTER both the main
    # node-restore loop and _restore_notes above, since it translates
    # cross-node references (a synthesis/comparison result's own source
    # ids) that aren't resolvable until every node it might reference has
    # its own new id - see that function's own docstring.
    _restore_branch_provenance_item_ids(document, node_payloads, all_nodes_map, nodes_by_id, notes_data, notes_map)

    charts_map, charts_by_id = _restore_charts(document, chat_data.get("charts", []), all_nodes_map, nodes_by_id)

    node_slot_count = len(node_payloads)
    note_slot_count = len(notes_data) if isinstance(notes_data, list) else 0
    chart_slot_count = len(chat_data.get("charts", [])) if isinstance(chat_data.get("charts"), list) else 0
    frame_slot_count = len(chat_data.get("frames", [])) if isinstance(chat_data.get("frames"), list) else 0

    frame_source_map = dict(all_nodes_map)
    for chart_index, chart_new_id in charts_map.items():
        frame_source_map[node_slot_count + chart_index] = chart_new_id
    frames_map = _restore_frames(document, chat_data.get("frames", []), frame_source_map)

    all_items_map = dict(all_nodes_map)
    for note_index, note_new_id in notes_map.items():
        all_items_map[node_slot_count + note_index] = note_new_id
    for chart_index, chart_new_id in charts_map.items():
        all_items_map[node_slot_count + note_slot_count + chart_index] = chart_new_id
    for frame_index, frame_new_id in frames_map.items():
        all_items_map[node_slot_count + note_slot_count + chart_slot_count + frame_index] = frame_new_id
    container_base_index = node_slot_count + note_slot_count + chart_slot_count + frame_slot_count
    _restore_containers(
        document,
        chat_data.get("containers", []),
        all_items_map,
        container_base_index,
    )

    # ADR-009 stage 9.6. A file written by this build carries a flat
    # `edges` list and it is authoritative; the legacy buckets are only
    # consulted for files written before this stage. Structural parent and
    # child edges have already been created by the restore loops above -
    # re-asserting them here is harmless because SceneDocument.connect() is
    # idempotent on (source, target), which is also what makes it safe for
    # the flat list to simply contain EVERY edge rather than only the ones
    # no earlier step covered.
    by_payload_id = dict(nodes_by_id)
    if isinstance(notes_data, list):
        for note_index, note_payload in enumerate(notes_data):
            note_new_id = notes_map.get(note_index)
            if not isinstance(note_payload, dict) or note_new_id is None:
                continue
            note_payload_id = note_payload.get("id")
            if note_payload_id:
                by_payload_id[str(note_payload_id)] = note_new_id
    by_payload_id.update(charts_by_id)

    if not _restore_flat_edges(document, chat_data, by_payload_id):
        _restore_basic_connections(document, chat_data, all_nodes_map, nodes_by_id)
        _restore_system_prompt_and_summary_connections(
            document, chat_data, notes_map, chat_nodes_map, nodes_by_id
        )

    _restore_pins(document, pins_data)
    _restore_view_state(document, chat_data)

    total_tokens = chat_data.get("total_session_tokens", 0)
    try:
        document.total_session_tokens = int(total_tokens)
    except (TypeError, ValueError):
        document.total_session_tokens = 0

    # ADR-002 Workstream 1 ("Branch status and lifecycle"): translated
    # through nodes_by_id, same as every other cross-node reference above -
    # a missing/stale/deleted-before-save reference is silently treated as
    # "no deliverable marked" rather than aborting the load. The kind_by_
    # new_id check (found by adversarial review) mirrors SceneDocument.
    # set_final_deliverable's own chat-kind-only validation - without it, a
    # malformed/hand-edited save file pointing this at a non-chat node would
    # load successfully and violate that invariant for the rest of the
    # session, since this path (unlike the live setter) has no SceneError
    # to raise against.
    raw_final_id = chat_data.get("final_deliverable_node_id")
    if raw_final_id and str(raw_final_id) in nodes_by_id:
        final_new_id = nodes_by_id[str(raw_final_id)]
        if kind_by_new_id.get(final_new_id) == "chat":
            document.final_deliverable_node_id = final_new_id
