"""ADR-003 stage 3.4 follow-on: publish coalescing + per-connection backpressure.

Stage 3.4 shipped the scene patch protocol but explicitly deferred (and
disclosed as not-done) the two pieces this file covers:

1. A ~16 ms per-session coalescer, so a burst of same-topic publishes ships
   ONE outbound message instead of one per mutation.
2. A bounded per-connection writer queue, so a slow reader cannot stall
   delivery to the other connections or block the coroutine that published.

Both are OPT-IN (`SessionBus(coalesce_window_seconds=...)` and
`attach(buffered=True)`) rather than default-on, because unbuffered,
uncoalesced publishes complete synchronously before `publish()` returns -
which is exactly what makes `await bus.publish(...); assert recorder.messages`
deterministic for the ~1600 tests that already do it. backend/app.py opts the
real shipped bus into both.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.domain.graph import SceneDocument
from backend.events import (
    DEFAULT_COALESCE_WINDOW_SECONDS,
    DEFAULT_SEND_QUEUE_MAXSIZE,
    EventBus,
    SessionBus,
    UnknownTopicError,
)


class Recorder:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


class SlowRecorder:
    """A reader that applies real backpressure - the whole point of the
    writer queue is that this can no longer hold anyone else up."""

    def __init__(self, delay: float = 0.2):
        self.messages: list[dict] = []
        self.delay = delay

    async def send_json(self, data: dict) -> None:
        await asyncio.sleep(self.delay)
        self.messages.append(data)


class BlockedRecorder:
    """Never finishes a send. Models a client whose socket buffer is full."""

    def __init__(self):
        self.started = 0
        self.release = asyncio.Event()

    async def send_json(self, data: dict) -> None:
        self.started += 1
        await self.release.wait()


def make_bus(document: SceneDocument, **kwargs) -> SessionBus:
    bus = SessionBus("coalesce-test", **kwargs)
    bus.register_topic(
        "scene",
        document.scene_payload,
        patch_builder=document.take_dirty_patch_ops,
        baseline_builder=document.published_scene_payload,
    )
    return bus


# -- backpressure isolation ---------------------------------------------------


def test_a_slow_client_no_longer_blocks_the_publishing_coroutine():
    # The stall half of stage 3.4's exit criterion. _broadcast used to
    # `await conn.send_json(...)` inline per connection, so one slow socket
    # held up the publisher - an agent run, or the WS receive loop itself.
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        slow = SlowRecorder(delay=0.2)
        bus.attach(slow, buffered=True)
        node = document.add_node(0, 0, "a")
        await bus.publish("scene")
        document.move_node(node.id, 1, 1)

        started = time.monotonic()
        await bus.publish("scene")
        blocked_for = time.monotonic() - started

        assert blocked_for < 0.05, (
            f"publish blocked {blocked_for:.3f}s on a slow reader; the writer "
            f"queue exists so it should return immediately"
        )
        # And the message really is delivered, just on the writer's own time.
        await asyncio.sleep(0.5)
        assert len(slow.messages) == 2

    asyncio.run(run())


def test_a_slow_client_no_longer_delays_a_fast_one():
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        slow, fast = SlowRecorder(delay=0.3), Recorder()
        bus.attach(slow, buffered=True)
        bus.attach(fast, buffered=True)
        document.add_node(0, 0, "a")

        await bus.publish("scene")
        await asyncio.sleep(0.05)  # far less than the slow client's 0.3s

        assert len(fast.messages) == 1, "the fast client must not wait behind the slow one"
        assert len(slow.messages) == 0, "...and the slow one is genuinely still mid-send"

    asyncio.run(run())


def test_an_overflowing_queue_drops_rather_than_blocking_or_growing():
    # Dropping is safe BECAUSE of the patch protocol: every patch carries
    # baseRevision, so a dropped frame makes the next one fail the client's
    # gap check, which already triggers a re-snapshot. No new wire kind and
    # no "you are stale" signal is needed - stage 3.4's recovery path covers
    # it. Blocking is the failure being fixed; unbounded growth would just
    # convert a latency problem into a memory one.
    async def run():
        document = SceneDocument()
        bus = make_bus(document, send_queue_maxsize=4)
        blocked = BlockedRecorder()
        bus.attach(blocked, buffered=True)
        node = document.add_node(0, 0, "a")

        for index in range(40):
            document.move_node(node.id, index, index)
            await bus.publish("scene")

        buffered = bus._buffered[blocked]
        assert buffered.queue.qsize() <= 4, "the queue must stay bounded"
        assert buffered.dropped > 0, "overflow must actually drop, not block"
        # The publisher never stalled despite the reader never completing.
        blocked.release.set()

    asyncio.run(run())


def test_a_dead_buffered_connection_is_detached_without_killing_the_broadcast():
    async def run():
        document = SceneDocument()
        bus = make_bus(document)

        class Dead:
            async def send_json(self, data):
                raise ConnectionError("dead socket")

        dead, alive = Dead(), Recorder()
        bus.attach(dead, buffered=True)
        bus.attach(alive, buffered=True)
        node = document.add_node(0, 0, "a")

        await bus.publish("scene")
        await asyncio.sleep(0.05)
        assert len(alive.messages) == 1
        assert bus.connection_count == 1, "the dead connection must be detached"

        document.move_node(node.id, 1, 1)
        await bus.publish("scene")
        await asyncio.sleep(0.05)
        assert len(alive.messages) == 2, "a dead peer must not poison later broadcasts"

    asyncio.run(run())


def test_detaching_cancels_the_writer_task():
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        recorder = Recorder()
        bus.attach(recorder, buffered=True)
        task = bus._buffered[recorder].task
        assert task is not None and not task.done()

        bus.detach(recorder)
        await asyncio.sleep(0)
        assert task.cancelled() or task.done(), "the writer task must not outlive its connection"
        assert recorder not in bus._buffered

    asyncio.run(run())


def test_unbuffered_attach_keeps_the_original_synchronous_delivery():
    # The property ~1600 existing tests depend on: after `await publish()`,
    # the recorder already has the message.
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        recorder = Recorder()
        bus.attach(recorder)  # default: unbuffered
        document.add_node(0, 0, "a")

        await bus.publish("scene")

        assert len(recorder.messages) == 1, "unbuffered sends complete before publish returns"
        assert recorder not in bus._buffered

    asyncio.run(run())


def test_attach_without_a_running_loop_falls_back_to_unbuffered():
    # A bare synchronous attach (no event loop) must not half-initialise a
    # buffered connection whose writer task could never be created.
    document = SceneDocument()
    bus = make_bus(document)
    recorder = Recorder()
    bus.attach(recorder, buffered=True)
    assert recorder in bus._connections
    assert recorder not in bus._buffered


class GatedRecorder:
    """A reader whose sends block until released - lets a test build up a
    real backlog in the writer queue, then observe exact delivery order."""

    def __init__(self):
        self.messages: list[dict] = []
        self.gate = asyncio.Event()

    async def send_json(self, data: dict) -> None:
        await self.gate.wait()
        self.messages.append(data)


# -- send_snapshot vs the writer queue (code-review fix) ----------------------
#
# send_snapshot used to send DIRECTLY on the socket, bypassing the buffered
# queue. With a backlog present, the snapshot overtook queued older patches,
# which then arrived after it with baseRevision below the client's new
# revision - each refused, each refusal triggering another resync. And the
# naive fix (plain offer()) is worse: a full queue silently drops the resync
# snapshot, and the client's sceneResyncPending flag is only cleared by a
# snapshot arriving, so it never asks again - wedged until reconnect.


def test_a_snapshot_never_overtakes_nor_is_followed_by_superseded_patches():
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        gated = GatedRecorder()
        bus.attach(gated, buffered=True)
        node = document.add_node(0, 0, "a")

        await bus.publish("scene")  # writer grabs this, then blocks on the gate
        await asyncio.sleep(0.01)
        for index in range(1, 5):   # a real backlog of scene patches
            document.move_node(node.id, index, index)
            await bus.publish("scene")

        await bus.send_snapshot("scene", gated)  # must purge + join the queue
        gated.gate.set()
        await asyncio.sleep(0.05)

        kinds = [m["kind"] for m in gated.messages]
        assert kinds[-1] == "state", f"the snapshot must arrive LAST, got {kinds}"
        assert "patch" not in kinds, (
            f"superseded patches must be purged, not delivered stale: {kinds}"
        )
        # And the snapshot is current: it carries the last-published position.
        snap = gated.messages[-1]["payload"]
        assert {n["id"]: n for n in snap["nodes"]}[node.id]["x"] == 4.0

    asyncio.run(run())


def test_a_resync_snapshot_is_never_dropped_by_a_full_queue():
    # The wedge case: sceneResyncPending is only ever cleared client-side by
    # a snapshot arriving, so dropping the resync snapshot means the client
    # never asks again - stuck until reconnect.
    async def run():
        document = SceneDocument()
        bus = make_bus(document, send_queue_maxsize=3)
        gated = GatedRecorder()
        bus.attach(gated, buffered=True)
        node = document.add_node(0, 0, "a")

        await bus.publish("scene")
        await asyncio.sleep(0.01)
        for index in range(1, 8):  # overflow the tiny queue
            document.move_node(node.id, index, index)
            await bus.publish("scene")
        assert bus._buffered[gated].dropped > 0, "precondition: the queue really overflowed"

        await bus.send_snapshot("scene", gated)
        gated.gate.set()
        await asyncio.sleep(0.05)

        assert gated.messages[-1]["kind"] == "state", (
            "the resync snapshot must be delivered even through a full queue"
        )

    asyncio.run(run())


def test_the_purge_keeps_stream_frames_and_other_topics_untouched():
    # A snapshot supersedes only ITS OWN topic's state/patch frames. Stream
    # deltas and other topics' frames are unrelated and must survive, in
    # their original order.
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        state = {"n": 0}
        bus.register_topic("counter", lambda: {"n": state["n"]})
        gated = GatedRecorder()
        bus.attach(gated, buffered=True)
        node = document.add_node(0, 0, "a")

        await bus.publish("scene")  # in the writer's hands, gate closed
        await asyncio.sleep(0.01)
        document.move_node(node.id, 1, 1)
        await bus.publish("scene")                       # queued: scene patch (superseded)
        await bus.publish_stream(
            topic="scene", request_id="r1", seq=0, delta="hi", done=False
        )                                                # queued: stream (kept)
        await bus.publish("counter")                     # queued: other topic (kept)

        await bus.send_snapshot("scene", gated)
        gated.gate.set()
        await asyncio.sleep(0.05)

        tail = [(m["kind"], m["topic"]) for m in gated.messages[1:]]
        assert tail == [("stream", "scene"), ("state", "counter"), ("state", "scene")], tail

    asyncio.run(run())


def test_send_snapshot_to_an_unbuffered_connection_is_still_synchronous():
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        recorder = Recorder()
        bus.attach(recorder)  # unbuffered
        document.add_node(0, 0, "a")
        await bus.publish("scene")

        await bus.send_snapshot("scene", recorder)

        assert recorder.messages[-1]["kind"] == "state", (
            "unbuffered handshake must complete before send_snapshot returns"
        )

    asyncio.run(run())


# -- coalescing ---------------------------------------------------------------


def test_concurrent_publishes_inside_one_window_ship_a_single_message():
    # The real shape this exists for: agents.py schedules runs with
    # asyncio.create_task and does NOT await them, so several independent
    # runs genuinely publish at overlapping times while the WS loop also
    # publishes user-driven intents.
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.02)
        recorder = Recorder()
        bus.attach(recorder)
        node = document.add_node(0, 0, "a")
        await bus.publish("scene")
        before = len(recorder.messages)

        async def mutate_and_publish(offset: int):
            document.move_node(node.id, offset, offset)
            await bus.publish("scene")

        await asyncio.gather(*(mutate_and_publish(i) for i in range(1, 6)))

        assert len(recorder.messages) - before == 1, "five concurrent publishes, one frame"

    asyncio.run(run())


def test_the_coalesced_frame_carries_the_LAST_state_not_the_first():
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.02)
        recorder = Recorder()
        bus.attach(recorder)
        node = document.add_node(0, 0, "a")
        await bus.publish("scene")

        async def mutate_and_publish(offset: float):
            document.move_node(node.id, offset, offset)
            await bus.publish("scene")

        await asyncio.gather(*(mutate_and_publish(i) for i in (1.0, 2.0, 99.0)))

        # Whatever the document settled on is what the client must hold.
        final = document.nodes[node.id]
        frame = recorder.messages[-1]
        if frame["kind"] == "patch":
            row = {op["node"]["id"]: op["node"] for op in frame["ops"] if op["op"] == "upsertNode"}[node.id]
        else:
            row = {n["id"]: n for n in frame["payload"]["nodes"]}[node.id]
        assert (row["x"], row["y"]) == (final.x, final.y)

    asyncio.run(run())


def test_publishes_in_separate_windows_are_not_merged():
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.01)
        recorder = Recorder()
        bus.attach(recorder)
        node = document.add_node(0, 0, "a")
        await bus.publish("scene")
        before = len(recorder.messages)

        document.move_node(node.id, 1, 1)
        await bus.publish("scene")
        await asyncio.sleep(0.05)  # well past the window
        document.move_node(node.id, 2, 2)
        await bus.publish("scene")

        assert len(recorder.messages) - before == 2

    asyncio.run(run())


def test_a_mutation_landing_while_a_flush_is_mid_SEND_is_not_swallowed():
    # The discriminating case for WHERE the slot is released. The flush is
    # made genuinely slow (a 0.1 s reader) so a second mutation lands while
    # the first flush is still sending:
    #   slot released BEFORE the send (correct) -> the slot is already free,
    #     so the second publish opens a fresh window and its mutation ships.
    #   slot released AFTER the send  (wrong)   -> the slot is still held, so
    #     the second publish JOINS a flush that already read the document,
    #     and its mutation never reaches the wire at all.
    # An earlier version of this test slept past the whole flush, so both
    # orderings passed it - it proved nothing.
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.01)
        slow = SlowRecorder(delay=0.1)
        bus.attach(slow)  # unbuffered: the flush itself waits on the send
        node = document.add_node(0, 0, "a")
        await bus.publish("scene")

        document.move_node(node.id, 5.0, 5.0)
        first = asyncio.ensure_future(bus.publish("scene"))
        await asyncio.sleep(0.05)  # window fired; the send is still in flight
        document.move_node(node.id, 7.0, 7.0)
        second = asyncio.ensure_future(bus.publish("scene"))
        await asyncio.gather(first, second)
        await asyncio.sleep(0.3)

        final_x = document.nodes[node.id].x
        seen_x = None
        for frame in slow.messages:
            if frame["kind"] == "patch":
                for op in frame["ops"]:
                    if op["op"] == "upsertNode" and op["node"]["id"] == node.id:
                        seen_x = op["node"]["x"]
            else:
                seen_x = {n["id"]: n for n in frame["payload"]["nodes"]}[node.id]["x"]
        assert seen_x == final_x, (
            f"the mid-send mutation never reached the wire (last seen x={seen_x}, "
            f"document has {final_x}) - the flush slot is being released too late"
        )

    asyncio.run(run())


def test_coalescing_is_per_topic_not_global():
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.02)
        state = {"n": 0}
        bus.register_topic("counter", lambda: {"n": state["n"]})
        recorder = Recorder()
        bus.attach(recorder)

        document.add_node(0, 0, "a")
        await asyncio.gather(bus.publish("scene"), bus.publish("counter"))

        topics = {m["topic"] for m in recorder.messages}
        assert topics == {"scene", "counter"}, "two topics must not share one flush"

    asyncio.run(run())


def test_an_unknown_topic_still_raises_synchronously_to_its_own_caller():
    # Resolved before any scheduling: surfacing this a window later inside a
    # shared flush would also wrongly fail every unrelated joiner.
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.02)
        with pytest.raises(UnknownTopicError):
            await bus.publish("no-such-topic")

    asyncio.run(run())


def test_one_joiner_being_cancelled_still_leaves_the_others_with_their_state():
    # NOTE the name: this pins the OUTCOME (a cancelled caller does not take
    # its co-joiners down with it), not the mechanism. The shield in
    # publish() is the conventional way to get this, but mutation testing
    # showed removing it changes nothing observable - the `not
    # pending.done()` guard makes the next caller open a fresh window
    # instead. Asserting the outcome keeps this test honest about what it
    # actually proves; see publish()'s own comment.
    async def run():
        document = SceneDocument()
        bus = make_bus(document, coalesce_window_seconds=0.02)
        recorder = Recorder()
        bus.attach(recorder)
        node = document.add_node(0, 0, "a")
        await bus.publish("scene")
        before = len(recorder.messages)

        document.move_node(node.id, 3, 3)
        doomed = asyncio.ensure_future(bus.publish("scene"))
        survivor = asyncio.ensure_future(bus.publish("scene"))
        await asyncio.sleep(0)
        doomed.cancel()
        await survivor

        assert len(recorder.messages) - before == 1, "the flush must survive a cancelled joiner"

    asyncio.run(run())


def test_window_zero_publishes_immediately_and_is_the_class_default():
    async def run():
        document = SceneDocument()
        bus = make_bus(document)
        assert bus._coalesce_window_seconds == 0.0
        recorder = Recorder()
        bus.attach(recorder)
        document.add_node(0, 0, "a")

        await bus.publish("scene")

        assert len(recorder.messages) == 1, "window 0 must not defer anything"

    asyncio.run(run())


def test_eventbus_hands_its_window_to_every_session_it_mints():
    bus = EventBus(coalesce_window_seconds=DEFAULT_COALESCE_WINDOW_SECONDS)
    session = bus.session("a")
    assert session._coalesce_window_seconds == DEFAULT_COALESCE_WINDOW_SECONDS
    # ...and the default stays immediate for everyone who does not ask.
    assert EventBus().session("b")._coalesce_window_seconds == 0.0


def test_the_shipped_defaults_are_the_values_the_adr_names():
    assert DEFAULT_COALESCE_WINDOW_SECONDS == 0.016, "the ADR's own '~16 ms window'"
    assert DEFAULT_SEND_QUEUE_MAXSIZE == 64
