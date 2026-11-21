"""Generation-context fingerprints and publish-time downstream staleness.

Only replies with an explicitly captured v1 fingerprint can be out of date.
Legacy saves have no fingerprint, so editing or reloading them never invents
staleness. Both the full snapshot and dirty-patch path compute this once per
publish using a linear parent-map/lineage traversal, not one graph walk per
node. Secondary, reference and association edges do not change ancestry.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from backend.domain.node_states import ChatState
from backend.domain.structure import is_structural_edge

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument

_ROOT = hashlib.sha256(b"graphlink/branch-history/v1").digest()


def _bytes_as_json(value: Any) -> dict[str, str]:
    if isinstance(value, (bytes, bytearray)):
        # Hash raw bytes rather than base64-encoding large images on every
        # publish. This is stable across save/load and keeps event-loop
        # work/memory bounded by SHA-256's streaming C implementation.
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    raise TypeError(f"unsupported branch content: {type(value).__name__}")


def _message_input(node) -> bytes:
    """Only model-visible content and branch settings, never layout or IDs."""
    if isinstance(node.state, ChatState):
        # chat_branch_history also falls back to the text mirror when the
        # parts list is empty; the fingerprint must follow that exact input.
        # The selected ChatVersion is projected into node.content/parts by
        # set_chat_version. Never hash state.versions or version_index: past
        # alternatives do not belong to the model-visible current history.
        content = node.state.content_parts or node.content
        fields = (
            "user" if node.state.is_user else "assistant", content,
            node.state.override_provider, node.state.override_model_id, node.state.use_knowledge,
        )
    else:
        # Stray non-chat ancestors appear as assistant messages in
        # chat_branch_history. Preserve the same role/content semantics.
        fields = ("assistant", node.content)
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=_bytes_as_json).encode("utf-8")


def _parents(document: SceneDocument) -> dict[str, str]:
    """Explicit primary first, then the first eligible legacy edge.

    This matches preferred_parent_edge for valid graphs but indexes incoming
    edges once instead of scanning every edge for every ancestor, avoiding an
    O(nodes * edges) publish regression.
    """
    chosen: dict[str, str] = {}
    for role in ("primary", "legacy"):
        for edge in document.edges.values():
            if (
                edge.role == role and edge.target not in chosen
                and is_structural_edge(document.nodes, edge)
            ):
                chosen[edge.target] = edge.source
    return chosen


def _reply_digest(history_digest: bytes, provider: str | None, model: str | None, knowledge: bool | None) -> str:
    route = json.dumps((provider or "", model or "", knowledge), separators=(",", ":")).encode("utf-8")
    return "v1:" + hashlib.sha256(b"graphlink/reply/v1\0" + history_digest + route).hexdigest()


def _lineage(document: SceneDocument) -> tuple[dict[str, bytes], dict[str, str]]:
    parents = _parents(document)
    hashes: dict[str, bytes] = {}
    statuses: dict[str, str] = {}
    affected: dict[str, bool] = {}

    # Iterative parent-first resolution handles very deep chains without
    # recursion limits. Each node is hashed at most once per publish.
    for node_id in document.nodes:
        if node_id in hashes:
            continue
        trail: list[str] = []
        seen: set[str] = set()
        cursor: str | None = node_id
        while cursor in document.nodes and cursor not in hashes and cursor not in seen:
            seen.add(cursor)
            trail.append(cursor)
            cursor = parents.get(cursor)
        if cursor in seen:
            # Old malformed saved graphs may contain cycles. Do not invent
            # provenance or loop forever: such a chain stays unbadged.
            for item in trail:
                hashes[item] = _ROOT
                affected[item] = False
            continue

        prior = hashes.get(cursor, _ROOT) if cursor is not None else _ROOT
        upstream_changed = affected.get(cursor, False) if cursor is not None else False
        for item in reversed(trail):
            node = document.nodes[item]
            if (
                isinstance(node.state, ChatState) and not node.state.is_user
                and node.state.reply_history_fingerprint
            ):
                expected = _reply_digest(
                    prior, node.state.provider, node.state.model, node.state.reply_knowledge_enabled,
                )
                if expected != node.state.reply_history_fingerprint:
                    statuses[item] = "out_of_date"
                    upstream_changed = True
            elif node.kind != "chat":
                # A derived non-chat node can hang directly off an edited
                # user/assistant message, with no intervening generated
                # reply. Its own creation-time parent history captures that
                # dependency. Existing sessions without a snapshot do not
                # acquire one just because they are published.
                baseline = document.nonchat_upstream_fingerprints.get(item)
                # With its structural parent removed, this node has no
                # live upstream history. A stored baseline alone must not
                # mark an otherwise unchanged orphan as stale.
                if (
                    baseline is not None and item in parents
                    and baseline != "v1:" + prior.hex()
                ):
                    upstream_changed = True
                if upstream_changed:
                    statuses[item] = "upstream_changed"

            hashes[item] = hashlib.sha256(prior + _message_input(node)).digest()
            affected[item] = upstream_changed
            prior = hashes[item]
    return hashes, statuses


def fingerprint_for_reply(
    document: SceneDocument, context_node_id: str,
    provider: str | None, model: str | None, knowledge_enabled: bool,
) -> str:
    """Snapshot the branch ending at the request's last input message.

    This is called at dispatch time (not completion): subsequent edits,
    reparenting, model-pin changes and knowledge-preference changes cannot
    silently rewrite the context that the model actually received.
    """
    hashes, _ = _lineage(document)
    return _reply_digest(hashes.get(context_node_id, _ROOT), provider, model, knowledge_enabled)


def scene_staleness(document: SceneDocument) -> dict[str, str]:
    """Sparse per-node publish statuses; all pre-feature graphs return {}."""
    if not document.nonchat_upstream_fingerprints and not any(
        isinstance(node.state, ChatState) and node.state.reply_history_fingerprint
        for node in document.nodes.values()
    ):
        return {}
    _, statuses = _lineage(document)
    return statuses


def capture_nonchat_context(document: SceneDocument, node_id: str) -> None:
    """Record a NEW non-chat node's current structural-parent history once.

    Called when its first structural edge attaches. A saved non-chat node
    restored from before this feature never gets a retroactive baseline.
    New downstream nodes attached after an edit capture the *new* context,
    so they do not inherit a spurious direct-context mismatch.
    """
    node = document.nodes.get(node_id)
    if (
        node is None or node.kind == "chat"
        or node_id in document.nonchat_upstream_fingerprints
    ):
        return
    parents = _parents(document)
    current = parents.get(node_id)
    if current is None:
        return
    seen: set[str] = set()
    path: list[str] = []
    while current in document.nodes and current not in seen:
        seen.add(current)
        path.append(current)
        current = parents.get(current)
    if current in seen:
        return
    digest = _ROOT
    for ancestor_id in reversed(path):
        digest = hashlib.sha256(digest + _message_input(document.nodes[ancestor_id])).digest()
    document.nonchat_upstream_fingerprints[node_id] = "v1:" + digest.hex()
