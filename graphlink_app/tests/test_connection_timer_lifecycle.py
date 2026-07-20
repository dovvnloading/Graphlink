"""Regression coverage for the timer-outlives-item crash class:

    RuntimeError: Internal C++ object (ThinkingConnectionItem) already deleted.

QGraphicsItem is not a QObject, so a QTimer/QVariantAnimation created inside a
QGraphicsItem.__init__ is never auto-parented or auto-cleaned-up. Several
classes across graphlink_connections.py, graphlink_canvas_base.py
(HoverAnimationMixin), graphlink_canvas_note.py (Note), and
graphlink_conversation_node.py (TypingIndicatorItem) created such timers with
no teardown hook, so the timer kept firing after ChatScene.clear() (used by
chat-switching, see graphlink_session/deserializers.py's restore_chat())
deleted the item's C++ side - the timer's callback then touched self.update()
on a dead object.

Two real bugs were found and fixed while building this, not just the one
reported: (1) each affected class got an itemChange(ItemSceneHasChanged,
value is None) hook to stop its own timers - correct and necessary for
QGraphicsScene.removeItem() (individual delete, e.g. right-click delete),
confirmed via a live diagnostic to fire reliably for that path; (2) but a
SECOND diagnostic proved QGraphicsScene.clear() - the actual path chat-
switching uses - never calls itemChange at all for the items it bulk-deletes,
even for the pre-existing "proven" Container/ChartItem precedent this fix
was modeled on (its ghost_frame_timer was still active after scene.clear()
too, confirmed empirically). So ChatScene.clear() (graphlink_scene.py) now
explicitly walks every tracked item and tears its timers down BEFORE calling
super().clear() - that pre-clear walk is what these tests exercise, using a
REAL ChatScene (not a bare QGraphicsScene, which has no such override and
would not exercise this fix at all).

Verification approach: once an item's C++ side is deleted, shiboken blocks
ALL further attribute access on it (confirmed empirically) - even reading a
plain instance attribute raises "Internal C++ object already deleted". So
every test captures a reference to the timer/animation OBJECT ITSELF before
triggering deletion, then inspects that independent QObject afterward, never
the (by-then) dead item.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QPointF, QRectF, QTimer
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene
from shiboken6 import isValid

from graphlink_canvas.graphlink_canvas_base import HoverAnimationMixin
from graphlink_canvas.graphlink_canvas_container import Container
from graphlink_canvas.graphlink_canvas_frame import Frame
from graphlink_canvas.graphlink_canvas_note import Note
from graphlink_connections import ConnectionItem, SystemPromptConnectionItem, ThinkingConnectionItem
from graphlink_conversation_node import ConversationNode, TypingIndicatorItem
from graphlink_nodes.graphlink_node_chat import ChatNode
from graphlink_scene import ChatScene


def _scene():
    return ChatScene(MagicMock())


class _FakeNode(QGraphicsItem):
    """Minimal connection endpoint matching the real contract every
    ConnectionItem subclass's _get_visual_rect() expects: plain int/float
    width/height attributes - NOT QGraphicsRectItem, whose built-in .rect()
    is a method, which _get_visual_rect's hasattr(item, 'rect') branch
    would misinterpret as the QRectF attribute Frame/Container expose."""

    def __init__(self, w=50, h=50):
        super().__init__()
        self.width = w
        self.height = h

    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter, option, widget=None):
        pass


def _make_endpoints(scene):
    start = _FakeNode()
    end = _FakeNode()
    start.setPos(0, 0)
    end.setPos(0, 100)
    scene.addItem(start)
    scene.addItem(end)
    return start, end


class TestUnderlyingVulnerabilityIsReal:
    def test_an_unparented_timer_left_running_crashes_on_a_deleted_item(self):
        """Standalone reproduction of the exact bug class (deliberately NOT
        using any already-fixed production class): a QGraphicsRectItem with
        a plain, unparented QTimer wired straight to self.update(), started,
        then the item is deleted out from under it via scene.clear() while
        the timer is still running. Calling the timer's connected slot
        afterward (the same call scene.clear() would leave a live QTimer
        free to make later) reproduces the reported RuntimeError, proving
        the danger this fix closes is real, not theoretical. Uses a bare
        QGraphicsScene deliberately, to isolate the vulnerability from
        ChatScene's fix. Note: Qt's signal/slot dispatch swallows exceptions
        raised inside a slot during real signal emission (prints to stderr
        instead of propagating) - calling the slot directly, as done here,
        is what actually surfaces the RuntimeError to a test."""
        from PySide6.QtWidgets import QGraphicsRectItem

        scene = QGraphicsScene()
        item = QGraphicsRectItem(QRectF(0, 0, 10, 10))
        scene.addItem(item)

        timer = QTimer()  # unparented - the exact root-cause pattern
        timer.timeout.connect(item.update)
        timer.start(16)
        assert timer.isActive()

        scene.clear()  # deletes item's C++ side; timer is untouched, still active
        assert timer.isActive()

        with pytest.raises(RuntimeError, match="already deleted"):
            item.update()  # what the timer's next tick would have done


class TestConnectionItemTimerLifecycle:
    def test_stops_timers_on_chat_scene_clear(self):
        scene = _scene()
        start, end = _make_endpoints(scene)
        conn = ConnectionItem(start, end)
        scene.addItem(conn)
        scene.connections.append(conn)

        conn.startArrowAnimation()
        animation_timer = conn.animation_timer
        hover_timer = conn.hover_start_timer
        assert animation_timer.isActive()

        scene.clear()

        assert not animation_timer.isActive()
        assert not hover_timer.isActive()

    def test_stops_timers_on_scene_removeitem(self):
        """The itemChange-based hook's own path: removeItem() does NOT
        delete the C++ object (ownership just returns to the caller), but
        it DOES fire itemChange(ItemSceneHasChanged, None) - unlike clear().
        """
        scene = _scene()
        start, end = _make_endpoints(scene)
        conn = ConnectionItem(start, end)
        scene.addItem(conn)
        conn.startArrowAnimation()
        assert conn.animation_timer.isActive()

        scene.removeItem(conn)

        assert not conn.animation_timer.isActive()


class TestThinkingConnectionItemTimerLifecycle:
    def test_stops_timers_on_chat_scene_clear(self):
        """The exact class from the reported traceback."""
        scene = _scene()
        start, end = _make_endpoints(scene)
        conn = ThinkingConnectionItem(start, end)
        scene.addItem(conn)
        scene.thinking_connections.append(conn)

        conn.startArrowAnimation()
        animation_timer = conn.animation_timer
        assert animation_timer.isActive()

        scene.clear()

        assert not animation_timer.isActive()


class TestSystemPromptConnectionItemTimerLifecycle:
    def test_stops_pulse_animation_on_chat_scene_clear(self):
        scene = _scene()
        start, end = _make_endpoints(scene)
        conn = SystemPromptConnectionItem(start, end)
        scene.addItem(conn)
        scene.system_prompt_connections.append(conn)
        pulse = conn.pulse_animation
        assert pulse.state() == pulse.State.Running

        scene.clear()

        assert pulse.state() != pulse.State.Running

    def test_pulse_animation_survives_being_added_to_a_scene(self):
        """Found while fixing the crash: the old itemChange stopped
        pulse_animation on EVERY ItemSceneHasChanged, including being added
        (not just removed) - so the pulsing border never actually animated.
        Only the removal case should stop it now."""
        scene = _scene()
        start, end = _make_endpoints(scene)
        conn = SystemPromptConnectionItem(start, end)
        pulse = conn.pulse_animation

        assert pulse.state() == pulse.State.Running
        scene.addItem(conn)
        assert pulse.state() == pulse.State.Running


class _MixinHost(QGraphicsItem, HoverAnimationMixin):
    """Minimal concrete host used to test HoverAnimationMixin's own teardown
    logic in isolation, independent of any of its 12 real production hosts."""

    def __init__(self):
        QGraphicsItem.__init__(self)
        HoverAnimationMixin.__init__(self)

    def boundingRect(self):
        return QRectF(0, 0, 10, 10)

    def paint(self, painter, option, widget=None):
        pass


class TestHoverAnimationMixinTimerLifecycle:
    def test_stop_hover_animation_timer_is_idempotent_and_stops_the_timer(self):
        host = _MixinHost()
        host.long_hover_timer.start()
        assert host.long_hover_timer.isActive()

        host._stop_hover_animation_timer()
        assert not host.long_hover_timer.isActive()
        assert host._hover_animation_disposed is True

        host._stop_hover_animation_timer()  # must not raise a second time


class TestChatNodeTimerLifecycle:
    def test_real_host_stops_hover_timer_on_chat_scene_clear(self):
        """Spot-checks a real production HoverAnimationMixin host end to
        end, through ChatScene.clear()'s pre-clear teardown walk - not just
        the mixin in isolation and not just the itemChange wiring."""
        scene = _scene()
        node = ChatNode("hello")
        scene.addItem(node)
        scene.nodes.append(node)

        node.long_hover_timer.start()
        timer = node.long_hover_timer
        assert timer.isActive()

        scene.clear()

        assert not timer.isActive()


class TestNoteTimerLifecycle:
    def test_stops_cursor_timer_on_chat_scene_clear(self):
        scene = _scene()
        note = Note(QPointF(0, 0))
        scene.addItem(note)
        scene.notes.append(note)

        note.cursor_timer.start()
        timer = note.cursor_timer
        assert timer.isActive()

        scene.clear()

        assert not timer.isActive()


class TestContainerTimerLifecycle:
    def test_stops_ghost_frame_timer_on_chat_scene_clear(self):
        """Container's own _teardown_async_helpers is pre-existing code this
        fix did not need to change - only ChatScene.clear() now actually
        calls it for the bulk-clear path, which it never did before."""
        scene = _scene()
        container = Container(items=[])
        scene.addItem(container)
        scene.containers.append(container)

        container.ghost_frame_timer.start(5000)
        timer = container.ghost_frame_timer
        assert timer.isActive()

        scene.clear()

        assert not timer.isActive()


class TestFrameTimerLifecycle:
    def test_stops_outline_animation_on_chat_scene_clear(self):
        """Found by adversarial review: the first version of the pre-clear
        walk covered notes/containers but missed self.frames entirely.
        Frame.outline_animation (started by toggle_lock()/toggle_collapse())
        is the identical unparented-QVariantAnimation pattern as Container's
        pulse/ghost-frame timers. It happened not to crash only because
        Frame._on_outline_animation_tick has its own pre-existing, unrelated
        try/except RuntimeError self-heal - exactly the fragile, reactive-
        only protection this fix's whole rationale exists to not rely on."""
        scene = _scene()
        frame = Frame(nodes=[])
        scene.addItem(frame)
        scene.frames.append(frame)

        frame.outline_animation.start()
        animation = frame.outline_animation
        assert animation.state() == animation.State.Running

        scene.clear()

        assert animation.state() != animation.State.Running


class TestConversationNodeTypingIndicatorTimerLifecycle:
    def test_stops_own_hover_timer_and_nested_typing_indicator_on_chat_scene_clear(self):
        scene = _scene()
        node = ConversationNode(parent_node=None)
        scene.addItem(node)
        scene.conversation_nodes.append(node)

        node.long_hover_timer.start()
        hover_timer = node.long_hover_timer

        indicator = TypingIndicatorItem()
        node.internal_scene.addItem(indicator)
        node._typing_indicator = indicator
        typing_timer = indicator._timer
        assert typing_timer.isActive()  # starts running unconditionally in __init__

        scene.clear()

        assert not hover_timer.isActive()
        assert not typing_timer.isActive()


class TestTypingIndicatorItemTimerLifecycle:
    def test_real_qobject_deletion_confirms_the_cpp_side_is_actually_gone(self):
        """TypingIndicatorItem is a QGraphicsObject (a real QObject, unlike
        the plain-QGraphicsItem classes above) - shiboken6.isValid() can
        directly confirm its C++ side is gone after scene.removeItem(), the
        same introspection this codebase's own test_popup_combo_lifecycle.py
        already uses for the analogous QWidget case. (This exercises the
        itemChange path directly: TypingIndicatorItem lives in its owning
        ConversationNode's nested internal_scene, not a top-level ChatScene
        list, so it's outside ChatScene's pre-clear walk and relies on its
        own itemChange hook, which DOES fire for removeItem().)"""
        scene = QGraphicsScene()
        indicator = TypingIndicatorItem()
        scene.addItem(indicator)
        assert isValid(indicator)

        timer = indicator._timer
        assert timer.isActive()
        scene.removeItem(indicator)

        assert not timer.isActive()
