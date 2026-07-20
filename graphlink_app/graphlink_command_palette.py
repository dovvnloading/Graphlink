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
