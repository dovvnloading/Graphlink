"""Headless regression coverage for canvas rendering and interaction paths."""

from unittest.mock import MagicMock

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF
from PySide6.QtGui import QColor, QImage, QPainter

from graphlink_config import canvas_font
from graphlink_connections import ConnectionItem
from graphlink_node import CodeNode, ImageNode
from graphlink_scene import ChatScene
from graphlink_view import ChatView


def _png_bytes(width=20, height=2000):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#536273"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _scene():
    return ChatScene(MagicMock())


def test_code_node_scrolls_long_content_and_respects_max_height():
    scene = _scene()
    parent = scene.add_chat_node("parent", preferred_pos=QPointF(0, 0))
    node = scene.add_code_node("\n".join(f"print({index})" for index in range(1200)), "python", parent)

    assert isinstance(node, CodeNode)
    assert node.height <= node.MAX_HEIGHT
    assert node.scrollbar.isVisible()
    node.scrollbar.set_value(1.0)
    assert node.scroll_value == 1.0


def test_image_preview_is_bounded_and_invalid_data_gets_a_placeholder_state():
    valid = ImageNode(_png_bytes(), None, "tall image")
    invalid = ImageNode(b"not an image", None, "missing image")

    assert valid.image_valid
    assert valid.height <= valid.MAX_HEIGHT
    assert not invalid.image_valid
    assert invalid.height > invalid.HEADER_HEIGHT


def test_zoom_api_keeps_transform_and_state_in_sync():
    view = ChatView(MagicMock())
    assert view.zoom_by(1.25)
    assert view._zoom_factor == view.transform().m11()
    assert not view.zoom_by(100)
    view.reset_zoom()
    assert view._zoom_factor == 1.0
    assert view.transform().m11() == 1.0


def test_view_wheel_routing_discovers_scrollable_items_by_capability():
    view = ChatView(MagicMock())
    scene = view.scene()
    parent = scene.add_chat_node("parent", preferred_pos=QPointF(0, 0))
    code = scene.add_code_node("\n".join("x = 1" for _ in range(1200)), "python", parent)
    view.itemAt = lambda _position: code

    assert view._scrollable_item_at(QPointF(0, 0)) is code


def test_fit_all_ignores_connection_bounds_and_transient_items():
    view = ChatView(MagicMock())
    view.resize(800, 600)
    scene = view.scene()
    root = scene.add_chat_node("root", preferred_pos=QPointF(1000, 1000))
    child = scene.add_chat_node("child", parent_node=root, preferred_pos=QPointF(1400, 1000))
    view.show()
    view.fit_all()

    assert scene.overview_rect().contains(root.sceneBoundingRect())
    assert scene.overview_rect().contains(child.sceneBoundingRect())
    assert view._zoom_factor == view.transform().m11()


def test_connection_paints_when_scene_has_no_view():
    scene = _scene()
    start = scene.add_chat_node("start", preferred_pos=QPointF(0, 0))
    end = scene.add_chat_node("end", parent_node=start, preferred_pos=QPointF(400, 0))
    connection = next(conn for conn in scene.connections if conn.end_node is end)

    image = QImage(800, 600, QImage.Format.Format_ARGB32)
    image.fill(QColor("#252526"))
    painter = QPainter(image)
    scene.render(painter)
    painter.end()

    assert connection.scene() is scene


def test_moving_a_node_uses_endpoint_index_and_font_settings_reach_canvas_items():
    scene = _scene()
    root = scene.add_chat_node("root", preferred_pos=QPointF(0, 0))
    child = scene.add_chat_node("child", parent_node=root, preferred_pos=QPointF(400, 0))
    connection = next(conn for conn in scene.connections if conn.end_node is child)

    assert connection in scene.connections_for_node(child)
    scene.setFontFamily("Arial")
    assert canvas_font(scene).family() == "Arial"
    scene.nodeMoved(child)
    assert connection.path.length() > 0
