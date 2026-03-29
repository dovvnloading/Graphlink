import os
import webbrowser
from datetime import datetime

import qtawesome as qta
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QWidget, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QLabel, QPushButton, QMessageBox, QGraphicsDropShadowEffect,
    QInputDialog, QTextEdit, QTabWidget, QFormLayout, QComboBox, QGridLayout, QApplication,
    QCheckBox, QScrollArea, QRadioButton, QButtonGroup
)

import api_provider
import graphite_config as config
import graphite_licensing
from graphite_agents import ModelPullWorkerThread
from graphite_styles import StyleSheet, THEMES
from graphite_ui_components import CustomTitleBar
from graphite_config import apply_theme, get_current_palette, set_current_model


class ChatLibraryDialog(QDialog):
    """
    A dialog for managing saved chat sessions. It allows users to view, search,
    load, rename, and delete past conversations.
    """
    def __init__(self, session_manager, parent=None):
        """
        Initializes the ChatLibraryDialog.

        Args:
            session_manager (ChatSessionManager): The session manager instance for
                                                  database interactions.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.session_manager = session_manager

        self.setWindowTitle("Chat Library")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        self.resize(500, 600)
        self.on_theme_changed() # Apply initial theme

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Search bar for filtering the chat list.
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search chats...")
        self.search_input.textChanged.connect(self.filter_chats)
        main_layout.addWidget(self.search_input)
        
        # Toolbar with action buttons.
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        
        new_chat_btn = QPushButton(qta.icon('fa5s.plus', color='white'), "New Chat")
        new_chat_btn.clicked.connect(self.new_chat)
        toolbar.addWidget(new_chat_btn)
        
        delete_btn = QPushButton(qta.icon('fa5s.trash', color='white'), "Delete")
        delete_btn.clicked.connect(self.delete_selected)
        toolbar.addWidget(delete_btn)
        
        rename_btn = QPushButton(qta.icon('fa5s.edit', color='white'), "Rename")
        rename_btn.clicked.connect(self.rename_selected)
        toolbar.addWidget(rename_btn)
        
        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        main_layout.addWidget(toolbar_widget)
        
        # List widget to display saved chats.
        self.chat_list = QListWidget()
        self.chat_list.setAlternatingRowColors(True)
        self.chat_list.itemDoubleClicked.connect(self.load_chat)
        self.chat_list.setStyleSheet("""
            QListWidget {
                background-color: #2d2d2d;
                border: 1px solid #3f3f3f;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #3f3f3f;
            }
            QListWidget::item:alternate {
                background-color: #333333;
            }
            QListWidget::item:selected {
                background-color: #2ecc71;
                color: white;
            }
            QListWidget::item:hover {
                background-color: #3f3f3f;
            }
        """)
        main_layout.addWidget(self.chat_list)
        
        # Status bar to show the total number of chats.
        self.status_label = QLabel()
        main_layout.addWidget(self.status_label)
        
        self.refresh_chat_list()
        
        # Center the dialog relative to its parent.
        if parent:
            parent_center = parent.geometry().center()
            self.move(parent_center.x() - self.width() // 2,
                     parent_center.y() - self.height() // 2)

    def on_theme_changed(self):
        """Applies the current application theme's stylesheet to the dialog."""
        self.setStyleSheet(THEMES[config.CURRENT_THEME]["stylesheet"])

    def closeEvent(self, event):
        """Ensures proper cleanup when the dialog is closed."""
        event.accept()
        
    def refresh_chat_list(self):
        """Reloads and displays the list of chats from the database."""
        self.chat_list.clear()
        chats = self.session_manager.db.get_all_chats()
        
        for chat_id, title, created_at, updated_at in chats:
            item = QListWidgetItem()
            
            created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
            updated_dt = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
            
            display_text = f"{title}\n"
            display_text += f"Created: {created_dt.strftime('%Y-%m-%d %H:%M')}\n"
            display_text += f"Updated: {updated_dt.strftime('%Y-%m-%d %H:%M')}"
            
            item.setText(display_text)
            item.setData(Qt.ItemDataRole.UserRole, chat_id) # Store chat ID in the item.
            
            self.chat_list.addItem(item)
            
        self.update_status()
            
    def update_status(self):
        """Updates the status label with the current chat count."""
        count = self.chat_list.count()
        self.status_label.setText(f"Total chats: {count}")
        
    def filter_chats(self, text):
        """
        Filters the visibility of items in the chat list based on the search text.

        Args:
            text (str): The search query.
        """
        text = text.lower()
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            item.setHidden(text not in item.text().lower())
                
    def new_chat(self):
        """Initiates the creation of a new chat session in the main window."""
        if self.parent() and hasattr(self.parent(), 'new_chat'):
            if self.parent().new_chat(parent_for_dialog=self):
                self.close()
                
    def delete_selected(self):
        """Deletes the currently selected chat from the database after confirmation."""
        current_item = self.chat_list.currentItem()
        if current_item:
            chat_id = current_item.data(Qt.ItemDataRole.UserRole)
            reply = QMessageBox.question(
                self, 'Delete Chat',
                'Are you sure you want to delete this chat?\nThis action cannot be undone.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.session_manager.db.delete_chat(chat_id)
                self.refresh_chat_list()
                
    def rename_selected(self):
        """Opens an input dialog to rename the currently selected chat."""
        current_item = self.chat_list.currentItem()
        if current_item:
            chat_id = current_item.data(Qt.ItemDataRole.UserRole)
            current_title = current_item.text().split('\n')[0]
            
            new_title, ok = QInputDialog.getText(
                self, 'Rename Chat', 'Enter new title:', text=current_title
            )
            
            if ok and new_title:
                self.session_manager.db.rename_chat(chat_id, new_title)
                self.refresh_chat_list()
                
    def load_chat(self, item):
        """
        Loads the selected chat into the main window.

        Args:
            item (QListWidgetItem): The list item that was double-clicked.
        """
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            self.session_manager.load_chat(chat_id)
            if self.session_manager.window:
                self.session_manager.window.update_title_bar()
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load chat: {str(e)}")

class AboutDialog(QDialog):
    """A dialog displaying application information."""
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("About Graphite")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(True)
        self.resize(400, 250)
        self.on_theme_changed()
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        app_title = QLabel("Graphite")
        app_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2ecc71;")
        main_layout.addWidget(app_title, alignment=Qt.AlignmentFlag.AlignCenter)

        dev_label = QLabel("Developed by: Matthew Wesney")
        dev_label.setStyleSheet("font-size: 14px;")
        main_layout.addWidget(dev_label, alignment=Qt.AlignmentFlag.AlignCenter)
        
        contact_label = QLabel("Contact: dev.graphite@gmail.com")
        contact_label.setStyleSheet("font-size: 12px;")
        main_layout.addWidget(contact_label, alignment=Qt.AlignmentFlag.AlignCenter)

        github_link = QLabel('<a href="https://github.com/dovvnloading/Graphite" style="color: #3498db; text-decoration: none;">View on GitHub</a>')
        github_link.setOpenExternalLinks(False)
        github_link.linkActivated.connect(lambda url: webbrowser.open(url))
        main_layout.addWidget(github_link, alignment=Qt.AlignmentFlag.AlignCenter)
        
        main_layout.addStretch()
        
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)

    def on_theme_changed(self):
        self.setStyleSheet(THEMES[config.CURRENT_THEME]["stylesheet"])

class ColorPickerDialog(QDialog):
    """
    A small, frameless pop-up dialog for selecting a color from a predefined palette.
    It automatically closes when the user clicks outside of it.
    """
    def __init__(self, parent=None):
        """Initializes the ColorPickerDialog."""
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)

        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(15, 15, 15, 15)

        self.container = QWidget(self)
        self.container.setObjectName("colorPickerContainer")
        dialog_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 190))
        shadow.setOffset(0, 2)
        self.container.setGraphicsEffect(shadow)
        
        main_layout = QVBoxLayout(self.container)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        default_btn = QPushButton("Reset to Default")
        default_btn.setIcon(qta.icon('fa5s.undo', color='white'))
        default_btn.clicked.connect(lambda: self.color_selected(None, "default"))
        main_layout.addWidget(default_btn)

        # Helper function to create a grid of color buttons.
        def create_section(title, color_type, names_list):
            label = QLabel(title)
            label.setStyleSheet("color: #cccccc; font-size: 10px; margin-top: 5px;")
            main_layout.addWidget(label)
            
            grid_layout = QGridLayout()
            grid_layout.setSpacing(8)
            col, row = 0, 0
            
            frame_colors = get_current_palette().FRAME_COLORS
            
            for name in names_list:
                color_data = frame_colors[name]
                btn = QPushButton()
                btn.setFixedSize(28, 28)
                btn.setToolTip(name)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                
                # Style "header" colors differently to show they only affect the header.
                style = f"""
                    QPushButton {{ background-color: {color_data["color"]}; border: 2px solid #3f3f3f; border-radius: 14px; }}
                    QPushButton:hover {{ border: 2px solid #ffffff; }}
                """
                if color_type == "header":
                    style = f"""
                        QPushButton {{
                            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {color_data["color"]}, stop:0.4 {color_data["color"]},
                                stop:0.41 #3f3f3f, stop:1 #3f3f3f);
                            border: 2px solid #3f3f3f; border-radius: 14px;
                        }}
                        QPushButton:hover {{ border: 2px solid #ffffff; }}
                    """
                btn.setStyleSheet(style)
                btn.clicked.connect(lambda checked, c=color_data: self.color_selected(c["color"], c["type"]))
                
                grid_layout.addWidget(btn, row, col)
                col = (col + 1) % 5
                if col == 0: row += 1
            main_layout.addLayout(grid_layout)

        # Define the color groups.
        frame_colors = get_current_palette().FRAME_COLORS
        full_color_names =[k for k, v in frame_colors.items() if v['type'] == 'full' and 'Gray' not in k]
        header_color_names =[k for k, v in frame_colors.items() if v['type'] == 'header']
        mono_color_names =[k for k, v in frame_colors.items() if 'Gray' in k]
        
        create_section("Frame Colors", "full", full_color_names)
        create_section("Header Colors Only", "header", header_color_names)
        create_section("Monochrome", "full", mono_color_names)
        
        main_layout.addStretch()
        
        self.setStyleSheet("""
            QDialog { background: transparent; }
            QWidget#colorPickerContainer { background-color: #252526; border-radius: 8px; }
            QPushButton { background-color: #3f3f3f; border-radius: 5px; padding: 8px; }
            QPushButton:hover { background-color: #555555; }
        """)
        
        self.selected_color = None
        self.selected_type = None
        
    def showEvent(self, event):
        """Installs an event filter to detect clicks outside the dialog."""
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        """Removes the event filter when the dialog is hidden."""
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        """
        Filters application-wide events to close the dialog if a mouse click
        occurs outside its boundaries.
        """
        if self.isVisible() and event.type() == QEvent.Type.MouseButtonPress:
            # If the click is not inside the visible container area, close the dialog.
            if not self.container.geometry().contains(self.mapFromGlobal(event.globalPos())):
                self.close()
                return True # Event handled.
        return super().eventFilter(watched, event)
        
    def color_selected(self, color, color_type):
        """
        Stores the selected color and type, then accepts the dialog.

        Args:
            color (str or None): The hex color string, or None for default.
            color_type (str): The type of color ('full', 'header', 'default').
        """
        self.selected_color = color
        self.selected_type = color_type
        self.accept()
        
    def get_selected_color(self):
        """Returns the color and type selected by the user."""
        return self.selected_color, self.selected_type

class PinEditDialog(QDialog):
    """A dialog for editing the title and note of a NavigationPin."""
    def __init__(self, title="", note="", parent=None):
        """
        Initializes the PinEditDialog.

        Args:
            title (str, optional): The initial title for the pin.
            note (str, optional): The initial note for the pin.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(300, 200)
        
        self.container = QWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.container)
        
        container_layout = QVBoxLayout(self.container)
        container_layout.setSpacing(10)
        container_layout.setContentsMargins(20, 20, 20, 20)
        
        container_layout.addWidget(QLabel("Pin Title"))
        self.title_input = QLineEdit(title)
        self.title_input.setPlaceholderText("Enter pin title...")
        container_layout.addWidget(self.title_input)
        
        container_layout.addWidget(QLabel("Note"))
        self.note_input = QTextEdit()
        self.note_input.setPlaceholderText("Add a note...")
        self.note_input.setText(note)
        self.note_input.setMaximumHeight(80)
        container_layout.addWidget(self.note_input)
        
        button_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(save_btn)
        button_layout.addWidget(cancel_btn)
        container_layout.addLayout(button_layout)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 0)
        self.container.setGraphicsEffect(shadow)
        
        self.container.setStyleSheet("""
            QWidget { background-color: #2d2d2d; border-radius: 10px; }
            QLabel { color: white; font-size: 12px; }
            QLineEdit, QTextEdit {
                background-color: #3f3f3f; border: none; border-radius: 5px;
                padding: 5px; color: white;
            }
            QPushButton {
                background-color: #2ecc71; border: none; border-radius: 5px;
                padding: 8px 16px; color: white;
            }
            QPushButton:hover { background-color: #27ae60; }
        """)

class HelpDialog(QDialog):
    """A dialog displaying help information and keyboard shortcuts in a tabbed view."""
    def __init__(self, parent=None):
        """Initializes the HelpDialog."""
        super().__init__(parent)
        self.setWindowTitle("Graphite Help")
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        self.resize(800, 600)
        self.on_theme_changed()

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        tab_widget = QTabWidget()
        tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #3f3f3f; background: #2d2d2d; border-radius: 4px; }
            QTabBar::tab {
                background: #252526; color: #ffffff; padding: 8px 16px;
                border: 1px solid #3f3f3f; border-bottom: none;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                min-width: 100px;
            }
            QTabBar::tab:selected { background: #2d2d2d; border-bottom: 2px solid #2ecc71; }
            QTabBar::tab:hover { background: #333333; }
        """)

        # Helper to create a scrollable tab with formatted sections.
        def create_scrollable_tab(sections):
            tab_content = QWidget()
            tab_layout = QVBoxLayout(tab_content)
            for title, items in sections:
                tab_layout.addWidget(self._create_section(title, items))
            tab_layout.addStretch()

            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setWidget(tab_content)
            scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            
            final_tab_widget = QWidget()
            final_layout = QVBoxLayout(final_tab_widget)
            final_layout.setContentsMargins(0,0,0,0)
            final_layout.addWidget(scroll_area)
            return final_tab_widget

        # --- Define content for each tab ---
        nav_tab = create_scrollable_tab([
            ("Mouse Navigation",[
                ("Pan View", "Hold the Middle Mouse Button and drag the cursor across the canvas to move your view.", "fa5s.hand-paper"),
                ("Zoom", "Hold Ctrl and use the Mouse Wheel to zoom in and out. Alternatively, use the toolbar buttons.", "fa5s.search-plus"),
                ("Zoom to Selection", "Hold Shift and drag to draw a box. Releasing the mouse will zoom the view to fit that area.", "fa5s.search"),
                ("Select Items", "Click an item to select it. Drag on an empty area of the canvas to draw a selection box.", "fa5s.mouse-pointer"),
                ("Move Items", "Click and drag any selected item or group of items to reposition them on the canvas.", "fa5s.arrows-alt"),
            ]),
            ("View Controls",[
                ("Reset View", "Click the 'Reset' button in the toolbar to instantly restore the default zoom level and position.", "fa5s.undo"),
                ("Fit All", "Click the 'Fit All' button to automatically adjust the view to show all items on the canvas.", "fa5s.expand")
            ])
        ])
        tab_widget.addTab(nav_tab, "Navigation")
        # ... (Other tabs follow the same pattern) ...
        chat_tab = create_scrollable_tab([
            ("Chat Interaction",[
                ("Send Message", "Type in the input bar and press Enter or click the Send button to create a new node.", "fa5s.paper-plane"),
                ("Select Context", "Click any node to make it the active context. Your next message will branch off from that selected node.", "fa5s.comment"),
                ("Attach Files", "Click the paperclip icon to attach an image or document to your message. The content will be included for the AI's context.", "fa5s.paperclip"),
                ("Export Content", "Right-click a node and use the 'Export to Doc' menu to save its content to various formats like .txt, .pdf, or .html.", "fa5s.file-export"),
                ("Save/Load Chat", "Use the 'Library' (Ctrl+L) to open, save, rename, or delete your past conversations.", "fa5s.folder-open")
            ])
        ])
        tab_widget.addTab(chat_tab, "Chat Features")
        org_tab = create_scrollable_tab([
             ("Organization Tools",[
                ("Create Frame", "Select items and press Ctrl+G to group them within a resizable frame.", "fa5s.object-group"),
                ("Create Container", "Select items and press Ctrl+Shift+G to group them in a container that moves with its contents.", "fa5s.box-open"),
                ("Edit Group Title", "Double-click a Frame or Container's title bar to edit its text. Press Enter to save.", "fa5s.edit"),
                ("Color Groups", "Click the multi-colored icon in a group's header to change its background or header color.", "fa5s.palette"),
                ("Auto-Organize", "Click the 'Organize' button in the toolbar to automatically arrange nodes in a logical tree layout.", "fa5s.sitemap")
            ])
        ])
        tab_widget.addTab(org_tab, "Organization")
        plugins_tab = create_scrollable_tab([
            ("Plugins & Tools",[
                ("Workflow Architect", "Access via the 'Plugins' menu. Builds an execution blueprint and recommends which specialist nodes to add next.", "fa5s.project-diagram"),
                ("Quality Gate", "Access via the 'Plugins' menu. Runs a release-readiness review on the current branch and highlights the next best remediation steps.", "fa5s.check-circle"),
                ("Gitlink", "Access via the 'Plugins' menu. Pulls GitHub repo context into structured XML, previews proposed file changes, and only writes approved changes to a local repo path.", "fa5s.link"),
                ("Py-Coder", "Access via the 'Plugins' menu. An environment for AI-driven code generation, execution, and analysis.", "fa5s.code"),
                ("System Prompt", "Access via the 'Plugins' menu. Overrides the default AI personality for a specific conversation branch.", "fa5s.cog"),
                ("Generate Takeaway", "Right-click a node to create a new note with a concise summary of its content.", "fa5s.lightbulb"),
                ("Generate Explainer", "Right-click a node to create a note with a simplified explanation of complex topics.", "fa5s.question-circle"),
                ("Generate Chart", "Right-click a node containing data to create various chart visualizations.", "fa5s.chart-bar")
            ])
        ])
        tab_widget.addTab(plugins_tab, "Plugins & Tools")
        shortcuts_tab = create_scrollable_tab([
            ("Keyboard Shortcuts",[
                ("W, A, S, D", "Pan the canvas view.", "fa5s.arrows-alt"),
                ("Q / E", "Zoom out / Zoom in.", "fa5s.search"),
                ("Ctrl + Arrow Keys", "Navigate between parent, child, and sibling nodes in a branch.", "fa5s.project-diagram"),
                ("Ctrl + K", "Open the Command Palette for quick access to all commands.", "fa5s.terminal"),
                ("Ctrl + F", "Open the text search overlay to find content within nodes.", "fa5s.search"),
                ("Ctrl + T", "Start a new chat session.", "fa5s.plus-square"),
                ("Ctrl + L", "Open the Chat Library.", "fa5s.book"),
                ("Ctrl + S", "Save the current chat session.", "fa5s.save"),
                ("Ctrl + N", "Create a new Note at the cursor's position.", "fa5s.sticky-note"),
                ("Ctrl + G", "Create a Frame from the current selection.", "fa5s.object-group"),
                ("Ctrl + Shift + G", "Create a Container from the current selection.", "fa5s.box-open"),
                ("Delete", "Delete all selected items.", "fa5s.trash-alt"),
                ("Ctrl + Left-Click (on line)", "Add a pin to a connection line to curve it.", "fa5s.dot-circle"),
                ("Ctrl + Right-Click (on pin)", "Remove a pin from a connection line.", "fa5s.times-circle"),
            ])
        ])
        tab_widget.addTab(shortcuts_tab, "Shortcuts")
        
        main_layout.addWidget(tab_widget)

    def on_theme_changed(self):
        """Applies the current theme's stylesheet."""
        self.setStyleSheet(THEMES[config.CURRENT_THEME]["stylesheet"])

    def _create_section(self, title, items):
        """
        Helper function to create a formatted section with a title and a list of items.

        Args:
            title (str): The title of the section.
            items (list[tuple]): A list of tuples, each containing (action, description, icon_name).

        Returns:
            QWidget: The formatted section widget.
        """
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setSpacing(15)

        title_label = QLabel(title)
        title_label.setStyleSheet("QLabel { color: #2ecc71; font-size: 16px; font-weight: bold; padding-bottom: 10px; }")
        layout.addWidget(title_label)

        for action, description, icon_name in items:
            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setSpacing(15)
            item_layout.setContentsMargins(0,0,0,0)

            icon_label = QLabel()
            icon = qta.icon(icon_name, color='#2ecc71')
            icon_label.setPixmap(icon.pixmap(24, 24))
            icon_label.setFixedWidth(30)
            item_layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignTop)

            text_widget = QWidget()
            text_layout = QVBoxLayout(text_widget)
            text_layout.setSpacing(4)
            text_layout.setContentsMargins(0,0,0,0)

            action_label = QLabel(action)
            action_label.setStyleSheet("color: white; font-weight: bold;")
            text_layout.addWidget(action_label)

            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("color: #aaaaaa;")
            text_layout.addWidget(desc_label)

            item_layout.addWidget(text_widget)
            item_layout.addStretch()
            layout.addWidget(item_widget)

        layout.addStretch()
        return section

class OllamaSettingsWidget(QWidget):
    """A settings widget for configuring the local Ollama provider."""
    def __init__(self, license_manager, parent=None):
        """Initializes the OllamaSettingsWidget."""
        super().__init__(parent)
        self.license_manager = license_manager
        self.worker_thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        info_label = QLabel("Configure the default model for chat tasks and the reasoning mode when using the local Ollama provider.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #d4d4d4; margin-bottom: 15px;")
        layout.addWidget(info_label)
        
        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # --- Reasoning Mode ---
        reasoning_mode_label = QLabel("Reasoning Mode:")
        reasoning_mode_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        
        self.thinking_radio = QRadioButton("Thinking Mode (Enable CoT)")
        self.thinking_radio.setToolTip("Instructs the model to provide a step-by-step reasoning process. Best for complex queries.")
        
        self.quick_radio = QRadioButton("Quick Mode (No CoT)")
        self.quick_radio.setToolTip("Instructs the model to provide a direct answer without showing its reasoning. Faster for simple queries.")
        
        self.reasoning_group = QButtonGroup(self)
        self.reasoning_group.addButton(self.thinking_radio)
        self.reasoning_group.addButton(self.quick_radio)

        reasoning_layout = QHBoxLayout()
        reasoning_layout.addWidget(self.thinking_radio)
        reasoning_layout.addWidget(self.quick_radio)
        reasoning_layout.addStretch()
        
        form_layout.addRow(reasoning_mode_label, reasoning_layout)

        saved_mode = self.license_manager.get_ollama_reasoning_mode()
        if saved_mode == "Thinking":
            self.thinking_radio.setChecked(True)
        else:
            self.quick_radio.setChecked(True)

        # --- Model Selection ---
        self.models =[
            'qwen2.5:7b-instruct', 'qwen3:8b', 'qwen3:14b', 'deepseek-r1:14b', 'phi3:14b', 'mistral:7b',
            'gpt-oss:20b', 'qwen3-vl:8b', 'deepseek-coder:6.7b', 'gemma3:4b', 'gemma3:12b'
        ]
        
        saved_model = self.license_manager.get_ollama_chat_model()

        self.current_model_label = QLabel(f"<b>{saved_model}</b>")
        self.current_model_label.setStyleSheet("color: #2ecc71;")
        form_layout.addRow("Current Active Chat Model:", self.current_model_label)

        self.model_combo = QComboBox()
        self.model_combo.addItems([""] + self.models)
        self.model_combo.currentTextChanged.connect(self.on_combo_change)
        form_layout.addRow("Preset Model:", self.model_combo)

        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("e.g., llama3:latest")
        self.model_input.textChanged.connect(self.on_text_change)
        form_layout.addRow("Custom Model Name:", self.model_input)
        
        layout.addLayout(form_layout)

        self.model_input.setText(saved_model)

        self.status_label = QLabel("Enter a model name to validate and set it.")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setStyleSheet("color: #e67e22; min-height: 40px;")
        layout.addWidget(self.status_label)
        layout.addStretch()

        button_layout = QHBoxLayout()
        self.validate_button = QPushButton("Validate and Pull Model")
        self.validate_button.clicked.connect(self.validate_model)
        self.save_button = QPushButton("Save Settings")
        self.save_button.clicked.connect(self.save_settings)
        button_layout.addStretch()
        button_layout.addWidget(self.validate_button)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        self.on_theme_changed()

    def on_theme_changed(self):
        palette = get_current_palette()
        selection_color = palette.SELECTION.name()
        selection_border = palette.SELECTION.darker(110).name()

        self.setStyleSheet(f"""
            QRadioButton {{
                color: #cccccc;
                font-size: 11px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
            }}
            QRadioButton::indicator:unchecked {{
                border: 1px solid #555;
                background-color: #3f3f3f;
                border-radius: 4px;
            }}
            QRadioButton::indicator:checked {{
                background-color: {selection_color};
                border: 1px solid {selection_border};
                image: url(C:/Users/Admin/source/repos/graphite_app/assets/check.png);
                border-radius: 4px;
            }}
        """)

    def save_settings(self):
        """Saves the entered model name as the default chat model."""
        model_name = self.model_input.text().strip()
        if not model_name:
            QMessageBox.warning(self, "Warning", "Model name cannot be empty.")
            return
        
        reasoning_mode = "Thinking" if self.thinking_radio.isChecked() else "Quick"
        
        # Persist the settings
        self.license_manager.set_ollama_chat_model(model_name)
        self.license_manager.set_ollama_reasoning_mode(reasoning_mode)

        # Apply the setting for the current session
        set_current_model(model_name)

        # Re-initialize the agent in the main window to apply the prompt change
        main_window = self.window().parent()
        if main_window and hasattr(main_window, 'reinitialize_agent'):
            main_window.reinitialize_agent()

        self.current_model_label.setText(f"<b>{model_name}</b>")
        QMessageBox.information(self, "Saved", f"Ollama settings have been saved and applied for the current session.")

    def on_combo_change(self, text):
        """Updates the text input when a preset model is selected from the combobox."""
        if not text: return
        # Temporarily disconnect the signal to prevent a feedback loop.
        self.model_input.textChanged.disconnect(self.on_text_change)
        self.model_input.setText(text)
        self.model_input.textChanged.connect(self.on_text_change)

    def on_text_change(self, text):
        """Updates the combobox selection when the text input is manually changed."""
        self.model_combo.currentTextChanged.disconnect(self.on_combo_change)
        if text in self.models:
            self.model_combo.setCurrentText(text)
        else:
            self.model_combo.setCurrentIndex(0) # Deselect if not a preset.
        self.model_combo.currentTextChanged.connect(self.on_combo_change)

    def validate_model(self):
        """
        Starts a background thread to pull the specified Ollama model, providing
        UI feedback on the process.
        """
        model_name = self.model_input.text().strip()
        if not model_name:
            self.status_label.setText("Model name cannot be empty.")
            return

        self.validate_button.setEnabled(False)
        self.validate_button.setText("Validating...")
        
        self.worker_thread = ModelPullWorkerThread(model_name)
        self.worker_thread.status_update.connect(self.handle_status_update)
        self.worker_thread.finished.connect(self.handle_worker_finished)
        self.worker_thread.error.connect(self.handle_worker_error)
        self.worker_thread.start()

    def handle_status_update(self, message):
        """Updates the status label with progress from the worker thread."""
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #3498db;")

    def handle_worker_finished(self, message, model_name):
        """Handles the successful completion of the model pull."""
        self.current_model_label.setText(f"<b>{model_name}</b>")
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #2ecc71;")
        self.reset_button()
        QMessageBox.information(self, "Success", message)

    def handle_worker_error(self, error_message):
        """Handles errors from the model pull worker thread."""
        self.status_label.setText(f"Error: {error_message}")
        self.status_label.setStyleSheet("color: #e74c3c;")
        self.reset_button()
        QMessageBox.warning(self, "Model Error", error_message)

    def reset_button(self):
        """Resets the state of the validation button."""
        self.validate_button.setEnabled(True)
        self.validate_button.setText("Validate and Pull Model")

class ApiSettingsWidget(QWidget):
    """A settings widget for configuring API-based providers like OpenAI and Gemini."""
    def __init__(self, parent=None):
        """Initializes the ApiSettingsWidget."""
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        layout.addWidget(QLabel("API Provider:", styleSheet="color: #ffffff; font-weight: bold;"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItems([config.API_PROVIDER_OPENAI, config.API_PROVIDER_GEMINI])
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        info = QLabel(
            "Configure your API endpoint.\n"
            "OpenAI-Compatible works with: OpenAI, LiteLLM, Anthropic, OpenRouter, etc.\n\n"
            "Choose different models for different tasks."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #d4d4d4; margin-bottom: 15px; margin-top: 10px;")
        layout.addWidget(info)

        self.base_url_label = QLabel("Base URL:")
        self.base_url_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        layout.addWidget(self.base_url_label)
        self.base_url_input = QLineEdit(os.getenv('GRAPHITE_API_BASE', 'https://api.openai.com/v1'), placeholderText="https://api.openai.com/v1")
        layout.addWidget(self.base_url_input)

        layout.addWidget(QLabel("API Key:", styleSheet="color: #ffffff; font-weight: bold; margin-top: 10px;"))
        self.api_key_input = QLineEdit(os.getenv('GRAPHITE_API_KEY', ''), echoMode=QLineEdit.Password, placeholderText="Enter your API key...")
        layout.addWidget(self.api_key_input)

        self.load_btn = QPushButton("Load Models from Endpoint")
        self.load_btn.clicked.connect(self.load_models_from_endpoint)
        layout.addWidget(self.load_btn)

        layout.addWidget(QLabel("Model Selection (per task):", styleSheet="color: #ffffff; font-weight: bold; margin-top: 15px;"))

        self.model_combos = {}
        # Create comboboxes for each specific task.
        layout.addWidget(QLabel("Title Generation (fast, cheap model):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.title_combo = QComboBox(placeholderText="Select model...")
        self.model_combos[config.TASK_TITLE] = self.title_combo
        layout.addWidget(self.title_combo)
        # ... (other task comboboxes) ...
        layout.addWidget(QLabel("Chat, Explain, Takeaways (main model):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.chat_combo = QComboBox(placeholderText="Select model...")
        self.model_combos[config.TASK_CHAT] = self.chat_combo
        layout.addWidget(self.chat_combo)

        layout.addWidget(QLabel("Chart Generation (code-capable model):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.chart_combo = QComboBox(placeholderText="Select model...")
        self.model_combos[config.TASK_CHART] = self.chart_combo
        layout.addWidget(self.chart_combo)
        
        layout.addWidget(QLabel("Web Content Validation (fastest model, Gemini-only):", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.web_validate_combo = QComboBox(placeholderText="Default: gemini-3.1-flash-lite-preview")
        self.model_combos[config.TASK_WEB_VALIDATE] = self.web_validate_combo
        layout.addWidget(self.web_validate_combo)

        layout.addWidget(QLabel("Web Content Summarization:", styleSheet="color: #d4d4d4; margin-top: 8px;"))
        self.web_summarize_combo = QComboBox(placeholderText="Select model...")
        self.model_combos[config.TASK_WEB_SUMMARIZE] = self.web_summarize_combo
        layout.addWidget(self.web_summarize_combo)

        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.save_button = QPushButton("Save Configuration")
        self.save_button.clicked.connect(self.save_settings)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        # Initialize UI based on saved settings.
        saved_provider = os.getenv('GRAPHITE_API_PROVIDER', config.API_PROVIDER_OPENAI)
        self.provider_combo.setCurrentText(saved_provider)
        self._on_provider_changed(saved_provider)

    def _populate_models(self, models):
        """Helper function to populate all model dropdowns with a given list."""
        for combo in self.model_combos.values():
            combo.clear()
            combo.addItems(models)
    
    def _on_provider_changed(self, provider_name):
        """Dynamically adjusts the UI based on the selected API provider."""
        is_openai = (provider_name == config.API_PROVIDER_OPENAI)
        
        self.base_url_label.setVisible(is_openai)
        self.base_url_input.setVisible(is_openai)
        self.load_btn.setVisible(is_openai)
        
        # Web validation is a special case, currently Gemini-only.
        self.web_validate_combo.clear()
        self.web_validate_combo.addItems(api_provider.GEMINI_MODELS_STATIC)
        self.web_validate_combo.setEnabled(provider_name == config.API_PROVIDER_GEMINI)

        default_idx = self.web_validate_combo.findText("gemini-3.1-flash-lite-preview")
        if default_idx >= 0:
            self.web_validate_combo.setCurrentIndex(default_idx)

        # Update API key and model lists.
        if is_openai:
            self.api_key_input.setPlaceholderText("Enter your OpenAI-compatible API key...")
            self.api_key_input.setText(os.getenv('GRAPHITE_OPENAI_API_KEY', ''))
            self._populate_models([]) # Clear models until user loads them.
        else: # Gemini
            self.api_key_input.setPlaceholderText("Enter your Google Gemini API key...")
            self.api_key_input.setText(os.getenv('GRAPHITE_GEMINI_API_KEY', ''))
            for task, combo in self.model_combos.items():
                if task != config.TASK_WEB_VALIDATE:
                    combo.clear()
                    combo.addItems(api_provider.GEMINI_MODELS_STATIC)

    def load_models_from_endpoint(self):
        """Fetches the list of available models from the configured API endpoint."""
        provider = self.provider_combo.currentText()
        base_url = self.base_url_input.text().strip()
        api_key = self.api_key_input.text().strip()

        if provider == config.API_PROVIDER_OPENAI and not base_url:
            QMessageBox.warning(self, "Missing Information", "Please enter the Base URL for the OpenAI-compatible provider.")
            return
        if not api_key:
            QMessageBox.warning(self, "Missing Information", "Please enter the API Key.")
            return

        try:
            api_provider.initialize_api(provider, api_key, base_url if provider == config.API_PROVIDER_OPENAI else None)
            models = api_provider.get_available_models()
            
            if provider == config.API_PROVIDER_OPENAI:
                 for task, combo in self.model_combos.items():
                    if task != config.TASK_WEB_VALIDATE:
                        combo.clear()
                        combo.addItems(models)
            
            QMessageBox.information(self, "Models Loaded", f"Successfully loaded {len(models)} models!")
        except Exception as e:
            QMessageBox.critical(self, "Failed to Load Models", f"Could not fetch models from API:\n\n{str(e)}")

    def save_settings(self):
        """Saves the configured API settings as environment variables and updates the provider."""
        provider = self.provider_combo.currentText()
        base_url = self.base_url_input.text().strip()
        api_key = self.api_key_input.text().strip()

        if not api_key:
            QMessageBox.warning(self, "Missing API Key", "Please enter your API Key.")
            return
            
        # Validate that all necessary models have been selected.
        tasks_to_check =[t for t in self.model_combos.keys() if t != config.TASK_WEB_VALIDATE or provider == config.API_PROVIDER_GEMINI]
        for task_key in tasks_to_check:
            if not self.model_combos[task_key].currentText():
                QMessageBox.warning(self, "Missing Model Selection", f"Please select a model for task: {task_key}")
                return

        # Save settings to environment variables.
        os.environ['GRAPHITE_API_PROVIDER'] = provider
        if provider == config.API_PROVIDER_OPENAI:
            os.environ['GRAPHITE_OPENAI_API_KEY'] = api_key
            os.environ['GRAPHITE_API_BASE'] = base_url
        else:
             os.environ['GRAPHITE_GEMINI_API_KEY'] = api_key

        # Update the API provider with the selected models.
        for task_key, combo in self.model_combos.items():
            if combo.currentText() or (task_key == config.TASK_WEB_VALIDATE and provider == config.API_PROVIDER_GEMINI):
                api_provider.set_task_model(task_key, combo.currentText())
        
        QMessageBox.information(self, "Configuration Saved", f"API settings for {provider} have been saved.")

class AppearanceSettingsWidget(QWidget):
    """A settings widget for controlling application appearance and behavior."""
    def __init__(self, license_manager, parent=None):
        """Initializes the AppearanceSettingsWidget."""
        super().__init__(parent)
        self.license_manager = license_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        
        self.desaturate_checkbox = QCheckBox("Enable Monochromatic Mode: Graphite needs to be reset for this to be applied")
        self.desaturate_checkbox.setToolTip("Reduces color saturation for a grayscale-like appearance. The app needs reset for this to go into full effect.")
        self.desaturate_checkbox.setChecked(self.license_manager.get_theme() == "mono")
        layout.addWidget(self.desaturate_checkbox)

        self.show_welcome_checkbox = QCheckBox("Show Welcome Screen on Startup")
        self.show_welcome_checkbox.setToolTip("If unchecked, the application will open directly to your last session.")
        self.show_welcome_checkbox.setChecked(self.license_manager.get_show_welcome_screen())
        layout.addWidget(self.show_welcome_checkbox)

        self.show_token_counter_checkbox = QCheckBox("Show Token Counter Overlay")
        self.show_token_counter_checkbox.setToolTip("Displays an overlay with token usage for the current session.")
        self.show_token_counter_checkbox.setChecked(self.license_manager.get_show_token_counter())
        layout.addWidget(self.show_token_counter_checkbox)
        
        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply_settings)
        button_layout.addWidget(self.apply_button)
        layout.addLayout(button_layout)

    def apply_settings(self):
        """Saves the selected appearance settings and notifies the application."""
        theme_name = "mono" if self.desaturate_checkbox.isChecked() else "dark"
        self.license_manager.set_theme(theme_name)
        
        self.license_manager.set_show_welcome_screen(self.show_welcome_checkbox.isChecked())
        self.license_manager.set_show_token_counter(self.show_token_counter_checkbox.isChecked())

        # Notify the application and main window of the changes.
        app = QApplication.instance()
        apply_theme(app, theme_name)
        
        main_window = self.window().parent()
        if main_window and hasattr(main_window, 'on_settings_changed'):
            main_window.on_settings_changed()

        QMessageBox.information(self, "Settings Applied", "Appearance settings have been saved.")

class SettingsDialog(QDialog):
    """
    A unified, tabbed dialog for all application settings, including appearance,
    Ollama configuration, and API endpoint configuration.
    """
    def __init__(self, license_manager, parent=None):
        """Initializes the SettingsDialog."""
        super().__init__(parent)
        self.license_manager = license_manager
        self.setWindowTitle("Settings")
        self.setMinimumWidth(750)
        self.setMinimumHeight(600)
        self.on_theme_changed()

        main_layout = QVBoxLayout(self)
        
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)
        
        # Create and add the settings tabs.
        self.appearance_tab = AppearanceSettingsWidget(self.license_manager)
        self.ollama_tab = OllamaSettingsWidget(self.license_manager)
        self.api_tab = ApiSettingsWidget()
        
        self.tab_widget.addTab(self.appearance_tab, "Appearance")
        self.tab_widget.addTab(self.ollama_tab, "Ollama (Local)")
        self.tab_widget.addTab(self.api_tab, "API Endpoint")

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        button_layout.addWidget(self.close_button)
        main_layout.addLayout(button_layout)
        
    def on_theme_changed(self):
        """Applies the current theme's stylesheet."""
        self.setStyleSheet(THEMES[config.CURRENT_THEME]["stylesheet"])
