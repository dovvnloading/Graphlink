import qtawesome as qta
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

from graphite_config import get_current_palette


class ThinkingNodeContextMenu(QMenu):
    """Context menu for ThinkingNode, providing copy and delete actions."""

    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.node = node
        palette = get_current_palette()

        self.setStyleSheet(f"""
            QMenu {{ background-color: #2d2d2d; border: 1px solid #3f3f3f; border-radius: 4px; padding: 4px; }}
            QMenu::item {{ background-color: transparent; padding: 8px 20px; border-radius: 4px; color: white; }}
            QMenu::item:selected {{ background-color: {palette.SELECTION.name()}; }}
        """)

        copy_action = QAction("Copy Content", self)
        copy_action.setIcon(qta.icon('fa5s.copy', color='white'))
        copy_action.triggered.connect(self.copy_content)
        self.addAction(copy_action)

        dock_action = QAction("Dock to Parent Node", self)
        dock_action.setIcon(qta.icon('fa5s.compress-arrows-alt', color='white'))
        dock_action.triggered.connect(self.node.dock)
        self.addAction(dock_action)

        scene = self.node.scene()
        is_branch_hidden = getattr(scene, 'is_branch_hidden', False)
        visibility_text = "Show All Branches" if is_branch_hidden else "Hide Other Branches"
        visibility_icon = 'fa5s.eye' if is_branch_hidden else 'fa5s.eye-slash'
        visibility_action = QAction(visibility_text, self)
        visibility_action.setIcon(qta.icon(visibility_icon, color='white'))
        visibility_action.triggered.connect(self.toggle_branch_visibility)
        self.addAction(visibility_action)

        delete_action = QAction("Delete Node", self)
        delete_action.setIcon(qta.icon('fa5s.trash', color='white'))
        delete_action.triggered.connect(self.delete_node)
        self.addAction(delete_action)

    def copy_content(self):
        QApplication.clipboard().setText(self.node.thinking_text)
        main_window = self.node.scene().window if self.node.scene() else None
        if main_window and hasattr(main_window, 'notification_banner'):
            main_window.notification_banner.show_message("Content copied to clipboard.", 3000, "success")

    def toggle_branch_visibility(self):
        scene = self.node.scene()
        if scene and hasattr(scene, 'toggle_branch_visibility'):
            scene.toggle_branch_visibility(self.node)

    def delete_node(self):
        scene = self.node.scene()
        if scene and hasattr(scene, 'deleteSelectedItems'):
            scene.clearSelection()
            self.node.setSelected(True)
            scene.deleteSelectedItems()


__all__ = ["ThinkingNodeContextMenu"]
