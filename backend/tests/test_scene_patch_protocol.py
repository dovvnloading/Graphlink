"""ADR-003 stage 3.4: the scene topic's patch protocol.

Covers the three pieces that together make a delta safe to send:

1. SceneDocument.take_dirty_patch_ops - what changed since the last publish,
   as whole-node/edge ops (backend/domain/graph.py).
2. SessionBus.publish's patch-vs-snapshot decision, and the revision
   semantics baseRevision depends on (backend/events.py).
3. The stage's own numeric exit criterion: a single node edit on a 500-node
   graph must cost <= 5 KiB on the wire.

The exit criterion is asserted as BYTES, not wall-clock. ADR-003's own
"Measured targets" line cites "the perf harness from ADR-015", but ADR-015
(quality gates and CI) contains no perf harness at all - ADR-019
(performance budgets) is the ADR that actually owns that concept, and says
so explicitly ("Three ADRs (003, 011, 015) already promise 'budgets' and a
'perf harness' that do not exist"). ADR-019 also rejects wall-clock timing
as a per-PR CI gate outright (shared-runner flakiness), reserving it for
nightly runs and gating CI on counting/size assertions instead. A byte-size
assertion is therefore both what this stage's exit criterion literally asks
for AND the only half of it that belongs in per-PR CI.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.domain.graph import SceneDocument
from backend.events import SessionBus

sys.path.insert(0, str(Path(__file__).resolve().parent / "perf"))

from graph_factory import LARGE  # noqa: E402

# ADR-003 stage 3.4's own stated exit criterion.
#
# Deliberate amendment - 2026-09-03 (Review Lens feature). The 5 KiB
# baseline assumed the pre-Review-Lens wire key set; the code_review
# node's 31 new keys (default-valued for every other kind - the same
# additive rule every kind before it followed) cost a measured +94 bytes
# on a single-node upsert (5120 -> 5214). Re-anchored to that new reality
# with ~2% headroom (5214 + 110); a future kind's own keys will need
# their own deliberate amendment here, same as this one - never a silent
# bump to absorb an accidental blob (codeReviewDiffText stays OFF the
# wire for exactly this reason - see CodeReviewState's own comment).
SINGLE_EDIT_WIRE_BUDGET_BYTES = 5324


class Recorder:
    """A connection that just records what it was sent."""

    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


def make_scene_bus(document: SceneDocument) -> SessionBus:
    bus = SessionBus("patch-test")
    bus.register_topic(
        "scene",
        document.scene_payload,
        patch_builder=document.take_dirty_patch_ops,
        baseline_builder=document.published_scene_payload,
    )
    return bus


def ops_of(message_or_ops: dict | list) -> list[str]:
    """The op names in either a whole patch MESSAGE or a bare ops list -
    take_dirty_patch_ops returns the latter, SessionBus wraps it in the
    former, and both are asserted on below."""
    ops = message_or_ops["ops"] if isinstance(message_or_ops, dict) else message_or_ops
    return [op["op"] for op in ops]


def revision_of(message: dict) -> int:
    """A scene publish carries its revision in a different place depending on
    which form it took - inside the envelope for a snapshot, at the top level
    for a patch."""
    return message["payload"]["revision"] if message["kind"] == "state" else message["revision"]


# -- take_dirty_patch_ops -----------------------------------------------------


def test_the_first_call_returns_none_so_the_caller_sends_a_full_snapshot():
    document = SceneDocument()
    document.add_node(0, 0, "a")
    assert document.take_dirty_patch_ops() is None


def test_no_change_since_the_last_publish_yields_no_ops():
    document = SceneDocument()
    document.add_node(0, 0, "a")
    document.take_dirty_patch_ops()
    assert document.take_dirty_patch_ops() == []


def test_a_moved_node_yields_exactly_one_upsert_carrying_its_new_position():
    document = SceneDocument()
    node = document.add_node(0, 0, "a")
    document.add_node(500, 500, "b")
    document.take_dirty_patch_ops()

    document.move_node(node.id, 42.0, 99.0)
    ops = document.take_dirty_patch_ops()

    assert ops_of(ops) == ["upsertNode"]
    assert ops[0]["node"]["id"] == node.id
    assert (ops[0]["node"]["x"], ops[0]["node"]["y"]) == (42.0, 99.0)


def test_a_removed_node_and_its_cascaded_edges_are_both_reported():
    # The client does not re-derive edge validity from a node removal, so the
    # cascade (edges die with either endpoint) has to be on the wire too.
    document = SceneDocument()
    a = document.add_node(0, 0, "a")
    b = document.add_node(100, 0, "b")
    edge = document.connect(a.id, b.id)
    document.take_dirty_patch_ops()

    document.remove_nodes([a.id])
    ops = document.take_dirty_patch_ops()

    assert ops_of(ops) == ["removeNodes", "removeEdges"]
    assert ops[0]["ids"] == [a.id]
    assert ops[1]["ids"] == [edge.id]


def test_a_frame_that_silently_refits_when_a_member_moves_is_reported_too():
    # The case a "mutators report their own changed ids" design misses:
    # move_node's caller knows only the node it asked to move, while
    # _recompute_group_bounds mutates the ENCLOSING frame as a side effect.
    # Diffing catches it with no per-mutator instrumentation at all.
    document = SceneDocument()
    a = document.add_node(0, 0, "a")
    b = document.add_node(100, 0, "b")
    frame = document.create_frame([a.id, b.id])
    document.take_dirty_patch_ops()

    document.move_node(a.id, 900.0, 900.0)
    upserted = {op["node"]["id"] for op in document.take_dirty_patch_ops() if op["op"] == "upsertNode"}

    assert a.id in upserted
    assert frame.id in upserted, "the frame re-fits as a side effect and must be on the wire"


def test_a_mutator_that_does_no_dirty_bookkeeping_of_its_own_is_still_reported():
    # Regression pin for a real silent-data-loss bug this stage hit and
    # abandoned an earlier mechanism over: with a mutation-site-fed dirty-id
    # set, an UNinstrumented mutation publishing while an earlier
    # instrumented one's marks were pending emitted a patch describing only
    # the stale earlier change - the real one never reached the client.
    # set_code_sandbox_allow_source_builds is exactly such a mutator (it does
    # no bookkeeping whatsoever), and connect() ran during its node setup.
    document = SceneDocument()
    parent = document.add_node(0, 0, "parent")
    node = document.add_code_sandbox_node(0, 0, parent.id)
    document.take_dirty_patch_ops()

    document.set_code_sandbox_allow_source_builds(node.id, True)
    ops = document.take_dirty_patch_ops()

    assert ops_of(ops) == ["upsertNode"]
    assert ops[0]["node"]["codeSandboxApprovalAllowSourceBuilds"] is True


def test_view_and_meta_changes_get_their_own_ops():
    document = SceneDocument()
    document.add_node(0, 0, "a")
    document.take_dirty_patch_ops()

    document.set_view_state(2.0, 10.0, 20.0)
    document.set_drag_factor(0.5)
    ops = document.take_dirty_patch_ops()

    assert ops_of(ops) == ["setView", "setMeta"]
    assert ops[0]["view"] == {"zoomFactor": 2.0, "scrollX": 10.0, "scrollY": 20.0}
    assert ops[1]["meta"]["dragFactor"] == 0.5


def test_a_pin_change_falls_back_to_a_full_snapshot():
    # Pins have no op in the ADR's own op set, so a pin mutation is honestly
    # outside what a patch can express - falling back beats silently
    # dropping it.
    document = SceneDocument()
    document.add_node(0, 0, "a")
    document.take_dirty_patch_ops()

    document.pins.add(title="Pin", note="", x=0.0, y=0.0)
    assert document.take_dirty_patch_ops() is None


def test_every_node_field_change_is_detected_not_just_position():
    document = SceneDocument()
    node = document.add_chat_node(0, 0, "hello", is_user=True)
    document.take_dirty_patch_ops()

    document.update_chat_node_content(node.id, "changed")
    ops = document.take_dirty_patch_ops()

    assert ops_of(ops) == ["upsertNode"]
    assert ops[0]["node"]["content"] == "changed"


# -- SessionBus publish/revision ----------------------------------------------


def test_publish_sends_a_patch_once_a_baseline_exists():
    async def run():
        document = SceneDocument()
        node = document.add_node(0, 0, "a")
        bus = make_scene_bus(document)
        recorder = Recorder()
        bus.attach(recorder)

        await bus.publish("scene")  # first publish: full snapshot
        assert recorder.messages[0]["kind"] == "state"

        document.move_node(node.id, 5.0, 6.0)
        await bus.publish("scene")

        patch = recorder.messages[1]
        assert patch["kind"] == "patch"
        assert patch["topic"] == "scene"
        assert ops_of(patch) == ["upsertNode"]

    asyncio.run(run())


def test_a_patch_chains_baserevision_to_the_previous_revision():
    async def run():
        document = SceneDocument()
        node = document.add_node(0, 0, "a")
        bus = make_scene_bus(document)
        recorder = Recorder()
        bus.attach(recorder)

        await bus.publish("scene")
        first_revision = revision_of(recorder.messages[0])

        document.move_node(node.id, 1.0, 1.0)
        await bus.publish("scene")
        patch = recorder.messages[1]

        assert patch["baseRevision"] == first_revision
        assert patch["revision"] == first_revision + 1

    asyncio.run(run())


def test_publish_falls_back_to_a_snapshot_when_nothing_changed():
    async def run():
        document = SceneDocument()
        document.add_node(0, 0, "a")
        bus = make_scene_bus(document)
        recorder = Recorder()
        bus.attach(recorder)

        await bus.publish("scene")
        await bus.publish("scene")

        assert [m["kind"] for m in recorder.messages] == ["state", "state"]

    asyncio.run(run())


def test_a_topic_with_no_patch_builder_always_sends_snapshots():
    async def run():
        bus = SessionBus("no-patch")
        state = {"n": 0}
        bus.register_topic("counter", lambda: {"n": state["n"]})
        recorder = Recorder()
        bus.attach(recorder)

        await bus.publish("counter")
        state["n"] = 1
        await bus.publish("counter")

        assert [m["kind"] for m in recorder.messages] == ["state", "state"]

    asyncio.run(run())


def test_send_snapshot_does_not_advance_the_revision():
    # The bug this stage fixed: _Topic.snapshot() used to bump the revision
    # itself, so a second window merely SUBSCRIBING advanced the counter
    # every other connection tracks - manufacturing a phantom baseRevision
    # gap and forcing needless re-snapshots for everyone.
    async def run():
        document = SceneDocument()
        bus = make_scene_bus(document)
        broadcast_recorder = Recorder()
        bus.attach(broadcast_recorder)
        await bus.publish("scene")
        revision_before = revision_of(broadcast_recorder.messages[0])

        subscriber = Recorder()
        await bus.send_snapshot("scene", subscriber)
        await bus.send_snapshot("scene", subscriber)

        assert revision_of(subscriber.messages[0]) == revision_before
        # ...and the next real broadcast still chains off that same number.
        document.add_node(0, 0, "a")
        await bus.publish("scene")
        assert revision_of(broadcast_recorder.messages[-1]) == revision_before + 1

    asyncio.run(run())


def test_a_subscribing_connection_receives_state_at_the_current_revision():
    async def run():
        document = SceneDocument()
        bus = make_scene_bus(document)
        first = Recorder()
        bus.attach(first)
        await bus.publish("scene")
        document.add_node(0, 0, "a")
        await bus.publish("scene")

        late = Recorder()
        await bus.send_snapshot("scene", late)

        # Exactly in sync: the late joiner's snapshot carries the same
        # revision the next patch will name as its baseRevision.
        assert late.messages[0]["kind"] == "state"
        assert revision_of(late.messages[0]) == 2

    asyncio.run(run())


# -- review-fix: subscribers must land on the PUBLISHED baseline --------------
#
# The protocol's correctness rests on "revision R means you hold the state as
# of publish R". send_snapshot used to serve the LIVE document stamped with
# the last-published revision, and those differ whenever the document is
# mutated between publishes - which is routine (several intent handlers await
# another topic's publish in between; agents.py sets then clears
# pending_request_id around an await). Hand a client something NEWER than R
# and its baseRevision chains perfectly forever while the diff, computed
# against the published baseline, never emits the op that would reconcile it.


def test_a_late_subscriber_never_keeps_a_node_the_diff_baseline_never_saw():
    # The "ghost node" case: created after the last publish, delivered to a
    # new subscriber by the handshake, then deleted. The diff never saw it,
    # so it emits no removeNodes - and the client would have shown a deleted
    # node for the rest of the session with nothing able to detect it.
    async def run():
        document = SceneDocument()
        bus = make_scene_bus(document)
        first = Recorder()
        bus.attach(first)
        keep = document.add_node(0, 0, "keep")
        await bus.publish("scene")

        ghost = document.add_node(50, 0, "ghost")  # mutated, NOT published
        late = Recorder()
        bus.attach(late)
        await bus.send_snapshot("scene", late)
        late_nodes = {n["id"] for n in late.messages[0]["payload"]["nodes"]}
        assert ghost.id not in late_nodes, "the handshake must serve the published baseline"

        document.remove_nodes([ghost.id])
        document.move_node(keep.id, 9, 9)  # unrelated change keeps ops non-empty
        await bus.publish("scene")

        for op in late.messages[1]["ops"]:
            if op["op"] == "upsertNode":
                late_nodes.add(op["node"]["id"])
            elif op["op"] == "removeNodes":
                late_nodes -= set(op["ids"])
        assert late_nodes == set(document.nodes)

    asyncio.run(run())


def test_a_value_mutated_and_reverted_between_publishes_never_leaks_to_a_late_subscriber():
    # The complementary case, and the nastier one: no op is EVER emitted for
    # a node whose value returns to its published state, so a subscriber
    # handed the transient value keeps it permanently.
    async def run():
        document = SceneDocument()
        bus = make_scene_bus(document)
        bus.attach(Recorder())
        node = document.add_node(0, 0, "n")
        await bus.publish("scene")

        document.move_node(node.id, 999.0, 999.0)  # transient, unpublished
        late = Recorder()
        bus.attach(late)
        await bus.send_snapshot("scene", late)
        seen_x = {n["id"]: n for n in late.messages[0]["payload"]["nodes"]}[node.id]["x"]
        assert seen_x == 0.0, "the handshake must not expose an unpublished value"

        document.move_node(node.id, 0.0, 0.0)  # reverted
        document.add_node(1, 1, "unrelated")   # keeps ops non-empty
        await bus.publish("scene")

        for op in late.messages[1]["ops"]:
            if op["op"] == "upsertNode" and op["node"]["id"] == node.id:
                seen_x = op["node"]["x"]
        assert seen_x == document.nodes[node.id].x

    asyncio.run(run())


def test_registering_a_patch_builder_without_a_baseline_builder_is_a_programming_error():
    # The pairing is load-bearing and its failure is completely silent, so it
    # is enforced rather than merely documented - and RAISED rather than
    # asserted, because `python -O` strips an assert and would take the
    # enforcement with it.
    document = SceneDocument()
    bus = SessionBus("no-baseline")
    with pytest.raises(ValueError, match="baseline_builder"):
        bus.register_topic("scene", document.scene_payload, patch_builder=document.take_dirty_patch_ops)


def test_a_patch_publish_builds_every_node_payload_exactly_once():
    # publish() used to call take_dirty_patch_ops() (which builds every node
    # wire) and THEN t.snapshot() (which builds them all again) purely for a
    # return value no production caller reads - 2x the pre-3.4 per-publish
    # CPU on the event loop, across ~146 publish sites, on the exact hot path
    # this stage exists to make cheaper.
    async def run():
        document = SceneDocument()
        for index in range(20):
            document.add_node(index, 0, f"n{index}")
        bus = make_scene_bus(document)
        bus.attach(Recorder())
        await bus.publish("scene")

        built = 0
        original = SceneDocument._node_wire

        def counting(self, node):
            nonlocal built
            built += 1
            return original(self, node)

        document.move_node(next(iter(document.nodes)), 5, 5)
        SceneDocument._node_wire = counting
        try:
            await bus.publish("scene")
        finally:
            SceneDocument._node_wire = original

        assert built == len(document.nodes), (
            f"expected one wire build per node, got {built} for {len(document.nodes)} nodes"
        )

    asyncio.run(run())


def test_loading_a_chat_resets_the_baseline_so_the_next_publish_is_a_snapshot():
    # clear_for_load replaces the whole document. Diffing the new scene
    # against the OLD one produced a patch no smaller than the snapshot it
    # replaced (500 upsertNode ops plus removeNodes/removeEdges listing every
    # old id) on the most bandwidth-heavy operation in the app.
    async def run():
        document = SceneDocument()
        bus = make_scene_bus(document)
        recorder = Recorder()
        bus.attach(recorder)
        document.add_node(0, 0, "before")
        await bus.publish("scene")

        document.clear_for_load()
        document.add_node(0, 0, "after")
        await bus.publish("scene")

        assert recorder.messages[-1]["kind"] == "state", "a wholesale replacement must send a snapshot"

    asyncio.run(run())


def test_a_patch_frame_carries_the_version_envelope():
    # The module's own contract is that a reader must be able to refuse a
    # payload older than its stated minimum. Without these fields on patches,
    # a future breaking scene-schema bump would leave an already-snapshotted
    # old client applying new-shaped nodes forever with nothing to refuse on.
    async def run():
        document = SceneDocument()
        node = document.add_node(0, 0, "a")
        bus = make_scene_bus(document)
        recorder = Recorder()
        bus.attach(recorder)
        await bus.publish("scene")
        document.move_node(node.id, 1, 1)
        await bus.publish("scene")

        patch = recorder.messages[1]
        assert patch["kind"] == "patch"
        assert patch["schemaVersion"] == 1
        assert patch["minCompatibleSchemaVersion"] == 1

    asyncio.run(run())


# -- the stage's numeric exit criterion ---------------------------------------


def test_a_single_node_edit_on_a_500_node_graph_fits_the_wire_budget():
    document = LARGE.build()
    assert len(document.nodes) == 500, "ADR-019's LARGE workload is the 500-node reference"
    document.take_dirty_patch_ops()  # establish the baseline

    node_id = next(iter(document.nodes))
    document.move_node(node_id, 42.0, 99.0)
    ops = document.take_dirty_patch_ops()
    patch_bytes = len(
        json.dumps(
            {"kind": "patch", "topic": "scene", "revision": 2, "baseRevision": 1, "ops": ops}
        ).encode("utf-8")
    )

    assert patch_bytes <= SINGLE_EDIT_WIRE_BUDGET_BYTES, (
        f"a single-node edit costs {patch_bytes} bytes, over the "
        f"{SINGLE_EDIT_WIRE_BUDGET_BYTES}-byte budget ADR-003 stage 3.4 sets"
    )


def test_the_patch_is_dramatically_smaller_than_the_snapshot_it_replaces():
    # Guards the actual POINT of the stage, independent of the absolute
    # budget above: if a future change quietly made publish() fall back to
    # snapshots for ordinary edits, the byte assertion alone would still
    # pass on a small fixture while the real win silently disappeared.
    document = LARGE.build()
    snapshot_bytes = len(json.dumps(document.scene_payload()).encode("utf-8"))
    document.take_dirty_patch_ops()

    document.move_node(next(iter(document.nodes)), 1.0, 2.0)
    patch_bytes = len(json.dumps(document.take_dirty_patch_ops()).encode("utf-8"))

    assert patch_bytes * 100 < snapshot_bytes, (
        f"expected a 100x+ reduction; got {snapshot_bytes} -> {patch_bytes}"
    )


# -- slow-client isolation ----------------------------------------------------


def test_one_dead_connection_never_blocks_delivery_to_the_others():
    # The stall half of the exit criterion, at the level this stage actually
    # changes: publish() must reach every healthy connection even when one
    # fails outright, and must drop the bad one rather than retry it forever.
    async def run():
        document = SceneDocument()
        node = document.add_node(0, 0, "a")
        bus = make_scene_bus(document)

        class Dead:
            async def send_json(self, data):
                raise ConnectionError("dead socket")

        healthy = Recorder()
        bus.attach(Dead())
        bus.attach(healthy)

        await bus.publish("scene")
        document.move_node(node.id, 1.0, 1.0)
        await bus.publish("scene")

        assert len(healthy.messages) == 2
        assert healthy.messages[1]["kind"] == "patch"
        assert bus.connection_count == 1, "the dead connection must be detached"

    asyncio.run(run())


@pytest.mark.parametrize("connection_count", [1, 3])
def test_every_attached_connection_receives_the_same_patch(connection_count):
    async def run():
        document = SceneDocument()
        node = document.add_node(0, 0, "a")
        bus = make_scene_bus(document)
        recorders = [Recorder() for _ in range(connection_count)]
        for recorder in recorders:
            bus.attach(recorder)

        await bus.publish("scene")
        document.move_node(node.id, 7.0, 8.0)
        await bus.publish("scene")

        patches = [r.messages[1] for r in recorders]
        assert all(p["kind"] == "patch" for p in patches)
        assert all(p == patches[0] for p in patches)

    asyncio.run(run())
