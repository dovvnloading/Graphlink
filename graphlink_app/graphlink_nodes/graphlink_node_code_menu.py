import qtawesome as qta
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu

from graphlink_context_menu import configure_context_menu, create_context_menu
from graphlink_exporter import Exporter


class CodeNodeContextMenu(QMenu):
    """Context menu for CodeNode, providing copy, export, and regenerate actions.

    Bug-scan finding: the export actions used to connect QAction.triggered to
    a lambda closing over self (e.g. `lambda: self._handle_export('txt')`).
    PySide6's GC does not reclaim a self-capturing lambda connected to a
    widget-owned signal (confirmed empirically - a bound-method connection is
    reclaimed fine), so this menu - and the CodeNode it stores via self.node -
    leaked forever on EVERY right-click. Fixed by storing the format via
    QAction.setData() and connecting every export action to one shared
    bound-method dispatcher that reads self.sender().data().
    """

    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.node = node
        configure_context_menu(self)

        copy_action = QAction("Copy Code", self)
        copy_action.setIcon(qta.icon('fa5s.copy', color='white'))
        copy_action.triggered.connect(self.copy_code)
        self.addAction(copy_action)

        self.addSeparator()

        export_menu = self.create_export_menu()
        self.addMenu(export_menu)

        scene = self.node.scene()
        is_branch_hidden = getattr(scene, 'is_branch_hidden', False)
        visibility_text = "Show All Branches" if is_branch_hidden else "Hide Other Branches"
        visibility_icon = 'fa5s.eye' if is_branch_hidden else 'fa5s.eye-slash'
        visibility_action = QAction(visibility_text, self)
        visibility_action.setIcon(qta.icon(visibility_icon, color='white'))
        visibility_action.triggered.connect(self.toggle_branch_visibility)
        self.addAction(visibility_action)

        if self.node.parent_content_node:
            regenerate_action = QAction("Regenerate Response", self)
            regenerate_action.setIcon(qta.icon('fa5s.sync', color='white'))
            regenerate_action.triggered.connect(self.regenerate_response)
            self.addAction(regenerate_action)

        delete_action = QAction("Delete Code Block", self)
        delete_action.setIcon(qta.icon('fa5s.trash', color='white'))
        delete_action.triggered.connect(self.delete_node)
        self.addAction(delete_action)

    def create_export_menu(self):
        export_menu = create_context_menu(self, "Export to Doc")
        export_menu.setIcon(qta.icon('fa5s.file-export', color='white'))

        py_action = QAction("Python Script (.py)", self)
        py_action.setData('py')
        py_action.triggered.connect(self._on_export_action_triggered)
        export_menu.addAction(py_action)

        txt_action = QAction("Text File (.txt)", self)
        txt_action.setData('txt')
        txt_action.triggered.connect(self._on_export_action_triggered)
        export_menu.addAction(txt_action)

        md_action = QAction("Markdown File (.md)", self)
        md_action.setData('md')
        md_action.triggered.connect(self._on_export_action_triggered)
        export_menu.addAction(md_action)

        html_action = QAction("HTML Document (.html)", self)
        html_action.setData('html')
        html_action.triggered.connect(self._on_export_action_triggered)
        export_menu.addAction(html_action)

        pdf_action = QAction("PDF Document (.pdf)", self)
        pdf_action.setData('pdf')
        pdf_action.triggered.connect(self._on_export_action_triggered)
        export_menu.addAction(pdf_action)

        return export_menu

    def _on_export_action_triggered(self):
        self._handle_export(self.sender().data())

    def _handle_export(self, file_format):
        exporter = Exporter()
        content = self.node.code
        default_filename = f"code_snippet.{file_format}"

        filters = {
            'py': "Python Files (*.py)",
            'txt': "Text Files (*.txt)",
            'pdf': "PDF Documents (*.pdf)",
            'html': "HTML Files (*.html)",
            'md': "Markdown Files (*.md)",
        }

        main_window = self.node.scene().window
        file_path, _ = QFileDialog.getSaveFileName(main_window, "Export Code Content", default_filename, filters[file_format])

        if not file_path:
            return

        success, error_msg = False, "Unknown format"
        try:
            if file_format == 'txt':
                success, error_msg = exporter.export_to_txt(content, file_path)
            elif file_format == 'py':
                success, error_msg = exporter.export_to_py(content, file_path)
            elif file_format == 'pdf':
                success, error_msg = exporter.export_to_pdf(content, file_path, is_code=True)
            elif file_format == 'html':
                success, error_msg = exporter.export_to_html(content, file_path, title="Code Snippet")
            elif file_format == 'md':
                success, error_msg = exporter.export_to_md(f"```{self.node.language}\n{content}\n```", file_path)
        except ImportError as e:
            main_window.notification_banner.show_message(f"Dependency Missing: {str(e)}", 8000, "warning")
            return

        if success:
            main_window.notification_banner.show_message(f"Export Successful:\n{file_path}", 5000, "success")
        else:
            main_window.notification_banner.show_message(f"Export Failed:\n{error_msg}", 8000, "error")

    def copy_code(self):
        QApplication.clipboard().setText(self.node.code)
        main_window = self.node.scene().window if self.node.scene() else None
        if main_window and hasattr(main_window, 'notification_banner'):
            main_window.notification_banner.show_message("Code copied to clipboard.", 3000, "success")

    def regenerate_response(self):
        parent_chat_node = self.node.parent_content_node
        if parent_chat_node and parent_chat_node.scene():
            main_window = parent_chat_node.scene().window
            if main_window and hasattr(main_window, 'regenerate_node'):
                main_window.regenerate_node(parent_chat_node)

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


__all__ = ["CodeNodeContextMenu"]
