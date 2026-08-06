"""CommandOps - ADR-010 stage 10.1: the command layer, undo/redo's foundation.

A MIXIN, not a standalone class, following the exact composition pattern
BranchOps/GroupOps already established: methods operate on the composing
dataclass's own state (self.nodes/self.edges/self.image_assets), composed
exactly once by domain/graph.py's `class SceneDocument(BranchOps, GroupOps,
CommandOps)`.

## Why a scoped before/after diff, not hand-authored inverses

The ADR's own text ("invert a delete = re-add those nodes+edges; invert a
move = restore prior positions") reads as if each mutation type needs its
own bespoke inverse. Recon into the ACTUAL domain model (done before writing
this file, not assumed) found three traps that make hand-authoring wrong in
practice, not just tedious:

- Node/edge ids come from one shared monotonic counter that never reuses
  values (SceneDocument._counter). A hand-authored "invert a delete by
  calling add_chat_node again" gets a NEW id - every reference to the old
  one (edges, frame/container item_ids, last_chat_node_id, pins) would need
  remapping. See node_states.py's PycoderState.pycoder_repl_id for the
  documented precedent of a REAL bug this exact instability already caused
  once (session reload silently swapping which on-disk REPL directory a
  node resolved to).
- remove_nodes() cascades silently: it evicts image/chart asset bytes from
  image_assets with no return value, and calls _detach_node_from_membership
  (groups.py), which can itself delete a SECOND node (an emptied frame/
  container) never named in the caller's node_ids list and never reported
  back.
- connect() is idempotent - it returns the pre-existing edge if
  source->target already exists. Inverting "connect" by deleting whatever
  edge it returned is wrong whenever no new edge was actually minted.

A snapshot-and-restore Command sidesteps all three by construction: undoing
a delete restores the SAME node object under its SAME id (no counter
involved, no remapping needed); a cascade-deleted frame is caught by the
generic before/after id-set diff below rather than needing to be
specifically anticipated; connect()'s idempotence is invisible to the diff
when it doesn't change anything, so a no-op connect naturally produces an
empty command.

This is NOT the "snapshot deque" the ADR's own Alternatives section
rejects. That alternative serializes the WHOLE document per op (megabytes
at 500 nodes). This snapshots only the ids a command actually touches -
O(command size), not O(document size) - the same complexity class the
ADR's chosen design already commits to elsewhere ("undo cost is O(change),
not O(graph)"). See record_command's own doc for the one deliberate
exception (frame/container nodes are always defensively watched, since
their item_ids/bounds are exactly the state other operations mutate as a
side effect) and why that exception is still bounded, not O(document size).

## What this module does NOT do (yet)

No undo/redo STACK exists here - that is stage 10.2, a separate ADR-010
stage with its own exit criterion. This module produces Command objects
whose apply()/invert() are proven correct in isolation; nothing here
decides when to call them or how many to retain. Composite commands
(stage 10.3), live-run refusal (10.4), and provenance-scoped "undo last
build" (10.5) are equally out of scope here.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, TypeVar

if TYPE_CHECKING:
    from backend.domain.model import SceneEdge, SceneNode

T = TypeVar("T")

# Asset bytes (image/chart PNGs) are the one thing in this module that IS
# genuinely expensive to snapshot - a multi-megabyte image is not "O(command
# size)" in the same cheap sense a node/edge dataclass copy is. Deliberately
# keyed as tuple[bytes, str] (raw bytes + mime type) to match
# SceneDocument.image_assets' own value shape exactly, so _restore below can
# write it straight back with no repacking.
_AssetSnapshot = "tuple[bytes, str]"


@dataclass
class Command:
    """A single invertible mutation, already applied by the time this
    object exists. `*_before`/`*_after` are keyed by id; a value of None
    means "this id did not exist" (so `invert()` deletes it / `apply()`
    creates it) rather than "no change" - an id with no real change at all
    is simply absent from the dict, keeping empty commands (e.g. an
    idempotent connect() that created nothing) cheap and easy to detect via
    `is_noop`.

    provenance: "user" for a direct user-initiated intent, or
    f"agent:{run_id}" for an agent-driven mutation - carried now so stage
    10.5's "undo last build" has something to key on later; unused by
    apply()/invert() themselves.
    """

    command_type: str
    provenance: str
    node_before: dict[str, "SceneNode | None"] = field(default_factory=dict)
    node_after: dict[str, "SceneNode | None"] = field(default_factory=dict)
    edge_before: dict[str, "SceneEdge | None"] = field(default_factory=dict)
    edge_after: dict[str, "SceneEdge | None"] = field(default_factory=dict)
    asset_before: dict[str, "tuple[bytes, str] | None"] = field(default_factory=dict)
    asset_after: dict[str, "tuple[bytes, str] | None"] = field(default_factory=dict)

    @property
    def is_noop(self) -> bool:
        """True when the mutator this command wrapped touched nothing that
        survived the diff (id.e. every explicitly/defensively watched id
        came out byte-identical to how it went in) - the idempotent-
        connect() case is the motivating example, but any accidental no-op
        call hits this the same way."""
        return not (
            self.node_before or self.node_after or self.edge_before or self.edge_after
        )

    def invert(self, document: object) -> None:
        """Restores document state to exactly how it was before this
        command's mutator ran. Goes through the *_before snapshots only -
        never re-invokes the original domain method, which is the whole
        point (re-invoking move_node with old coordinates would work, but
        re-invoking add_chat_node to "undo a delete" would mint a new id;
        restoring the captured object under its original id sidesteps that
        entirely)."""
        _restore(document.nodes, self.node_before)
        _restore(document.edges, self.edge_before)
        _restore(document.image_assets, self.asset_before)

    def apply(self, document: object) -> None:
        """The mirror of invert() - restores to the *_after state. Not
        needed for a first-time forward mutation (that already happened
        for real before this Command was constructed); this exists for
        REDO, applying a previously-inverted command a second time."""
        _restore(document.nodes, self.node_after)
        _restore(document.edges, self.edge_after)
        _restore(document.image_assets, self.asset_after)


def _restore(live: dict, snapshot: dict) -> None:
    for key, value in snapshot.items():
        if value is None:
            live.pop(key, None)
        else:
            live[key] = copy.deepcopy(value)


class CommandOps:
    def record_command(
        self,
        command_type: str,
        provenance: str,
        mutator: Callable[[], T],
        *,
        node_ids: Iterable[str] = (),
        edge_ids: Iterable[str] = (),
    ) -> "tuple[T, Command]":
        """Runs `mutator` (a zero-arg closure wrapping exactly one call into
        an existing SceneDocument mutator, e.g. `lambda:
        self.remove_nodes(ids)`) and returns `(mutator's return value,
        the Command that inverts it)`.

        `node_ids`/`edge_ids` are the ids the CALLER already knows the
        mutator targets (a create's parent-connect target, a delete's
        victim ids, a move's moved ids, connect's two endpoints) - used to
        take a defensive BEFORE snapshot of anything that might be mutated
        in place rather than created/deleted (move_node mutates x/y on the
        SAME object; a naive before/after id-set diff alone would miss
        that, since the id never leaves self.nodes).

        Every frame/container-kind node is ALSO always defensively
        snapshotted, regardless of node_ids - not because most commands
        touch them, but because they are the one kind of node whose state
        (item_ids, bounds, group_manual_x/y) other operations mutate as a
        documented SIDE EFFECT (_detach_node_from_membership's cascade-
        delete-when-emptied, move_node's bounds recompute on an enclosing
        frame) without that frame ever appearing in the caller's own
        node_ids. This is bounded by frame/container COUNT, not total node
        count - cheap even at 500 nodes, since a scene has far fewer group
        nodes than leaf nodes in practice, and is correct by construction
        for every group side effect discovered during this stage's own
        recon, not just the one the recon happened to name.

        Asset bytes (image_assets) are snapshotted only for explicitly
        named node_ids that currently carry an image_asset_id/
        chart_asset_id - deliberately NOT defensive like the frame case,
        since asset bytes are the one genuinely expensive thing here and
        only a node's OWN delete evicts its OWN asset (see
        remove_nodes' - never a side effect of some other node's edit).

        Raises AssertionError if a node/edge disappears during `mutator()`
        that was not in the watched set - a Command silently missing a
        deletion IS the exact "vanishes without a trace" failure this
        stage exists to prevent, so this fails loud rather than shipping a
        Command whose invert() would be wrong.

        Edge discovery is automatic, not the caller's job: remove_nodes()
        deletes EVERY edge touching any of its target ids (both directions,
        including a parent-connect edge created as a side effect of the
        node's own creation, e.g. add_image_node's unconditional connect())
        - requiring the caller to enumerate those by hand is exactly the
        kind of easy-to-forget bookkeeping this layer exists to remove.
        `edge_ids` remains for the cases with no node on one/either end of
        the relevant edges to discover from (connect's freshly-minted edge
        is caught by the id-set diff below either way; removeEdges' targets
        have no other discovery path since the node(s) they touch are not
        being deleted)."""
        watch_node_ids = set(node_ids) | {
            n.id for n in self.nodes.values() if n.kind in ("frame", "container")
        }
        watch_edge_ids = set(edge_ids) | {
            e.id for e in self.edges.values() if e.source in watch_node_ids or e.target in watch_node_ids
        }

        node_snapshot_before = {
            nid: copy.deepcopy(self.nodes[nid]) for nid in watch_node_ids if nid in self.nodes
        }
        edge_snapshot_before = {
            eid: copy.deepcopy(self.edges[eid]) for eid in watch_edge_ids if eid in self.edges
        }
        asset_snapshot_before: dict[str, tuple[bytes, str]] = {}
        for nid in set(node_ids):
            node = self.nodes.get(nid)
            if node is None or node.state is None:
                continue
            for attr in ("image_asset_id", "chart_asset_id"):
                asset_id = getattr(node.state, attr, None)
                if asset_id and asset_id in self.image_assets:
                    asset_snapshot_before[asset_id] = self.image_assets[asset_id]

        node_ids_before = set(self.nodes.keys())
        edge_ids_before = set(self.edges.keys())
        asset_ids_before = set(self.image_assets.keys())

        result = mutator()

        node_ids_after = set(self.nodes.keys())
        edge_ids_after = set(self.edges.keys())
        asset_ids_after = set(self.image_assets.keys())

        node_before_out: dict[str, "SceneNode | None"] = {}
        node_after_out: dict[str, "SceneNode | None"] = {}
        for nid in node_ids_after - node_ids_before:
            node_before_out[nid] = None
            node_after_out[nid] = copy.deepcopy(self.nodes[nid])
        for nid in node_ids_before - node_ids_after:
            if nid not in node_snapshot_before:
                raise AssertionError(
                    f"command {command_type!r} deleted node {nid!r} with no defensive "
                    "snapshot taken - extend record_command's watch set (node_ids or "
                    "the frame/container defensive set) rather than let this Command "
                    "silently lose it"
                )
            node_before_out[nid] = node_snapshot_before[nid]
            node_after_out[nid] = None
        for nid in watch_node_ids & node_ids_before & node_ids_after:
            before = node_snapshot_before.get(nid)
            after = self.nodes.get(nid)
            if before is not None and after is not None and before != after:
                node_before_out[nid] = before
                node_after_out[nid] = copy.deepcopy(after)

        edge_before_out: dict[str, "SceneEdge | None"] = {}
        edge_after_out: dict[str, "SceneEdge | None"] = {}
        for eid in edge_ids_after - edge_ids_before:
            edge_before_out[eid] = None
            edge_after_out[eid] = copy.deepcopy(self.edges[eid])
        for eid in edge_ids_before - edge_ids_after:
            if eid not in edge_snapshot_before:
                raise AssertionError(
                    f"command {command_type!r} deleted edge {eid!r} with no defensive "
                    "snapshot taken - pass its id via edge_ids"
                )
            edge_before_out[eid] = edge_snapshot_before[eid]
            edge_after_out[eid] = None

        asset_before_out: dict[str, "tuple[bytes, str] | None"] = {}
        asset_after_out: dict[str, "tuple[bytes, str] | None"] = {}
        for aid in asset_ids_after - asset_ids_before:
            asset_before_out[aid] = None
            asset_after_out[aid] = self.image_assets[aid]
        for aid in asset_ids_before - asset_ids_after:
            if aid not in asset_snapshot_before:
                raise AssertionError(
                    f"command {command_type!r} evicted asset {aid!r} with no defensive "
                    "snapshot taken - its owning node must be in node_ids"
                )
            asset_before_out[aid] = asset_snapshot_before[aid]
            asset_after_out[aid] = None

        command = Command(
            command_type=command_type,
            provenance=provenance,
            node_before=node_before_out,
            node_after=node_after_out,
            edge_before=edge_before_out,
            edge_after=edge_after_out,
            asset_before=asset_before_out,
            asset_after=asset_after_out,
        )
        # A no-op command (an idempotent connect() that created nothing, a
        # setter called with the value it already had) is never logged -
        # undoing it would do nothing, so it would only ever consume one of
        # the bounded log's slots and make Ctrl+Z appear to "do nothing
        # once" before reaching the operation the user actually meant.
        if not command.is_noop:
            self.command_log.append(command)
        return result, command
