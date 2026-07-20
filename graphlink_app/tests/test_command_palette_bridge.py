"""Contract tests for the command-palette island bridge."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from graphlink_command_palette_bridge import CommandPaletteBridge


class _CommandManager:
    """A minimal stand-in for graphlink_command_palette.CommandManager - same
    dict shape (name/aliases/callback/condition), so the bridge is exercised
    against the exact contract it actually depends on without importing the
    Qt-heavy real module."""

    def __init__(self):
        self.commands = []

    def register_command(self, name, aliases, callback, condition=None):
        self.commands.append(
            {
                "name": name,
                "aliases": [name.lower()] + [alias.lower() for alias in aliases],
                "callback": callback,
                "condition": condition or (lambda: True),
            }
        )
        self.commands.sort(key=lambda cmd: cmd["name"])


def _states(bridge):
    payloads = []
    bridge.stateChanged.connect(lambda p: payloads.append(json.loads(p)))
    return payloads


class TestOpen:
    def test_publishes_visible_true_and_the_available_commands(self):
        manager = _CommandManager()
        manager.register_command("New Chat", ["start new"], lambda: None)
        bridge = CommandPaletteBridge(manager)
        payloads = _states(bridge)

        bridge.open()

        assert payloads[-1]["visible"] is True
        assert payloads[-1]["commands"] == [
            {"id": "0", "name": "New Chat", "aliases": ["new chat", "start new"]}
        ]

    def test_excludes_commands_whose_condition_is_currently_false(self):
        manager = _CommandManager()
        manager.register_command("Delete Selected", [], lambda: None, condition=lambda: False)
        bridge = CommandPaletteBridge(manager)
        payloads = _states(bridge)

        bridge.open()

        assert payloads[-1]["commands"] == []

    def test_ids_are_the_index_in_the_full_registered_list_not_the_available_subset(self):
        manager = _CommandManager()
        manager.register_command("Available A", [], lambda: None)
        manager.register_command("Unavailable", [], lambda: None, condition=lambda: False)
        manager.register_command("Available B", [], lambda: None)
        bridge = CommandPaletteBridge(manager)
        payloads = _states(bridge)

        bridge.open()

        ids = {entry["name"]: entry["id"] for entry in payloads[-1]["commands"]}
        # Sorted order: Available A(0), Available B(1), Unavailable(2) - the
        # unavailable one still occupies index 2, just never appears in the
        # emitted list.
        assert ids == {"Available A": "0", "Available B": "1"}

    def test_clears_a_stale_notice_from_a_previous_session(self):
        manager = _CommandManager()
        manager.register_command("Cmd", [], lambda: None, condition=lambda: False)
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        bridge.executeCommand("0")  # stale -> sets a notice
        payloads = _states(bridge)

        bridge.open()

        assert payloads[-1]["notice"] is None


class TestExecuteCommand:
    def test_valid_command_invokes_the_callback(self):
        manager = _CommandManager()
        calls = []
        manager.register_command("New Chat", [], lambda: calls.append("ran"))
        bridge = CommandPaletteBridge(manager)
        bridge.open()

        bridge.executeCommand("0")
        QApplication.processEvents()  # let QTimer.singleShot(0, ...) fire

        assert calls == ["ran"]

    def test_valid_command_hides_the_palette_before_the_callback_runs(self):
        manager = _CommandManager()
        order = []
        manager.register_command("New Chat", [], lambda: order.append("callback"))
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        bridge.stateChanged.connect(
            lambda payload: order.append("visible" if json.loads(payload)["visible"] else "hidden")
        )

        bridge.executeCommand("0")
        QApplication.processEvents()

        assert order == ["hidden", "callback"]

    def test_stale_command_does_not_invoke_the_callback(self):
        manager = _CommandManager()
        available = {"flag": True}
        calls = []
        manager.register_command(
            "Delete Selected", [], lambda: calls.append("ran"), condition=lambda: available["flag"]
        )
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        available["flag"] = False  # app state changed while the palette sat open

        bridge.executeCommand("0")
        QApplication.processEvents()

        assert calls == []

    def test_stale_command_sets_a_notice_and_keeps_the_palette_open(self):
        manager = _CommandManager()
        available = {"flag": True}
        manager.register_command("Delete Selected", [], lambda: None, condition=lambda: available["flag"])
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        available["flag"] = False
        payloads = _states(bridge)

        bridge.executeCommand("0")

        assert payloads[-1]["notice"] == "That command is no longer available."
        assert payloads[-1]["visible"] is True

    def test_stale_command_drops_out_of_the_republished_commands_list(self):
        manager = _CommandManager()
        available = {"flag": True}
        manager.register_command("Delete Selected", [], lambda: None, condition=lambda: available["flag"])
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        available["flag"] = False
        payloads = _states(bridge)

        bridge.executeCommand("0")

        assert payloads[-1]["commands"] == []

    def test_unknown_id_does_not_raise_and_reports_a_notice(self):
        manager = _CommandManager()
        manager.register_command("New Chat", [], lambda: None)
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        payloads = _states(bridge)

        bridge.executeCommand("999")

        assert payloads[-1]["notice"] == "That command is no longer available."

    def test_non_numeric_id_does_not_raise(self):
        manager = _CommandManager()
        manager.register_command("New Chat", [], lambda: None)
        bridge = CommandPaletteBridge(manager)
        bridge.open()

        bridge.executeCommand("not-a-number")  # must not raise


class TestDismiss:
    def test_hides_a_visible_palette(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        payloads = _states(bridge)

        bridge.dismiss()

        assert payloads[-1]["visible"] is False

    def test_is_idempotent_if_already_hidden(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        payloads = _states(bridge)

        bridge.dismiss()

        assert payloads == []

    def test_clears_any_pending_notice(self):
        manager = _CommandManager()
        manager.register_command("Cmd", [], lambda: None, condition=lambda: False)
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        bridge.executeCommand("0")  # sets a notice, stays open
        payloads = _states(bridge)

        bridge.dismiss()

        assert payloads[-1]["notice"] is None


class TestVisibilityChanged:
    def test_emits_true_on_open(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.open()

        assert seen == [True]

    def test_does_not_re_emit_if_already_open(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.open()

        assert seen == []

    def test_emits_false_on_dismiss(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.dismiss()

        assert seen == [False]

    def test_emits_false_on_a_successful_execute(self):
        manager = _CommandManager()
        manager.register_command("New Chat", [], lambda: None)
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.executeCommand("0")
        QApplication.processEvents()

        assert seen == [False]

    def test_does_not_emit_on_a_stale_execute(self):
        manager = _CommandManager()
        manager.register_command("Cmd", [], lambda: None, condition=lambda: False)
        bridge = CommandPaletteBridge(manager)
        bridge.open()
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.executeCommand("0")

        assert seen == []


class TestDisposeIsIdempotent:
    def test_publish_is_a_no_op_after_dispose(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        payloads = _states(bridge)

        bridge.dispose()
        bridge.open()

        assert payloads == []
        assert bridge.disposed is True

    def test_dispose_twice_does_not_raise(self):
        manager = _CommandManager()
        bridge = CommandPaletteBridge(manager)
        bridge.dispose()
        bridge.dispose()  # must not raise
