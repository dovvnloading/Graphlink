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

## The stack (stages 10.2-10.5)

Beyond producing Commands, this module owns the undo/redo stack itself:

- **undo()/redo()** move commands between `command_log` (the undo stack) and
  `redo_stack`. Performing any NEW command clears the redo branch, the
  standard behavior everywhere - undo three things, do something else, and
  "redo" must not reapply the discarded three onto a now-divergent document.
- **composite()** (10.3) groups several separate record_command calls into
  one undoable action, for multi-step operations like auto-layout. A single
  record_command already handles multiple nodes on its own; this is for the
  different case of several distinct calls that should read as one action.
- **_guard_live_runs** (10.4) refuses an undo that would touch a node with an
  in-flight agent run - restoring a pre-run snapshot underneath a writing
  agent would leave state neither party asked for. Cancel first, then undo.
- **undo_run()** (10.5) reverses a whole agent run's commands as one action,
  keyed on `Command.run_id`.

Undo history is session-scoped and in-memory, bounded at 100, and cleared on
session load - it is an interaction convenience, deliberately distinct from
ADR-009's on-disk backups, which are the actual durability control.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, TypeVar

from backend.domain.node_states import PlanState

if TYPE_CHECKING:
    from backend.domain.model import SceneEdge, SceneNode
    from graphlink_navigation_pins import NavigationPinRecord

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
    # ADR-010 close-out: navigation pins live in a SEPARATE store
    # (SceneDocument.pins, a NavigationPinStore) - not self.nodes/self.edges,
    # so they need their own snapshot shape, not another dict-by-id like the
    # fields above. NavigationPinRecord is a FROZEN dataclass and
    # NavigationPinStore.records already returns an immutable tuple
    # snapshot, so - unlike nodes/edges, which need copy.deepcopy because
    # they're mutated in place - a held reference IS already a safe
    # point-in-time snapshot; no deep copy needed anywhere in this module
    # for pins. None means "this command never touched the pin store"
    # (the same "None vs empty" distinction is_noop relies on for the dicts
    # above); a non-None value - including an empty tuple, e.g. after
    # deleting the last pin - means "watched, this is what it was."
    pin_before: "tuple[NavigationPinRecord, ...] | None" = None
    pin_after: "tuple[NavigationPinRecord, ...] | None" = None
    # ADR-010 stage 10.5: the agent run this command belongs to, or None for
    # a direct user action. Set for every mutation an agent run produces, so
    # "undo this build" can revert a whole run's worth of commands as a unit
    # rather than making the user press Ctrl+Z once per node the agent made.
    run_id: str | None = None

    @property
    def label(self) -> str:
        """The human-readable action name the undo affordance shows ("Undo
        Delete", "Redo Move"). Derived from command_type rather than stored
        separately so it can never drift from what the command actually is."""
        return _COMMAND_LABELS.get(self.command_type, _humanize(self.command_type))

    @property
    def touched_node_ids(self) -> set[str]:
        """Every node id this command would create, delete or modify in
        either direction - what stage 10.4's live-run refusal checks against
        (you cannot undo across a node that is mid-generation)."""
        return set(self.node_before) | set(self.node_after)

    @property
    def is_noop(self) -> bool:
        """True when the mutator this command wrapped touched nothing that
        survived the diff (id.e. every explicitly/defensively watched id
        came out byte-identical to how it went in) - the idempotent-
        connect() case is the motivating example, but any accidental no-op
        call hits this the same way."""
        return not (
            self.node_before or self.node_after or self.edge_before or self.edge_after
        ) and (self.pin_before is None or self.pin_before == self.pin_after)

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
        if self.pin_before is not None:
            # reset() replaces the WHOLE store, unlike _restore's per-key
            # dict surgery - correct here because the pin store is always
            # snapshotted as one unit (see record_command's own doc for why
            # per-id scoping doesn't apply to pins the way it does nodes).
            document.pins.reset(list(self.pin_before))

    def apply(self, document: object) -> None:
        """The mirror of invert() - restores to the *_after state. Not
        needed for a first-time forward mutation (that already happened
        for real before this Command was constructed); this exists for
        REDO, applying a previously-inverted command a second time."""
        _restore(document.nodes, self.node_after)
        _restore(document.edges, self.edge_after)
        _restore(document.image_assets, self.asset_after)
        if self.pin_after is not None:
            document.pins.reset(list(self.pin_after))


def _restore(live: dict, snapshot: dict) -> None:
    for key, value in snapshot.items():
        if value is None:
            live.pop(key, None)
        else:
            restored = copy.deepcopy(value)
            # ADR-006 stage 6.4 review fix (HIGH): pending_request_id is a
            # VOLATILE in-flight marker, not document state - snapshots taken
            # while a reply command was being recorded (on_end runs in
            # _dispatch's finally, AFTER on_reply) captured the live request
            # id, and restoring it would resurrect a phantom "generating"
            # state: the frontend keys live-stream rendering on it (blank
            # node, frames never arrive) and _guard_live_runs would then
            # refuse further undo/redo for a run that no longer exists.
            # Always None is correct here: _guard_live_runs has already
            # refused the undo/redo if a REAL run is live on this node.
            if hasattr(restored, "pending_request_id"):
                restored.pending_request_id = None
            # review-fix: the same phantom-state hazard applies to a plan
            # node's builder_status. A builderReplan/builderPlan command
            # recorded MID-RUN snapshots the node while builder_status was
            # a run-owned value ("running"/"planning"/"awaiting_approval").
            # By the same _guard_live_runs guarantee above (pending_
            # request_id already proven not-live on the CURRENT node before
            # any undo/redo of a plan node reaches here), that run has
            # necessarily already landed a terminal/paused status for real -
            # restoring the snapshot's stale run-owned value would render
            # the node permanently "Building..."/"Waiting for approval"
            # with no live run behind it and no button that can advance it.
            # Normalize to "interrupted", the same resume-safe landing
            # session reload already uses for exactly this situation.
            if isinstance(getattr(restored, "state", None), PlanState) and restored.state.builder_status in (
                "running", "planning", "awaiting_approval",
            ):
                restored.state.builder_status = "interrupted"
                restored.state.builder_status_detail = (
                    "Restored from undo/redo history - resume to continue."
                )
                restored.state.builder_awaiting_tool_approval = False
                restored.state.builder_approval_tool_name = ""
                restored.state.builder_approval_summary = ""
            # review-fix (stage 8.7): builder_activity is documented as run
            # TELEMETRY, not reversible document content (PlanState's own
            # docstring: "deliberately left untouched by an undo, so the
            # record of what happened survives reverting what happened") -
            # unlike plan_steps, which genuinely IS reversible content
            # (scene/setPlanSteps is A-classified for exactly that reason).
            # A command recorded MID-RUN (builderReplan fires on every
            # builder.replan call, not just once at the run's start)
            # snapshots the node with whatever activity existed at THAT
            # instant; restoring that snapshot verbatim would silently erase
            # every row logged afterward the moment the command is inverted
            # - the same phantom-state class of hazard pending_request_id's
            # unconditional reset above already guards against, just for a
            # different field. Carries the CURRENT node's activity log
            # forward instead of trusting the snapshot's stale copy - `live`
            # still holds the pre-restore node at this point, one line
            # before it is replaced. A node that does not currently exist
            # (this restore is recreating a deleted one) has no "current" to
            # preserve, so the snapshot's own activity is used as-is - the
            # only case where trusting the snapshot is correct.
            current = live.get(key)
            if isinstance(getattr(restored, "state", None), PlanState) and isinstance(
                getattr(current, "state", None), PlanState,
            ):
                restored.state.builder_activity = current.state.builder_activity
            live[key] = restored


# The user-facing action names. Deliberately phrased as what the USER did
# ("Delete", "Move") rather than the intent name ("removeNodes"), since this
# text lands directly in "Undo Delete" on a button and in its tooltip.
_COMMAND_LABELS = {
    "addNode": "Add Node",
    "addChatNode": "Add Chat Node",
    "addCodeNode": "Add Code Node",
    "addDocumentNode": "Add Document",
    "addThinkingNode": "Add Thinking Node",
    "addHtmlNode": "Add HTML Node",
    "addImageNode": "Add Image",
    "addConversationNode": "Add Conversation",
    "addNote": "Add Note",
    "removeNodes": "Delete",
    "deleteChatNode": "Delete Message",
    "deleteConversationMessage": "Delete Message",
    "moveNode": "Move",
    "moveNodes": "Move",
    "organizeNodes": "Organize Nodes",
    "connectNodes": "Connect",
    "removeEdges": "Disconnect",
    "createFrame": "Create Frame",
    "createContainer": "Create Container",
    "ungroup": "Ungroup",
    "sendMessage": "Send Message",
    "chatReply": "Assistant Reply",
    "regenerateResponse": "Regenerate",
    "generateChart": "Generate Chart",
    "generateNote": "Generate Note",
    "compareBranches": "Compare Branches",
    "synthesizeBranches": "Synthesize Branches",
    "generateImageReply": "Generate Image",
    "setNoteContent": "Edit Note",
    "setGroupLabel": "Rename Group",
    "setGroupColor": "Recolor",
    "toggleFrameLock": "Lock Frame",
    "toggleGroupCollapsed": "Collapse Group",
    "resizeFrame": "Resize Frame",
    "fitFrameToContent": "Fit Frame",
    "resizeChart": "Resize Chart",
    "toggleChartAspectLock": "Chart Aspect Lock",
    "setChatCollapsed": "Collapse",
    "collapseAllNodes": "Collapse All",
    "expandAllNodes": "Expand All",
    "setNodeDocked": "Dock",
    "collapseBranch": "Collapse Branch",
    "setBranchStatus": "Set Status",
    "setFinalDeliverable": "Mark Final",
    "addPin": "Add Pin",
    "removePin": "Remove Pin",
    "updatePin": "Edit Pin",
    "movePin": "Move Pin",
    # ADR-010 close-out: the 7 List-A intents that had no reserved label
    # (the 20 above were pre-reserved during stage 10.1's own recon, a
    # direct code-level signal they were already scoped as List A and only
    # pending the wrap - these 7 were genuine gaps the close-out recon
    # found, not pre-flagged).
    "sendConversationMessage": "Send Message",
    "appendConversationAssistantMessage": "Assistant Reply",
    "sendArtifactMessage": "Send Instruction",
    "completeArtifactGeneration": "Artifact Reply",
    "setPyCoderMode": "Set Mode",
    "setCodeSandboxRequirements": "Edit Requirements",
    "setGitlinkLocalRoot": "Set Local Folder",
}


def _humanize(command_type: str) -> str:
    """Fallback label for a command_type with no explicit entry above -
    "setSomeThing" -> "Set Some Thing". Keeps a newly added command from
    ever showing a raw camelCase identifier in the UI, even if whoever
    added it forgot the label table."""
    out = []
    for index, char in enumerate(command_type):
        if char.isupper() and index > 0:
            out.append(" ")
        out.append(char.upper() if index == 0 else char)
    return "".join(out)


class UndoRefusedError(Exception):
    """ADR-010 stage 10.4: raised when an undo/redo is refused rather than
    failed - carries a message meant to be shown to the user verbatim."""


def _merge_commands(command_type, provenance, commands, run_id=None):
    """ADR-010 stage 10.3: folds a composite's buffered commands into one.

    Merge order matters and is asymmetric: for the BEFORE state the FIRST
    write wins (the earliest snapshot is the true "before the whole group"),
    while for the AFTER state the LAST write wins (the final value is what
    the group ended up producing). Getting this backwards would make undo
    restore an intermediate state rather than the state before the action.

    `run_id` (ADR-008): the composite's own run attribution. Applied to the
    single-command passthrough as well as the merged case - a composite
    that happened to buffer exactly one real command must still come out
    run-stamped, or undo_run would skip that step of a build.
    """
    real = [c for c in commands if not c.is_noop]
    if not real:
        return None
    if len(real) == 1:
        if run_id and real[0].run_id is None:
            real[0].run_id = run_id
        return real[0]

    merged = Command(
        command_type=command_type,
        provenance=provenance,
        run_id=run_id or next((c.run_id for c in real if c.run_id), None),
    )
    for command in real:
        for field_name in ("node_before", "edge_before", "asset_before"):
            target = getattr(merged, field_name)
            for key, value in getattr(command, field_name).items():
                target.setdefault(key, value)  # first write wins
        for field_name in ("node_after", "edge_after", "asset_after"):
            getattr(merged, field_name).update(getattr(command, field_name))  # last wins
        # Pins are a single whole-store value per command, not a dict to
        # union - "first write wins" for before/"last write wins" for after
        # means literally the first non-None pin_before seen and the last
        # non-None pin_after seen, same ordering rule as the dicts above.
        if command.pin_before is not None and merged.pin_before is None:
            merged.pin_before = command.pin_before
        if command.pin_after is not None:
            merged.pin_after = command.pin_after

    # An id created and then deleted inside the same group (or restored to
    # exactly what it was) nets out to no change at all - dropping those
    # keeps the merged command's own is_noop honest and avoids a pointless
    # dict entry that would restore a value to itself.
    for before_name, after_name in (
        ("node_before", "node_after"),
        ("edge_before", "edge_after"),
        ("asset_before", "asset_after"),
    ):
        before, after = getattr(merged, before_name), getattr(merged, after_name)
        for key in set(before) & set(after):
            if before[key] == after[key]:
                del before[key]
                del after[key]

    # Same net-zero cleanup for pins: if the store ended up byte-identical
    # to how it started across the whole group, there is nothing to record -
    # clearing both back to None keeps is_noop (and therefore whether this
    # composite gets logged at all) correct.
    if merged.pin_before is not None and merged.pin_before == merged.pin_after:
        merged.pin_before = None
        merged.pin_after = None
    return merged


class CommandOps:
    def record_command(
        self,
        command_type: str,
        provenance: str,
        mutator: Callable[[], T],
        *,
        node_ids: Iterable[str] = (),
        edge_ids: Iterable[str] = (),
        run_id: "str | None" = None,
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
        named node_ids that currently carry an image_asset_id -
        deliberately NOT defensive like the frame case, since asset bytes
        are the one genuinely expensive thing here and only a node's OWN
        delete evicts its OWN asset (see remove_nodes' - never a side
        effect of some other node's edit). Chart nodes carried a second
        such attr (chart_asset_id) before ADR-013 stage 13.4 retired their
        backend-rendered display PNG - see ChartState's own docstring.

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
            asset_id = getattr(node.state, "image_asset_id", None)
            if asset_id and asset_id in self.image_assets:
                asset_snapshot_before[asset_id] = self.image_assets[asset_id]

        node_ids_before = set(self.nodes.keys())
        edge_ids_before = set(self.edges.keys())
        asset_ids_before = set(self.image_assets.keys())
        # ADR-010 close-out: pins are ALWAYS defensively watched as one
        # whole-store unit, unconditionally, the same "always watch, no
        # opt-in flag needed" posture already used for frame/container nodes
        # above - not a per-id scope like node_ids/edge_ids, since the store
        # is small (a handful of waypoints, never hundreds) and cheap to
        # snapshot whole (NavigationPinStore.records is already an
        # immutable tuple, no deep copy required). This also means a
        # mutation NOT wrapped for pins simply never sees a diff here,
        # rather than needing its own opt-in parameter that could be
        # forgotten - the same reasoning record_command's own doc gives for
        # rejecting a `pin_ids` parameter.
        pin_snapshot_before = self.pins.records

        result = mutator()

        node_ids_after = set(self.nodes.keys())
        edge_ids_after = set(self.edges.keys())
        asset_ids_after = set(self.image_assets.keys())
        pin_snapshot_after = self.pins.records

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

        pin_before_out = pin_snapshot_before if pin_snapshot_after != pin_snapshot_before else None
        pin_after_out = pin_snapshot_after if pin_snapshot_after != pin_snapshot_before else None

        command = Command(
            command_type=command_type,
            provenance=provenance,
            node_before=node_before_out,
            node_after=node_after_out,
            edge_before=edge_before_out,
            edge_after=edge_after_out,
            asset_before=asset_before_out,
            asset_after=asset_after_out,
            pin_before=pin_before_out,
            pin_after=pin_after_out,
            run_id=run_id,
        )
        # A no-op command (an idempotent connect() that created nothing, a
        # setter called with the value it already had) is never logged -
        # undoing it would do nothing, so it would only ever consume one of
        # the bounded log's slots and make Ctrl+Z appear to "do nothing
        # once" before reaching the operation the user actually meant.
        if not command.is_noop:
            self._push_command(command)
        return result, command

    # -- ADR-010 stage 10.2/10.3/10.4/10.5: the undo/redo stack -------------

    def _push_command(self, command: "Command") -> None:
        """Adds a newly performed command to the undo stack.

        Doing anything new after undoing discards the redo branch - the
        standard, universally expected behavior (undo three things, type
        something, and "redo" must not reapply the discarded three on top of
        a now-divergent document). While an open composite is being built
        (see composite()), commands accumulate there instead and only reach
        the stack when the group closes."""
        if self._composite_depth > 0:
            self._composite_buffer.append(command)
            return
        self.redo_stack.clear()
        self.command_log.append(command)

    @contextmanager
    def composite(self, command_type: str, provenance: str = "user", *, run_id: "str | None" = None):
        """ADR-010 stage 10.3: groups every command recorded inside the block
        into ONE undoable action.

        Needed for multi-step actions whose individual mutations are separate
        record_command calls - the canonical case being auto-layout
        (organizeNodes moves every node in the scene) and a multi-select
        delete. One Ctrl+Z must reverse the whole logical action, not peel it
        apart one node at a time.

        Note that a SINGLE record_command call already covers multiple nodes
        fine - its before/after diff is naturally n-ary. This exists for the
        different case of several SEPARATE record_command calls that should
        read as one action. Re-entrant: nesting composites flattens into the
        outermost one, so a helper that opens its own composite does not
        fragment its caller's."""
        self._composite_depth += 1
        outermost = self._composite_depth == 1
        try:
            yield
        except BaseException:
            # REVIEW-FIX: this block used to run its merge-and-push logic
            # unconditionally from a `finally`, with no `except` at all - a
            # mutator raising partway through the with-block (record_command's
            # own AssertionError for an unanticipated deletion, or any
            # exception from the wrapped domain call) still fell through to
            # the same commit path, silently pushing whatever had been
            # buffered SO FAR as if it were the complete action. That's a
            # real, already-applied partial document mutation landing on the
            # undo stack, while the caller's own exception handler has no
            # reason to call publish_scene() for a group it believes never
            # completed - the same live-document-diverges-from-clients shape
            # undo_run's own REVIEW-FIX below already fixed for the reverse
            # (undo) direction. Discard the buffered commands instead of
            # merging/pushing them: a mid-block exception must mean the
            # group never happened, matching what the caller already
            # assumes when its own try/except catches this and never
            # republishes.
            if outermost:
                self._composite_buffer.clear()
            raise
        else:
            if outermost:
                buffered = list(self._composite_buffer)
                self._composite_buffer.clear()
                merged = _merge_commands(command_type, provenance, buffered, run_id=run_id)
                # is_noop is re-checked AFTER merging, not just on the inputs:
                # a group whose steps cancel out (create a node then delete it
                # inside the same composite) has real non-noop members but
                # nets to nothing, and pushing that would put a do-nothing
                # entry on the stack that eats one Ctrl+Z silently.
                if merged is not None and not merged.is_noop:
                    self.redo_stack.clear()
                    self.command_log.append(merged)
        finally:
            self._composite_depth -= 1

    def can_undo(self) -> bool:
        return len(self.command_log) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def undo_label(self) -> str:
        return self.command_log[-1].label if self.command_log else ""

    def redo_label(self) -> str:
        return self.redo_stack[-1].label if self.redo_stack else ""

    def _guard_live_runs(self, command: "Command", verb: str = "undo") -> None:
        """ADR-010 stage 10.4: refuse to undo/redo across a node with a live
        run. A node mid-generation has an in-flight agent writing to it;
        restoring a snapshot from before that run started would race the
        write and leave the node in a state neither the user nor the agent
        asked for. The ADR's own guardrail - cancel first, then undo.

        `verb` (REVIEW-FIX): the two UndoRefusedError messages below used to
        be hard-coded with "undo" wording, so a refusal from redo() - which
        calls this same guard - told the user "Can't undo..." for an action
        they never asked to undo. intents_undo.py's redo handler passes
        str(exc) straight to the notification banner (UndoRefusedError's own
        docstring: shown "verbatim"), so the wrong verb reached the UI on
        every refused redo. undo() and undo_run() keep the default "undo";
        redo() passes "redo" so the same guard reads correctly from either
        direction."""
        for node_id in command.touched_node_ids:
            node = self.nodes.get(node_id)
            if node is not None and node.pending_request_id:
                raise UndoRefusedError(
                    f"Can't {verb} while \"{node.title}\" is still generating - "
                    "cancel it first."
                )
        # REVIEW-FIX: the loop above only catches a live run whose OWN
        # busy marker sits on one of THIS command's touched nodes. A
        # multi-step Builder run (backend/builder.py) marks
        # pending_request_id on the PLAN node only, for the run's whole
        # duration - never on the individual nodes its own per-step tool
        # calls create/edit/connect (backend/tools_graph.py's handlers
        # thread the SAME run_id into record_command but touch only the
        # content node(s), never the plan node). So undoing an earlier
        # step of a still-running build - whose own touched node is never
        # the plan node - sailed through completely unguarded while the
        # run kept writing more state on top of the now-vanished node.
        # Cross-check the RUN itself, not just this command's own nodes:
        # if this command belongs to a run (command.run_id) and that run's
        # own plan node is still pending, refuse regardless of which node
        # this particular command touched. Stays pure (no dispatcher/
        # RunRegistry access, only node state already available here) by
        # reading node.state.builder_run_id, the SAME value builder.py
        # stamps onto the plan node with the identical run_id it threads
        # into every tool-call command.
        if command.run_id:
            for node in self.nodes.values():
                if (
                    getattr(node.state, "builder_run_id", None) == command.run_id
                    and node.pending_request_id
                ):
                    raise UndoRefusedError(
                        f"Can't {verb} a step from a build that is still running - "
                        "stop it first."
                    )

    def undo(self) -> "Command":
        """Reverses the most recent command and moves it onto the redo stack.
        Raises UndoRefusedError if there is nothing to undo, or if the
        command touches a node with a live run."""
        if not self.command_log:
            raise UndoRefusedError("Nothing to undo.")
        command = self.command_log[-1]
        self._guard_live_runs(command)
        command.invert(self)
        self.command_log.pop()
        self.redo_stack.append(command)
        return command

    def redo(self) -> "Command":
        """Re-applies the most recently undone command."""
        if not self.redo_stack:
            raise UndoRefusedError("Nothing to redo.")
        command = self.redo_stack[-1]
        self._guard_live_runs(command, "redo")  # REVIEW-FIX: redo-appropriate wording
        command.apply(self)
        self.redo_stack.pop()
        self.command_log.append(command)
        return command

    def undo_run(self, run_id: str) -> int:
        """ADR-010 stage 10.5: reverses every command belonging to one agent
        run, newest first, as a single user-facing action ("undo this
        build"). Returns how many commands were reversed.

        Only reverses commands at the TOP of the stack that belong to the
        run: if the user has since made their own edits on top, those are
        not silently discarded to reach the agent's work underneath -
        undoing a build has to mean undoing the build, not everything after
        it too. Stops at the first command that is not part of the run."""
        undone: list["Command"] = []
        try:
            while self.command_log and self.command_log[-1].run_id == run_id:
                command = self.command_log[-1]
                self._guard_live_runs(command)
                command.invert(self)
                self.command_log.pop()
                self.redo_stack.append(command)
                undone.append(command)
        except UndoRefusedError:
            # REVIEW-FIX: this loop is not atomic by construction - undoing
            # command N here already applies a REAL document mutation
            # (deleted nodes, popped edges) before an OLDER, still-refused
            # command is even reached. Without this rollback, a refusal
            # partway through left every already-undone command's mutation
            # applied to the live document with no scene republish -
            # intents_undo.py's own undo_run wrapper shows a notification
            # on UndoRefusedError and returns without ever calling
            # publish_scene(), so the backend document and every connected
            # client's canvas silently diverged (proven directly: 2 of 3
            # commands genuinely removed from document.nodes while only a
            # "notification" topic message ever went out).
            #
            # Re-apply exactly what this call itself just undid, in
            # reverse order, restoring the document to precisely the state
            # it was in before this call started - a refusal is now
            # observably a NO-OP, matching what the caller already assumes
            # (return 0, nothing published).
            #
            # Deliberately bypasses the public undo()/redo() (and their
            # own _guard_live_runs call) for this rollback: these commands
            # were already proven safe to touch a moment ago (undone in
            # THIS same call), and if the refused run is genuinely still
            # live, the guard above would refuse re-applying them too -
            # making the rollback itself undoable and leaving no way back
            # to a consistent state at all.
            for command in reversed(undone):
                command.apply(self)
                self.redo_stack.pop()
                self.command_log.append(command)
            raise
        return len(undone)
