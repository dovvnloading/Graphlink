import qtawesome as qta
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu

from graphite_config import get_current_palette
from graphite_exporter import Exporter
from graphite_nodes.graphite_node_chat import ChatNode


class ChatNodeContextMenu(QMenu):
    """
    A comprehensive context menu for ChatNode, providing access to text manipulation,
    AI actions (summaries, explainers, charts), and organizational tools.
    """

    def __init__(self, node, parent=None):
        super().__init__(parent)
        self.node = node
        palette = get_current_palette()

        self.setStyleSheet(f"""
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
        """)

        copy_action = QAction("Copy Text", self)
        copy_action.setIcon(qta.icon('fa5s.copy', color='white'))
        copy_action.triggered.connect(self.copy_text)
        self.addAction(copy_action)

        collapse_text = "Expand Node" if self.node.is_collapsed else "Collapse Node"
        collapse_icon = 'fa5s.expand-arrows-alt' if self.node.is_collapsed else 'fa5s.compress-arrows-alt'
        collapse_action = QAction(collapse_text, self)
        collapse_action.setIcon(qta.icon(collapse_icon, color='white'))
        collapse_action.triggered.connect(self.node.toggle_collapse)
        self.addAction(collapse_action)

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

        if self.node.docked_thinking_nodes:
            self.addSeparator()
            undock_action = QAction("Undock Thinking Node", self)
            undock_action.setIcon(qta.icon('fa5s.expand-arrows-alt', color='white'))
            undock_action.triggered.connect(self.undock_thinking_node)
            self.addAction(undock_action)

        self.addSeparator()

        selected_chat_nodes = [item for item in self.node.scene().selectedItems() if isinstance(item, ChatNode)]

        if len(selected_chat_nodes) > 1:
            group_summary_action = QAction("Generate Group Summary", self)
            group_summary_action.setIcon(qta.icon('fa5s.object-group', color='white'))
            group_summary_action.triggered.connect(self.generate_group_summary)
            self.addAction(group_summary_action)
        else:
            doc_view_action = QAction("Open Document View", self)
            doc_view_action.setIcon(qta.icon('fa5s.book-open', color='white'))
            doc_view_action.triggered.connect(self.open_document_view)
            self.addAction(doc_view_action)
            self.addSeparator()

            takeaway_action = QAction("Generate Key Takeaway", self)
            takeaway_action.setIcon(qta.icon('fa5s.lightbulb', color='white'))
            takeaway_action.triggered.connect(self.generate_takeaway)
            self.addAction(takeaway_action)

            explainer_action = QAction("Generate Explainer Note", self)
            explainer_action.setIcon(qta.icon('fa5s.question', color='white'))
            explainer_action.triggered.connect(self.generate_explainer)
            self.addAction(explainer_action)

            chart_menu = QMenu("Generate Chart", self)
            chart_menu.setIcon(qta.icon('fa5s.chart-bar', color='white'))
            chart_menu.setStyleSheet(self.styleSheet())

            chart_types = [
                ("Bar Chart", "bar", 'fa5s.chart-bar'),
                ("Line Graph", "line", 'fa5s.chart-line'),
                ("Histogram", "histogram", 'fa5s.chart-area'),
                ("Pie Chart", "pie", 'fa5s.chart-pie'),
                ("Sankey Diagram", "sankey", 'fa5s.project-diagram'),
            ]

            for title, chart_type, icon in chart_types:
                action = QAction(title, chart_menu)
                action.setIcon(qta.icon(icon, color='white'))
                action.triggered.connect(lambda checked, t=chart_type: self.generate_chart(t))
                chart_menu.addAction(action)

            self.addMenu(chart_menu)

            image_gen_action = QAction("Generate Image", self)
            image_gen_action.setIcon(qta.icon('fa5s.image', color='white'))
            image_gen_action.triggered.connect(self.generate_image)
            self.addAction(image_gen_action)

        self.addSeparator()

        delete_action = QAction("Delete Node", self)
        delete_action.setIcon(qta.icon('fa5s.trash', color='white'))
        delete_action.triggered.connect(self.delete_node)
        self.addAction(delete_action)

        if not self.node.is_user:
            self.addSeparator()

            regenerate_action = QAction("Regenerate Response", self)
            regenerate_action.setIcon(qta.icon('fa5s.sync', color='white'))
            regenerate_action.triggered.connect(self.regenerate_response)
            self.addAction(regenerate_action)

    def undock_thinking_node(self):
        if self.node.docked_thinking_nodes:
            node_to_undock = self.node.docked_thinking_nodes.pop(0)
            node_to_undock.undock()
            self.node.update()

    def create_export_menu(self):
        export_menu = QMenu("Export to Doc", self)
        export_menu.setIcon(qta.icon('fa5s.file-export', color='white'))

        txt_action = QAction("Text File (.txt)", self)
        txt_action.triggered.connect(lambda: self._handle_export('txt'))
        export_menu.addAction(txt_action)

        md_action = QAction("Markdown File (.md)", self)
        md_action.triggered.connect(lambda: self._handle_export('md'))
        export_menu.addAction(md_action)

        html_action = QAction("HTML Document (.html)", self)
        html_action.triggered.connect(lambda: self._handle_export('html'))
        export_menu.addAction(html_action)

        docx_action = QAction("Word Document (.docx)", self)
        docx_action.triggered.connect(lambda: self._handle_export('docx'))
        export_menu.addAction(docx_action)

        pdf_action = QAction("PDF Document (.pdf)", self)
        pdf_action.triggered.connect(lambda: self._handle_export('pdf'))
        export_menu.addAction(pdf_action)

        return export_menu

    def _handle_export(self, file_format):
        exporter = Exporter()
        content = self.node.text
        default_filename = f"chat_node_export.{file_format}"

        filters = {
            'txt': "Text Files (*.txt)",
            'pdf': "PDF Documents (*.pdf)",
            'docx': "Word Documents (*.docx)",
            'html': "HTML Files (*.html)",
            'md': "Markdown Files (*.md)",
        }

        main_window = self.node.scene().window
        file_path, _ = QFileDialog.getSaveFileName(main_window, "Export Node Content", default_filename, filters[file_format])

        if not file_path:
            return

        success, error_msg = False, "Unknown format"
        try:
            if file_format == 'txt':
                success, error_msg = exporter.export_to_txt(content, file_path)
            elif file_format == 'pdf':
                success, error_msg = exporter.export_to_pdf(content, file_path, is_code=False)
            elif file_format == 'docx':
                success, error_msg = exporter.export_to_docx(content, file_path)
            elif file_format == 'html':
                success, error_msg = exporter.export_to_html(content, file_path)
            elif file_format == 'md':
                success, error_msg = exporter.export_to_md(content, file_path)
        except ImportError as e:
            main_window.notification_banner.show_message(f"Dependency Missing: {str(e)}", 8000, "warning")
            return

        if success:
            main_window.notification_banner.show_message(f"Export Successful:\n{file_path}", 5000, "success")
        else:
            main_window.notification_banner.show_message(f"Export Failed:\n{error_msg}", 8000, "error")

    def toggle_branch_visibility(self):
        scene = self.node.scene()
        if scene and hasattr(scene, 'toggle_branch_visibility'):
            scene.toggle_branch_visibility(self.node)

    def copy_text(self):
        QApplication.clipboard().setText(self.node.text)
        main_window = self.node.scene().window if self.node.scene() else None
        if main_window and hasattr(main_window, 'notification_banner'):
            main_window.notification_banner.show_message("Text copied to clipboard.", 3000, "success")

    def delete_node(self):
        scene = self.node.scene()
        if scene and hasattr(scene, 'delete_chat_node'):
            scene.delete_chat_node(self.node)

    def regenerate_response(self):
        main_window = self.node.scene().window
        if not main_window:
            return
        if hasattr(main_window, 'regenerate_node'):
            main_window.regenerate_node(self.node)

    def generate_takeaway(self):
        scene = self.node.scene()
        if scene and scene.window:
            scene.window.generate_takeaway(self.node)

    def generate_group_summary(self):
        scene = self.node.scene()
        if scene and scene.window:
            scene.window.generate_group_summary()

    def generate_explainer(self):
        scene = self.node.scene()
        if scene and scene.window:
            scene.window.generate_explainer(self.node)

    def generate_chart(self, chart_type):
        scene = self.node.scene()
        if scene and scene.window:
            scene.window.generate_chart(self.node, chart_type)

    def generate_image(self):
        scene = self.node.scene()
        if scene and scene.window:
            scene.window.generate_image(self.node)

    def open_document_view(self):
        scene = self.node.scene()
        if scene and scene.window:
            scene.window.show_document_view(self.node)


__all__ = ["ChatNodeContextMenu"]
