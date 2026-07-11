"""Tests for PluginPortal.execute_plugin's not-found path (see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 3.6/§4.8).

execute_plugin used to handle an unrecognized plugin_name with print() + return None -
a real, silent failure, since instantiate_seeded_plugin() in graphlink_window_actions.py
just restores state and returns on None with zero user-visible feedback (e.g. an
LLM-recommended plugin name that has drifted out of sync with the registry). Fixed to
show a notification banner instead, matching every other plugin-creation failure path
in this file (see create_node()'s no_selection_message/invalid_parent_message).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_plugins.graphlink_plugin_portal import PluginPortal


def _make_portal():
    main_window = MagicMock()
    return PluginPortal(main_window), main_window


def test_unrecognized_plugin_name_returns_none():
    portal, _main_window = _make_portal()
    assert portal.execute_plugin("Definitely Not A Real Plugin") is None


def test_unrecognized_plugin_name_shows_a_notification_banner():
    portal, main_window = _make_portal()
    portal.execute_plugin("Definitely Not A Real Plugin")
    main_window.notification_banner.show_message.assert_called_once()
    message, _duration, level = main_window.notification_banner.show_message.call_args[0]
    assert "Definitely Not A Real Plugin" in message
    assert level == "warning"


def test_recognized_plugin_name_does_not_show_a_failure_banner():
    portal, main_window = _make_portal()
    portal.execute_plugin = lambda name: PluginPortal.execute_plugin(portal, name)
    # System Prompt's own callback shows its own (different) notification when there is
    # no root node, so use a name whose callback we substitute with a no-op to isolate
    # execute_plugin's not-found branch specifically.
    for plugin in portal.plugins:
        if plugin["name"] == "System Prompt":
            plugin["callback"] = lambda: "created"
            break

    result = portal.execute_plugin("System Prompt")
    assert result == "created"
    main_window.notification_banner.show_message.assert_not_called()
