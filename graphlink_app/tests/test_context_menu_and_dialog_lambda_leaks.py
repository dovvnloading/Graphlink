"""Bug-scan finding: ChatNodeContextMenu, CodeNodeContextMenu,
DocumentNodeContextMenu, and ColorPickerDialog connected several
QAction.triggered/QPushButton.clicked signals to a lambda closing over
self (e.g. `lambda: self._handle_export('txt')`, or - inside a loop -
`lambda checked, t=chart_type: self.generate_chart(t)`).

PySide6's garbage collector does not reclaim a self-capturing lambda
connected to a widget-owned signal - confirmed empirically (see the
worker-owning-node fix in PR #93): a bound-method connection to the same
signal IS reclaimed fine, a lambda is not. And it isn't limited to custom
Qt Signals or worker threads either - ColorPickerDialog's leak comes from
a stock QPushButton.clicked calling a plain method, no Signal.emit
involved at all.

So every one of these menus/dialogs - and, for the three context menus,
the canvas node each one stores via self.node - leaked forever, for the
rest of the process, on every single right-click or "change color" popup.

Fixed by moving the per-instance variant data onto the widget itself
(QAction.setData() / QWidget.setProperty()) and connecting every
action/button to ONE shared bound-method dispatcher that reads
self.sender().data()/.property(...). A bound-method connection doesn't
trip the same GC-invisible cycle.

These tests use weakref + gc.collect() against REAL menu/dialog + REAL
node instances (never a MagicMock in place of the object under GC test -
a MagicMock's own internal bookkeeping can itself look like a leak or
mask one, so it would be the wrong tool here) to prove collectibility
directly, matching the convention established in
test_plugin_node_dispose_lifecycle.py's TestArtifactNodeDelBackstop /
TestDisposeBreaksTheWindowActionsSignalCycle classes.
"""

import gc
import sys
import weakref
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_canvas.graphlink_canvas_dialogs import ColorPickerDialog
from graphlink_nodes.graphlink_node_chat_menu import ChatNodeContextMenu
from graphlink_nodes.graphlink_node_code_menu import CodeNodeContextMenu
from graphlink_nodes.graphlink_node_document_menu import DocumentNodeContextMenu
from graphlink_scene import ChatScene


def _make_scene():
    window = MagicMock()
    return ChatScene(window=window)


class TestChatNodeContextMenuDoesNotLeak:
    def _build(self):
        scene = _make_scene()
        node = scene.add_chat_node("hello world", is_user=True)
        menu = ChatNodeContextMenu(node)
        return scene, weakref.ref(menu), weakref.ref(node)

    def test_menu_and_node_are_collectible_after_construction(self):
        scene, menu_ref, node_ref = self._build()
        del scene
        gc.collect()

        assert menu_ref() is None, "ChatNodeContextMenu leaked (self-capturing lambda cycle)"
        assert node_ref() is None, "ChatNode leaked via the menu's self.node reference"

    def test_export_actions_still_dispatch_to_the_right_format(self):
        scene = _make_scene()
        node = scene.add_chat_node("hello world", is_user=True)
        menu = ChatNodeContextMenu(node)
        menu._handle_export = MagicMock()

        export_menu = menu.create_export_menu()
        formats_seen = []
        for action in export_menu.actions():
            formats_seen.append(action.data())
            action.trigger()

        assert formats_seen == ["txt", "md", "html", "docx", "pdf"]
        assert menu._handle_export.call_args_list == [
            ((fmt,),) for fmt in ("txt", "md", "html", "docx", "pdf")
        ]

    def test_reveal_action_carries_the_docked_node_as_its_data(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = scene.add_chat_node("child", is_user=False, parent_node=parent)
        docked = scene.add_chat_node("docked", is_user=False, parent_node=parent)
        node.get_docked_child_nodes = lambda: [docked]

        menu = ChatNodeContextMenu(node)
        menu.undock_node = MagicMock()

        undock_menu = None
        for action in menu.actions():
            sub = action.menu()
            if sub is not None and sub.title() == "Reveal Docked Items":
                undock_menu = sub
                break
        assert undock_menu is not None, "Reveal Docked Items submenu not found"

        reveal_actions = undock_menu.actions()
        assert len(reveal_actions) == 1
        assert reveal_actions[0].data() is docked

        reveal_actions[0].trigger()
        menu.undock_node.assert_called_once_with(docked)

    def test_chart_type_actions_dispatch_the_right_type(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = scene.add_chat_node("child", is_user=False, parent_node=parent)
        menu = ChatNodeContextMenu(node)
        menu.generate_chart = MagicMock()

        chart_menu = None
        for action in menu.actions():
            sub = action.menu()
            if sub is not None and sub.title() == "Generate Chart":
                chart_menu = sub
                break
        assert chart_menu is not None, "Generate Chart submenu not found"

        types_seen = [a.data() for a in chart_menu.actions()]
        assert types_seen == ["bar", "line", "histogram", "pie", "sankey"]

        chart_menu.actions()[0].trigger()
        menu.generate_chart.assert_called_once_with("bar")


class TestCodeNodeContextMenuDoesNotLeak:
    def _build(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=False)
        node = scene.add_code_node("print(1)", "python", parent)
        menu = CodeNodeContextMenu(node)
        return scene, weakref.ref(menu), weakref.ref(node)

    def test_menu_and_node_are_collectible_after_construction(self):
        scene, menu_ref, node_ref = self._build()
        del scene
        gc.collect()

        assert menu_ref() is None, "CodeNodeContextMenu leaked (self-capturing lambda cycle)"
        assert node_ref() is None, "CodeNode leaked via the menu's self.node reference"

    def test_export_actions_still_dispatch_to_the_right_format(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=False)
        node = scene.add_code_node("print(1)", "python", parent)
        menu = CodeNodeContextMenu(node)
        menu._handle_export = MagicMock()

        export_menu = menu.create_export_menu()
        formats_seen = [a.data() for a in export_menu.actions()]
        export_menu.actions()[0].trigger()

        assert formats_seen == ["py", "txt", "md", "html", "pdf"]
        menu._handle_export.assert_called_once_with("py")


class TestDocumentNodeContextMenuDoesNotLeak:
    def _build(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = scene.add_document_node("file.txt", "content", parent, attachment_kind="document")
        menu = DocumentNodeContextMenu(node)
        return scene, weakref.ref(menu), weakref.ref(node)

    def test_menu_and_node_are_collectible_after_construction(self):
        scene, menu_ref, node_ref = self._build()
        del scene
        gc.collect()

        assert menu_ref() is None, "DocumentNodeContextMenu leaked (self-capturing lambda cycle)"
        assert node_ref() is None, "DocumentNode leaked via the menu's self.node reference"

    def test_export_actions_still_dispatch_to_the_right_format(self):
        scene = _make_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = scene.add_document_node("file.txt", "content", parent, attachment_kind="document")
        menu = DocumentNodeContextMenu(node)
        menu._handle_export = MagicMock()

        export_menu = menu.create_export_menu()
        formats_seen = [a.data() for a in export_menu.actions()]
        export_menu.actions()[3].trigger()

        assert formats_seen == ["txt", "md", "html", "docx", "pdf"]
        menu._handle_export.assert_called_once_with("docx")


class TestColorPickerDialogDoesNotLeak:
    def test_dialog_is_collectible_after_construction(self):
        def build():
            dialog = ColorPickerDialog(None)
            return weakref.ref(dialog)

        ref = build()
        gc.collect()

        assert ref() is None, "ColorPickerDialog leaked (self-capturing lambda cycle)"

    def test_default_button_selects_default(self):
        dialog = ColorPickerDialog(None)
        dialog._on_default_clicked()

        assert dialog.get_selected_color() == (None, "default")

    def test_a_swatch_button_selects_its_own_color_data(self):
        from PySide6.QtWidgets import QPushButton

        dialog = ColorPickerDialog(None)
        # Find a real swatch button by its stored property (the reset button
        # has no such property, so this can't accidentally match it).
        swatch_buttons = [
            btn for btn in dialog.findChildren(QPushButton)
            if btn.property("frame_color_data") is not None
        ]
        assert swatch_buttons, "no swatch buttons found"

        target = swatch_buttons[0]
        expected = target.property("frame_color_data")
        target.click()

        assert dialog.get_selected_color() == (expected["color"], expected["type"])
