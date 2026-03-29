import qtawesome as qta
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu

from graphite_config import get_current_palette


class ImageNodeContextMenu(QMenu):
    """Context menu for ImageNode, providing copy, save, and regenerate actions."""

    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.node = node
        palette = get_current_palette()

        self.setStyleSheet(f"""
            QMenu {{
                background-color: #2d2d2d; border: 1px solid #3f3f3f;
                border-radius: 4px; padding: 4px;
            }}
            QMenu::item {{
                background-color: transparent; padding: 8px 20px;
                border-radius: 4px; color: white;
            }}
            QMenu::item:selected {{ background-color: {palette.SELECTION.name()}; }}
            QMenu::separator {{ height: 1px; background-color: #3f3f3f; margin: 4px 0px; }}
        """)

        copy_image_action = QAction("Copy Image", self)
        copy_image_action.setIcon(qta.icon('fa5s.copy', color='white'))
        copy_image_action.triggered.connect(self.copy_image)
        self.addAction(copy_image_action)

        save_image_action = QAction("Export Image (.png/.jpg)", self)
        save_image_action.setIcon(qta.icon('fa5s.save', color='white'))
        save_image_action.triggered.connect(self.save_image)
        self.addAction(save_image_action)

        self.addSeparator()

        scene = self.node.scene()
        is_branch_hidden = getattr(scene, 'is_branch_hidden', False)
        visibility_text = "Show All Branches" if is_branch_hidden else "Hide Other Branches"
        visibility_icon = 'fa5s.eye' if is_branch_hidden else 'fa5s.eye-slash'
        visibility_action = QAction(visibility_text, self)
        visibility_action.setIcon(qta.icon(visibility_icon, color='white'))
        visibility_action.triggered.connect(self.toggle_branch_visibility)
        self.addAction(visibility_action)

        if self.node.parent_content_node and self.node.prompt:
            regenerate_action = QAction("Regenerate Image", self)
            regenerate_action.setIcon(qta.icon('fa5s.sync', color='white'))
            regenerate_action.triggered.connect(self.regenerate_image)
            self.addAction(regenerate_action)

        delete_action = QAction("Delete Image", self)
        delete_action.setIcon(qta.icon('fa5s.trash', color='white'))
        delete_action.triggered.connect(self.delete_node)
        self.addAction(delete_action)

    def copy_image(self):
        QApplication.clipboard().setImage(self.node.image)
        main_window = self.node.scene().window if self.node.scene() else None
        if main_window and hasattr(main_window, 'notification_banner'):
            main_window.notification_banner.show_message("Image copied to clipboard.", 3000, "success")

    def save_image(self):
        main_window = self.node.scene().window if self.node.scene() else None
        file_path, _ = QFileDialog.getSaveFileName(
            main_window, "Save Image", "", "PNG Images (*.png);;JPEG Images (*.jpg)"
        )
        if file_path:
            self.node.image.save(file_path)
            if main_window and hasattr(main_window, 'notification_banner'):
                main_window.notification_banner.show_message(f"Image saved to:\n{file_path}", 5000, "success")

    def regenerate_image(self):
        parent_chat_node = self.node.parent_content_node
        if parent_chat_node and parent_chat_node.scene():
            main_window = parent_chat_node.scene().window
            if main_window and hasattr(main_window, 'generate_image'):
                main_window.generate_image(parent_chat_node)

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


__all__ = ["ImageNodeContextMenu"]
