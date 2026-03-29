from PySide6.QtWidgets import (
    QGraphicsObject, QGraphicsProxyWidget, QWidget, QVBoxLayout,
    QTextEdit, QPushButton, QLabel, QHBoxLayout, QSlider, QDialog,
    QSplitter
)
from PySide6.QtCore import QRectF, Qt, Signal, QPoint, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPainterPath, QCursor, QFont
import qtawesome as qta
from graphite_config import get_current_palette, get_graph_node_colors, get_neutral_button_colors
from graphite_canvas_items import HoverAnimationMixin
import json

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineScript
    WEBENGINE_AVAILABLE = True
except ImportError:
    # Handle the case where WebEngine might not be installed but other parts of the app are
    WEBENGINE_AVAILABLE = False


class HtmlPopoutWindow(QDialog):
    """A separate, resizable window for displaying the rendered HTML preview."""
    def __init__(self, parent_node, parent=None):
        super().__init__(parent)
        self.parent_node = parent_node
        self.setWindowTitle("HTML Preview")
        self.setGeometry(200, 200, 1024, 768)

        self.web_view = QWebEngineView()
        self._inject_scrollbar_style()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web_view)

    def _inject_scrollbar_style(self):
        css = """
        ::-webkit-scrollbar {
            width: 10px;
            height: 10px;
            background-color: #252526;
        }
        ::-webkit-scrollbar-track {
            background-color: #252526;
            border-radius: 5px;
        }
        ::-webkit-scrollbar-thumb {
            background-color: #555555;
            border-radius: 5px;
            border: 1px solid #252526;
        }
        ::-webkit-scrollbar-thumb:hover {
            background-color: #6a6a6a;
        }
        ::-webkit-scrollbar-corner {
            background: transparent;
        }
        """
        js = f"""
        (function() {{
            var style = document.createElement('style');
            style.type = 'text/css';
            style.innerHTML = {json.dumps(css)};
            document.head.appendChild(style);
        }})();
        """
        script = QWebEngineScript()
        script.setSourceCode(js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setRunsOnSubFrames(True)
        self.web_view.page().scripts().insert(script)

    def set_content(self, html):
        """Sets the HTML content of the internal web view."""
        self.web_view.setHtml(html)

    def closeEvent(self, event):
        """Notifies the parent node that this window is closing."""
        if self.parent_node:
            self.parent_node.popout_window = None
        super().closeEvent(event)


class HtmlViewNode(QGraphicsObject, HoverAnimationMixin):
    """
    A specialized QGraphicsItem that provides an interface for rendering HTML code.
    This node features a resizable splitter to adjust the code and preview panes.
    """
    NODE_WIDTH = 600
    NODE_HEIGHT = 850
    COLLAPSED_WIDTH = 250
    COLLAPSED_HEIGHT = 40
    CONNECTION_DOT_RADIUS = 5
    CONNECTION_DOT_OFFSET = 0

    def __init__(self, parent_node, parent=None):
        """
        Initializes the HtmlViewNode.

        Args:
            parent_node (QGraphicsItem): The node from which this node branches.
            parent (QGraphicsItem, optional): The parent graphics item. Defaults to None.
        """
        super().__init__(parent)
        HoverAnimationMixin.__init__(self)
        self.parent_node = parent_node
        self.children = []
        self.is_user = False
        self.conversation_history = []
        self.html_content = ""
        self.popout_window = None
        self.splitter_state = None
        
        self.is_collapsed = False
        self.collapse_button_rect = QRectF()

        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsObject.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.hovered = False

        self.widget = QWidget()
        self.widget.setObjectName("htmlViewMainWidget")
        self.widget.setFixedSize(self.NODE_WIDTH, self.NODE_HEIGHT)
        self.widget.setStyleSheet("""
            QWidget#htmlViewMainWidget { background-color: transparent; color: #e0e0e0; }
            QWidget#htmlViewMainWidget QLabel { background-color: transparent; }
        """)
        
        self._setup_ui()
        
        self.proxy = QGraphicsProxyWidget(self)
        self.proxy.setWidget(self.widget)
    
    @property
    def width(self):
        return self.COLLAPSED_WIDTH if self.is_collapsed else self.NODE_WIDTH

    @property
    def height(self):
        return self.COLLAPSED_HEIGHT if self.is_collapsed else self.NODE_HEIGHT

    def set_collapsed(self, collapsed):
        if self.is_collapsed != collapsed:
            self.is_collapsed = collapsed
            self.proxy.setVisible(not self.is_collapsed)
            self.prepareGeometryChange()
            if self.scene():
                self.scene().update_connections()
                self.scene().nodeMoved(self)
            self.update()

    def toggle_collapse(self):
        self.set_collapsed(not self.is_collapsed)

    def _on_splitter_moved(self, pos, index):
        self.splitter_state = self.splitter.sizes()

    def _setup_ui(self):
        """Constructs the internal widget layout and components of the node."""
        main_layout = QVBoxLayout(self.widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        node_colors = get_graph_node_colors()
        node_color = node_colors["header"]
        
        header_layout = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qta.icon('fa5s.code', color=node_color).pixmap(18, 18))
        header_layout.addWidget(icon)
        title_label = QLabel("HTML Renderer")
        title_label.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {node_color.name()}; background: transparent;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self.splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3f3f3f, stop:0.5 #555555, stop:1 #3f3f3f);
            }
            QSplitter::handle:vertical:hover {
                background: #2ecc71;
            }
        """)

        # --- HTML Input Pane ---
        input_container = QWidget()
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(0,0,0,0)
        input_layout.setSpacing(5)
        input_layout.addWidget(QLabel("HTML Source:"))
        self.html_input = QTextEdit()
        self.html_input.setAcceptRichText(False)
        self.html_input.setPlaceholderText("Paste your HTML code here...")
        self.html_input.textChanged.connect(self._on_content_changed)
        input_layout.addWidget(self.html_input)
        self.render_button = QPushButton("Render")
        self.render_button.clicked.connect(self.render_html)
        input_layout.addWidget(self.render_button)
        self.splitter.addWidget(input_container)

        # --- Preview Pane ---
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0,0,0,0)
        preview_layout.setSpacing(5)
        
        preview_header_layout = QHBoxLayout()
        preview_header_layout.addWidget(QLabel("Live Preview:"))
        preview_header_layout.addStretch()
        
        self.popout_button = QPushButton()
        self.popout_button.setIcon(qta.icon('fa5s.external-link-alt', color='#ccc'))
        self.popout_button.setFixedSize(28, 28)
        self.popout_button.setToolTip("Open Preview in a New Window")
        self.popout_button.clicked.connect(self._handle_popout)
        self.popout_button.setStyleSheet("""
            QPushButton { border: 1px solid #555; border-radius: 4px; }
            QPushButton:hover { background-color: #4f4f4f; }
        """)
        preview_header_layout.addWidget(self.popout_button)
        
        preview_layout.addLayout(preview_header_layout)
        
        # This container enforces the 1:1 aspect ratio for the web view
        webview_container = QWidget()
        webview_layout = QVBoxLayout(webview_container)
        webview_layout.setContentsMargins(0,0,0,0)
        
        if WEBENGINE_AVAILABLE:
            self.web_view = QWebEngineView()
            self._inject_scrollbar_style()
            self.web_view.setStyleSheet("background-color: #ffffff; border-radius: 4px;")
            webview_layout.addWidget(self.web_view)
        else:
            self.popout_button.setEnabled(False)
            error_label = QLabel(
                "QtWebEngineWidgets module not found.\n"
                "Please install it to use the HTML renderer:\n"
                "pip install PySide6-WebEngine"
            )
            error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            error_label.setStyleSheet(
                "background-color: #252526; border: 1px dashed #555;"
                "color: #888; border-radius: 4px; padding: 20px;"
            )
            webview_layout.addWidget(error_label)
            self.render_button.setEnabled(False)

        preview_width = self.NODE_WIDTH - 30
        webview_container.setFixedSize(preview_width, preview_width)
        preview_layout.addWidget(webview_container)
        
        self.splitter.addWidget(preview_container)
        main_layout.addWidget(self.splitter)
        self.splitter.setSizes([200, 500])
        self.splitter_state = self.splitter.sizes()

        for widget in [self.html_input]:
            widget.setStyleSheet("""
                QTextEdit {
                    background-color: #252526; border: 1px solid #3f3f3f;
                    color: #cccccc; border-radius: 4px; padding: 5px;
                    font-family: Consolas, Monaco, monospace;
                }
            """)
        
        button_colors = get_neutral_button_colors()

        self.render_button.setIcon(qta.icon('fa5s.play', color=button_colors["icon"].name()))
        self.render_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {button_colors["background"].name()};
                color: {button_colors["icon"].name()};
                border: 1px solid {button_colors["border"].name()};
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {button_colors["hover"].name()};
                border-color: {button_colors["hover"].lighter(112).name()};
            }}
            QPushButton:pressed {{
                background-color: {button_colors["pressed"].name()};
                border-color: {button_colors["border"].darker(105).name()};
            }}
            QPushButton:disabled {{
                background-color: #2b2b2b;
                border-color: #353535;
                color: #7b7b7b;
            }}
        """)
        
    def _inject_scrollbar_style(self):
        css = """
        ::-webkit-scrollbar {
            width: 10px;
            height: 10px;
            background-color: #252526;
        }
        ::-webkit-scrollbar-track {
            background-color: #252526;
            border-radius: 5px;
        }
        ::-webkit-scrollbar-thumb {
            background-color: #555555;
            border-radius: 5px;
            border: 1px solid #252526;
        }
        ::-webkit-scrollbar-thumb:hover {
            background-color: #6a6a6a;
        }
        ::-webkit-scrollbar-corner {
            background: transparent;
        }
        """
        js = f"""
        (function() {{
            var style = document.createElement('style');
            style.type = 'text/css';
            style.innerHTML = {json.dumps(css)};
            document.head.appendChild(style);
        }})();
        """
        script = QWebEngineScript()
        script.setSourceCode(js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setRunsOnSubFrames(True)
        self.web_view.page().scripts().insert(script)

    def _handle_popout(self):
        if not WEBENGINE_AVAILABLE:
            return

        if self.popout_window is None:
            main_window = None
            if self.scene() and self.scene().views():
                main_window = self.scene().views()[0].window
            
            self.popout_window = HtmlPopoutWindow(parent_node=self, parent=main_window)
            self.popout_window.set_content(self.html_content)
            self.popout_window.show()
        else:
            self.popout_window.raise_()
            self.popout_window.activateWindow()

    def _on_content_changed(self):
        self.html_content = self.html_input.toPlainText()

    def render_html(self):
        """Renders the current HTML content in the web view."""
        if WEBENGINE_AVAILABLE:
            self.web_view.setHtml(self.html_content)
            if self.popout_window:
                self.popout_window.set_content(self.html_content)
    
    def get_html_content(self):
        """Returns the current HTML content from the input editor."""
        return self.html_content
    
    def set_html_content(self, html_text):
        """
        Sets the HTML content of the input editor and automatically renders it.
        
        Args:
            html_text (str): The HTML string to set.
        """
        self.html_content = html_text
        self.html_input.setHtml(html_text)
        self.render_html()

    def get_splitter_state(self):
        return self.splitter.sizes()

    def set_splitter_state(self, sizes):
        if sizes:
            self.splitter.setSizes(sizes)

    def boundingRect(self):
        padding = self.CONNECTION_DOT_OFFSET + self.CONNECTION_DOT_RADIUS
        return QRectF(-padding, 0, self.width + 2 * padding, self.height)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = get_current_palette()
        node_colors = get_graph_node_colors()
        
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        painter.setBrush(QColor("#2d2d2d"))
        
        node_color = node_colors["border"]
        pen = QPen(node_color, 1.5)

        if self.isSelected():
            pen = QPen(palette.SELECTION, 2)
        elif self.hovered:
            pen = QPen(QColor("#ffffff"), 2)
        
        painter.setPen(pen)
        painter.drawPath(path)
        
        dot_color = node_colors["dot"]
        if self.isSelected() or self.hovered:
            dot_color = pen.color().lighter(110) if self.isSelected() else node_colors["hover_dot"]
        
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        
        dot_rect_left = QRectF(-self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_left, 90 * 16, -180 * 16)
        
        dot_rect_right = QRectF(self.width - self.CONNECTION_DOT_RADIUS, (self.height / 2) - self.CONNECTION_DOT_RADIUS, self.CONNECTION_DOT_RADIUS * 2, self.CONNECTION_DOT_RADIUS * 2)
        painter.drawPie(dot_rect_right, 90 * 16, 180 * 16)

        if self.is_collapsed:
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(40, 0, self.width - 80, self.height), Qt.AlignmentFlag.AlignVCenter, "HTML Renderer")
            
            icon = qta.icon('fa5s.code', color=node_color.name())
            icon.paint(painter, QRect(10, 10, 20, 20))
            
            self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
            expand_icon = qta.icon('fa5s.expand-arrows-alt', color='#ffffff' if self.hovered else '#888888')
            expand_icon.paint(painter, QRect(int(self.width - 30), 10, 20, 20))
        else:
            if self.hovered:
                self.collapse_button_rect = QRectF(self.width - 35, 5, 30, 30)
                painter.setBrush(QColor(255, 255, 255, 30))
                painter.setPen(QColor(255, 255, 255, 150))
                painter.drawRoundedRect(self.collapse_button_rect.adjusted(6,6,-6,-6), 4, 4)
                
                icon_pen = QPen(QColor("#ffffff"), 2)
                painter.setPen(icon_pen)
                center = self.collapse_button_rect.center()
                painter.drawLine(int(center.x() - 4), int(center.y()), int(center.x() + 4), int(center.y()))
            else:
                self.collapse_button_rect = QRectF()

    def mousePressEvent(self, event):
        if self.collapse_button_rect.contains(event.pos()):
            self.toggle_collapse()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().is_dragging_item = True
            if hasattr(self.scene(), 'window'):
                self.scene().window.setCurrentNode(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.scene():
            self.scene().is_dragging_item = False
            self.scene()._clear_smart_guides()
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemSceneHasChanged and not self.scene():
            if self.popout_window:
                self.popout_window.close()
        
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionChange and self.scene() and self.scene().is_dragging_item:
            return self.scene().snap_position(self, value)
        if change == QGraphicsObject.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.scene().nodeMoved(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._handle_hover_enter(event)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        self._handle_hover_leave(event)
        super().hoverLeaveEvent(event)
