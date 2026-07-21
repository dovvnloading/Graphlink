"""Phase 7 prerequisite increment 1: HtmlViewNode normalized to the
request-signal contract, plus the serializer round-trip corruption fix
that fell out of the state-ownership recon.

Two things under test:
1. The USER-initiated render now goes through a `render_requested = Signal(object)`
   seam the window connects to (execute_html_view_node), matching every other
   plugin node's request-signal contract - the seam a future web island's
   "Render" intent will target. The programmatic set_html_content() restore
   path still renders directly (not a "request"), matching WebNode.set_result.
2. The serializer now reads the raw-source model attribute (get_html_content)
   instead of html_input.toHtml(). toHtml() on the setAcceptRichText(False)
   editor returned a Qt rich-text DOCUMENT wrapper with the user's markup
   HTML-escaped, which set_html_content then rendered verbatim on reload -
   corrupting the node on the first save/reload cycle. The decisive test is a
   full serialize->deserialize round-trip preserving raw markup.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_html_view import HtmlViewNode
from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.serializers import SceneSerializer

_RAW_MARKUP = "<html><body><h1>Title</h1><p>Some <b>bold</b> markup &amp; entities</p></body></html>"


def _make_window_and_scene():
    window = MagicMock()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    return window, scene


class TestRequestSignalContract:
    def test_node_declares_the_render_request_signal(self):
        node = HtmlViewNode(None)

        assert hasattr(node, "render_requested")

    def test_render_button_emits_the_request_signal_with_the_node(self):
        node = HtmlViewNode(None)
        node.set_html_content("<p>hi</p>")
        received = []
        node.render_requested.connect(received.append)

        node._handle_render_button()

        assert received == [node]

    def test_render_button_handler_does_not_render_directly(self):
        # The button must go through the signal, not call render_html itself -
        # otherwise the web-island seam this increment exists to create would be
        # bypassed. render_html stays callable (the window slot calls it), but
        # the button handler must not.
        node = HtmlViewNode(None)
        node.render_html = MagicMock()

        node._handle_render_button()

        node.render_html.assert_not_called()

    def test_execute_html_view_node_slot_calls_render_html(self):
        # The window slot's whole job is to call back into the node's render.
        from graphlink_window_actions import WindowActionsMixin

        node = MagicMock()
        # Bind the real method to a bare object so we exercise the actual slot.
        WindowActionsMixin.execute_html_view_node(MagicMock(), node)

        node.render_html.assert_called_once_with()

    def test_execute_html_view_node_slot_tolerates_none_and_missing_render(self):
        from graphlink_window_actions import WindowActionsMixin

        # Must not raise on a None node or a node lacking render_html.
        WindowActionsMixin.execute_html_view_node(MagicMock(), None)
        WindowActionsMixin.execute_html_view_node(MagicMock(), object())


class TestSerializerReadsModelStateNotWidget:
    def test_serializer_writes_the_raw_source_not_the_toHtml_wrapper(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = HtmlViewNode(parent)
        node.set_html_content(_RAW_MARKUP)
        scene.addItem(node)
        scene.html_view_nodes.append(node)

        payload = SceneSerializer(window).serialize_node(node, [parent, node])

        # The core assertion: the persisted value IS the raw source, with real
        # markup, not a Qt rich-text document wrapper with escaped tags.
        assert payload["html_content"] == _RAW_MARKUP
        assert "<h1>" in payload["html_content"]
        assert "&lt;h1&gt;" not in payload["html_content"]
        assert "<!DOCTYPE HTML" not in payload["html_content"]

    def test_full_serialize_deserialize_round_trip_preserves_raw_markup(self):
        # The decisive regression test for the save/reload corruption: a real
        # round-trip through both the serializer and the deserializer must
        # return the exact raw source the user typed.
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = HtmlViewNode(parent)
        node.set_html_content(_RAW_MARKUP)
        scene.addItem(node)
        scene.html_view_nodes.append(node)

        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        html_payload = SceneSerializer(window).serialize_node(node, [parent, node])

        target_window, target_scene = _make_window_and_scene()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        deserializer.deserialize_node(1, html_payload, {0: restored_parent})

        assert len(target_scene.html_view_nodes) == 1
        restored = target_scene.html_view_nodes[0]
        assert restored.get_html_content() == _RAW_MARKUP


class TestDeserializerWiresTheSignal:
    def test_restored_node_has_render_requested_connected_to_the_window_slot(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = HtmlViewNode(parent)
        node.set_html_content(_RAW_MARKUP)
        scene.addItem(node)
        scene.html_view_nodes.append(node)
        html_payload = SceneSerializer(window).serialize_node(node, [parent, node])
        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])

        target_window, target_scene = _make_window_and_scene()
        # A real callable slot on the window so _connect_if_available connects.
        target_window.execute_html_view_node = MagicMock()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        deserializer.deserialize_node(1, html_payload, {0: restored_parent})

        restored = target_scene.html_view_nodes[0]
        restored.render_requested.emit(restored)

        target_window.execute_html_view_node.assert_called_once_with(restored)
