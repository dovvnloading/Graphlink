import qtawesome as qta
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from graphite_config import get_current_palette


class PluginNodeContextMenu(QMenu):
    """Shared context menu for plugin-like nodes that can open Document View."""

    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.node = node
        palette = get_current_palette()

        self.setStyleSheet(
            f"""
            QMenu {{
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                background-color: transparent;
                padding: 8px 20px;
                border-radius: 4px;
                color: white;
            }}
            QMenu::item:selected {{
                background-color: {palette.SELECTION.name()};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: #3f3f3f;
                margin: 4px 0px;
            }}
            """
        )

        doc_view_action = QAction("Open Document View", self)
        doc_view_action.setIcon(qta.icon("fa5s.book-open", color="white"))
        doc_view_action.triggered.connect(self.open_document_view)
        self.addAction(doc_view_action)

        if getattr(self.node, "supports_branch_context_toggle", False):
            branch_context_action = QAction("Include Previous Branch Context", self)
            branch_context_action.setCheckable(True)
            branch_context_action.setChecked(bool(getattr(self.node, "include_branch_context", True)))
            branch_context_action.toggled.connect(self._set_branch_context_enabled)
            self.addAction(branch_context_action)

        if hasattr(self.node, "toggle_collapse"):
            self.addSeparator()
            collapse_text = "Expand Node" if bool(getattr(self.node, "is_collapsed", False)) else "Collapse Node"
            collapse_icon = "fa5s.expand-arrows-alt" if bool(getattr(self.node, "is_collapsed", False)) else "fa5s.compress-arrows-alt"
            collapse_action = QAction(collapse_text, self)
            collapse_action.setIcon(qta.icon(collapse_icon, color="white"))
            collapse_action.triggered.connect(self.node.toggle_collapse)
            self.addAction(collapse_action)

        self.addSeparator()

        delete_action = QAction("Delete Node", self)
        delete_action.setIcon(qta.icon("fa5s.trash", color="white"))
        delete_action.triggered.connect(self.delete_node)
        self.addAction(delete_action)

    def open_document_view(self):
        scene = self.node.scene()
        window = getattr(scene, "window", None) if scene else None
        if window and hasattr(window, "show_document_view"):
            window.show_document_view(self.node)

    def _set_branch_context_enabled(self, enabled):
        setattr(self.node, "include_branch_context", bool(enabled))
        callback = getattr(self.node, "on_branch_context_changed", None)
        if callable(callback):
            callback(bool(enabled))
            return

        refresh_callback = getattr(self.node, "refresh_branch_context", None)
        if callable(refresh_callback):
            refresh_callback()

    def delete_node(self):
        scene = self.node.scene()
        if not scene or not hasattr(scene, "deleteSelectedItems"):
            return
        scene.clearSelection()
        self.node.setSelected(True)
        scene.deleteSelectedItems()
