"""Regression coverage for the 6 survey-reported bugs fixed together.

Each was independently re-verified (4 confirmed as reported, 2 partially true)
before fixing; these tests pin the fixed behavior so it can't silently regress.
The two paint-internal fixes (ChatNode's in-place QColor mutation, ImageNode's
missing search ring) are exercised as "paint runs without error in the buggy
code path" smoke tests plus, for ImageNode, that the search-highlight code is
actually reached - a full pixel assertion would be brittle and is not worth it
for a one-line copy / mirror-the-sibling fix.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsScene, QStyleOptionGraphicsItem

from graphlink_canvas.graphlink_canvas_frame import Frame
from graphlink_canvas.graphlink_canvas_container import Container
from graphlink_canvas.graphlink_canvas_navigation_pin import NavigationPin
from graphlink_connections import (
    ContentConnectionItem,
    DocumentConnectionItem,
    ImageConnectionItem,
    PyCoderConnectionItem,
    SystemPromptConnectionItem,
    ConnectionItem,
    resolve_collapsed_endpoint,
)


# --- Bug 1: HtmlViewNode.set_html_content corrupted markup via setHtml round-trip ---

def test_html_view_set_html_content_preserves_raw_markup():
    from graphlink_html_view import HtmlViewNode

    node = HtmlViewNode(None)
    markup = "<html><body><h1>Title</h1><p>Some <b>bold</b> markup &amp; entities</p></body></html>"
    node.set_html_content(markup)

    # Before the fix, setHtml() parsed the markup as rich text and the
    # textChanged handler overwrote html_content with the tag-stripped plain
    # text ("Title\nSome bold markup & entities"). The source must stay verbatim.
    assert node.get_html_content() == markup
    assert "<h1>" in node.get_html_content()


# --- Bug 4: NavigationPin.positionPreviewChanged was an orphaned dead signal ---

def test_navigation_pin_dead_preview_signal_removed():
    assert not hasattr(NavigationPin, "positionPreviewChanged"), (
        "positionPreviewChanged was emitted every drag step but connected "
        "nowhere; it should be gone"
    )
    # The signals that ARE wired must remain.
    for kept in ("editRequested", "contextMenuRequested", "positionCommitted"):
        assert hasattr(NavigationPin, kept)


def test_navigation_pin_still_constructs_and_moves_without_the_signal():
    scene = QGraphicsScene()
    pin = NavigationPin(title="wp", note="", pin_id="p1")
    scene.addItem(pin)
    # Removing the itemChange override (its only purpose was emitting the dead
    # signal) must not break dragging the pin around.
    pin.setPos(10, 20)
    pin.setPos(30, 40)
    assert pin.pos().x() == 30 and pin.pos().y() == 40


# --- Bug 5: connection collapsed-endpoint semantics (Frame ignored; innermost) ---

_ALL_ENDPOINT_CLASSES = [
    ConnectionItem,
    ContentConnectionItem,
    DocumentConnectionItem,
    ImageConnectionItem,
    SystemPromptConnectionItem,
    PyCoderConnectionItem,
]


def _child_in(parent):
    child = QGraphicsRectItem()
    child.setParentItem(parent)
    return child


def test_resolve_collapsed_endpoint_honors_collapsed_frame():
    frame = Frame([])
    frame.is_collapsed = True
    child = _child_in(frame)
    assert resolve_collapsed_endpoint(child) is frame


def test_resolve_collapsed_endpoint_honors_collapsed_container():
    container = Container([])
    container.is_collapsed = True
    child = _child_in(container)
    assert resolve_collapsed_endpoint(child) is container


def test_resolve_collapsed_endpoint_returns_outermost_when_nested():
    outer = Frame([])
    outer.is_collapsed = True
    inner = Container([])
    inner.is_collapsed = True
    inner.setParentItem(outer)
    child = _child_in(inner)
    # The outermost collapsed grouping is the one still visible on the canvas.
    assert resolve_collapsed_endpoint(child) is outer


def test_resolve_collapsed_endpoint_returns_item_when_nothing_collapsed():
    frame = Frame([])
    frame.is_collapsed = False
    child = _child_in(frame)
    assert resolve_collapsed_endpoint(child) is child


def test_every_connection_class_delegates_endpoint_to_the_shared_helper():
    # All 6 classes' _get_effective_endpoint ignore self, so an arbitrary
    # stand-in self is fine. This proves none of the previously-duplicated
    # variants still carries its own Frame-blind copy.
    frame = Frame([])
    frame.is_collapsed = True
    child = _child_in(frame)
    for cls in _ALL_ENDPOINT_CLASSES:
        assert cls._get_effective_endpoint(object(), child) is frame, (
            f"{cls.__name__}._get_effective_endpoint did not clamp to the "
            "collapsed Frame"
        )


# --- Bug 6: ApiSettingsWidget button label never reverted after a failed load ---

def _make_api_settings_widget():
    from graphlink_ui_dialogs.graphlink_settings_dialogs import ApiSettingsWidget

    # __init__ feeds several settings getters straight into Qt widgets that
    # reject non-str/-list values, so stub the ones it reads with real types.
    settings = MagicMock()
    settings.get_api_base_url.return_value = ""
    settings.get_api_provider.return_value = ""
    settings.get_api_models.return_value = {}
    settings.get_openai_key.return_value = ""
    settings.get_anthropic_key.return_value = ""
    settings.get_gemini_key.return_value = ""
    return ApiSettingsWidget(settings)


def test_api_button_label_stays_load_after_a_failed_fetch():
    widget = _make_api_settings_widget()
    assert widget.load_btn.text() == "Load Available Models"

    # Simulate a load attempt that fails: load_models_from_endpoint resets the
    # flag, the error handler does NOT set it, and _clear_api_worker runs.
    widget._api_load_succeeded = False
    widget._clear_api_worker()

    assert widget.load_btn.text() == "Load Available Models", (
        "a failed fetch must not relabel the button as if a catalog exists"
    )
    assert widget.load_btn.isEnabled()


def test_api_button_label_becomes_refresh_after_a_successful_load():
    widget = _make_api_settings_widget()

    # Simulate a successful load: handle_models_loaded sets the flag True, then
    # _clear_api_worker (connected after it) reads it.
    widget._api_load_succeeded = True
    widget._clear_api_worker()

    assert widget.load_btn.text() == "Refresh Available Models"


# --- Bug 2 & 3: paint-path fixes, exercised through a real paint() that BITES ---

def _paint_once(item):
    image = QImage(400, 400, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        item.paint(painter, QStyleOptionGraphicsItem(), None)
    finally:
        painter.end()


def test_chat_node_paint_does_not_mutate_the_shared_accent_color(monkeypatch):
    # Drives the REAL full-detail paint() and proves it no longer mutates the
    # accent QColor in place. Against the pre-fix code (accent_fill =
    # colors["accent"]; accent_fill.setAlpha(140)) this fails: the held accent
    # object's alpha drops to 140.
    from graphlink_nodes import graphlink_node_chat as chat_mod
    from graphlink_nodes.graphlink_node_chat import ChatNode

    from graphlink_scene import ChatScene

    monkeypatch.setattr(chat_mod, "lod_mode_for_item", lambda item: "full")
    node = ChatNode("hi", is_user=False)
    # ChatScene (not a bare QGraphicsScene): ChatNode.itemChange reads
    # scene().font_family on add. Held so it doesn't GC-delete the node.
    scene = ChatScene(MagicMock())
    scene.addItem(node)

    colors = node._surface_colors()
    accent = colors["accent"]
    assert accent.alpha() == 255  # precondition
    # Make paint() operate on THIS dict (and this accent object) so an in-place
    # mutation would be observable here.
    monkeypatch.setattr(node, "_surface_colors", lambda: colors)

    _paint_once(node)

    assert accent.alpha() == 255, "paint() mutated the shared accent QColor in place"


def test_image_node_paint_requests_the_search_highlight_only_when_matched(monkeypatch):
    # Proves the new full-detail search-ring branch actually EXECUTES: it is the
    # only thing in ImageNode.paint that asks for the "search_highlight" color.
    # Against the pre-fix code (no ring) this color is never requested even when
    # matched, so the assertion below fails.
    from graphlink_nodes import graphlink_node_image as image_mod
    from graphlink_nodes.graphlink_node_image import ImageNode

    monkeypatch.setattr(image_mod, "lod_mode_for_item", lambda item: "full")
    real_get = image_mod.get_semantic_color
    requested = []
    monkeypatch.setattr(
        image_mod, "get_semantic_color",
        lambda name: (requested.append(name), real_get(name))[1],
    )

    node = ImageNode(b"", None, prompt="a cat")
    scene = QGraphicsScene()  # held so it doesn't GC-delete the node's C++ object
    scene.addItem(node)

    node.is_search_match = False
    _paint_once(node)
    assert "search_highlight" not in requested  # no ring when not matched

    requested.clear()
    node.is_search_match = True
    _paint_once(node)
    assert "search_highlight" in requested, "matched ImageNode did not draw the search ring"
