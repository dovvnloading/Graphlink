from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QWidget, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QGraphicsDropShadowEffect
)

class CommandManager:
    """
    Manages the registration and retrieval of application-wide commands.

    This class acts as a central registry for all actions that can be invoked
    through the command palette. It decouples the command's definition (its name,
    aliases, callback function, and availability condition) from the UI that
    presents it. This makes it easy to add, remove, or modify commands without
    changing the command palette's implementation.
    """
    def __init__(self):
        """Initializes the CommandManager, creating an empty list to store commands."""
        # A list to store all registered command dictionaries.
        self.commands = []

    def register_command(self, name, aliases, callback, condition=None):
        """
        Registers a new command with the manager.

        Args:
            name (str): The primary display name of the command.
            aliases (list[str]): A list of alternative names or search keywords
                                 to help users find the command.
            callback (function): The function to execute when the command is triggered.
            condition (function, optional): A function that returns True if the command
                                            is currently available to the user, or False
                                            if it should be hidden. If None, the command
                                            is always considered available. This is used
                                            for context-sensitive commands. Defaults to None.
        """
        self.commands.append({
            'name': name,
            'aliases': [name.lower()] + [alias.lower() for alias in aliases],
            'callback': callback,
            'condition': condition or (lambda: True) # Default condition is always true.
        })
        # Sort commands alphabetically by name for a consistent and predictable display in the UI.
        self.commands.sort(key=lambda cmd: cmd['name'])

    def get_available_commands(self):
        """
        Returns a list of all registered commands whose conditions are currently met.

        This method is called by the UI (e.g., the command palette) to get the list
        of commands that should be displayed to the user at that moment.

        Returns:
            list[dict]: A list of command dictionaries that are currently active and available.
        """
        return [cmd for cmd in self.commands if cmd['condition']()]

class CommandPaletteDialog(QDialog):
    """
    A floating, searchable dialog for finding and executing application commands.

    This dialog provides a quick, keyboard-driven way for users to access application
    functionality, similar to the command palettes found in modern code editors like
    VS Code or Sublime Text. It presents a list of available commands and filters them
    as the user types.
    """
    def __init__(self, commands, parent=None):
        """
        Initializes the CommandPaletteDialog.

        Args:
            commands (list[dict]): A list of available command dictionaries, typically
                                   retrieved from `CommandManager.get_available_commands()`.
            parent (QWidget, optional): The parent widget. Defaults to None.
        """
        super().__init__(parent)
        self.commands = commands
        self.selected_command = None

        # Configure window flags for a frameless, translucent, modal dialog that floats on top.
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(600, 400)

        # The main layout has margins to ensure the drop shadow is visible around the canvas.
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # A central 'canvas' widget holds all visible UI elements and the shadow effect.
        # This is a common pattern for creating custom-styled, frameless windows.
        self.canvas = QWidget(self)
        self.canvas.setObjectName("commandPaletteCanvas")
        main_layout.addWidget(self.canvas)

        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(8, 8, 8, 8)
        canvas_layout.setSpacing(8)

        # Input field for searching/filtering commands.
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type a command...")
        self.search_input.textChanged.connect(self._filter_commands)
        canvas_layout.addWidget(self.search_input)

        # List widget to display the filtered command results.
        self.results_list = QListWidget()
        canvas_layout.addWidget(self.results_list)

        # Apply a drop shadow to the canvas for a floating, modern appearance.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 190))
        shadow.setOffset(0, 2)
        self.canvas.setGraphicsEffect(shadow)

        self.setStyleSheet("""
            QDialog {
                background: transparent;
            }
            QWidget#commandPaletteCanvas {
                background-color: #252526;
                color: #ffffff;
                border-radius: 8px;
            }
            QLineEdit {
                padding: 10px;
                font-size: 14px;
                background-color: #1e1e1e;
                border: 1px solid #3f3f3f;
                border-radius: 4px;
            }
            QListWidget {
                border: 1px solid #3f3f3f;
                background-color: #2d2d2d;
                font-size: 13px;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 10px;
            }
            QListWidget::item:selected {
                background-color: #2ecc71;
                color: white;
            }
        """)

        # Initially populate the list with all available commands.
        self._filter_commands("")
        self.search_input.setFocus()
        self.results_list.itemActivated.connect(self._execute_command)

    def _filter_commands(self, text):
        """
        Filters the command list based on the text in the search input.

        This method is connected to the `textChanged` signal of the search input.
        It clears and repopulates the results list with commands that match the
        search term.

        Args:
            text (str): The search term from the input field.
        """
        self.results_list.clear()
        search_term = text.lower().strip()
        
        for command in self.commands:
            # A command is a match if the search term is found in its name or any of its aliases.
            is_match = any(search_term in alias for alias in command['aliases'])
            if is_match:
                item = QListWidgetItem(command['name'])
                # Store the entire command dictionary in the item's data role.
                # This is the standard Qt way to associate complex data with a list item.
                item.setData(Qt.ItemDataRole.UserRole, command)
                self.results_list.addItem(item)
        
        # Select the first item in the list by default for quick execution.
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
    
    def _execute_command(self):
        """
        Finalizes the dialog by setting the selected command and closing.

        This method is called when an item is activated (e.g., by pressing Enter
        or double-clicking). It retrieves the selected command's data and accepts
        the dialog, signaling that a choice has been made.
        """
        current_item = self.results_list.currentItem()
        if current_item:
            self.selected_command = current_item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def get_selected_command(self):
        """
        Returns the command that was selected by the user.

        This is called by the parent code that opened the dialog to retrieve the
        user's choice after the dialog has been accepted.

        Returns:
            dict or None: The selected command dictionary, or None if no command
                          was selected.
        """
        return self.selected_command

    def keyPressEvent(self, event):
        """
        Handles keyboard navigation and execution within the dialog.

        This provides a keyboard-centric user experience, allowing users to
        navigate the command list, execute commands, and close the dialog
        without using the mouse.

        Args:
            event (QKeyEvent): The key press event.
        """
        if event.key() == Qt.Key.Key_Escape:
            self.reject() # Close the dialog without a selection.
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._execute_command()
        elif event.key() == Qt.Key.Key_Up:
            current_row = self.results_list.currentRow()
            if current_row > 0:
                self.results_list.setCurrentRow(current_row - 1)
        elif event.key() == Qt.Key.Key_Down:
            current_row = self.results_list.currentRow()
            if current_row < self.results_list.count() - 1:
                self.results_list.setCurrentRow(current_row + 1)
        else:
            # Pass other key events to the parent class (e.g., for text input).
            super().keyPressEvent(event)