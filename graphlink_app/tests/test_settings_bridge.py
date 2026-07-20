"""Contract tests for the settings island bridge (Phase 3 increment 2 -
shell-only: activeSection navigation, nothing page-specific yet)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_config as config
from graphlink_settings_bridge import SECTION_NAMES, SettingsBridge


def _last_payload(bridge: SettingsBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_section_names_match_settings_dialogs_own_vocabulary():
    # SettingsDialog.SECTION_DEFS / set_current_section_by_mode already use
    # this exact vocabulary - a second, drifted set of names here would be a
    # real bug the moment increment 8 wires this bridge into the real shell.
    assert SECTION_NAMES == (
        "General",
        config.MODE_OLLAMA_LOCAL,
        config.MODE_LLAMACPP_LOCAL,
        config.MODE_API_ENDPOINT,
        "Integrations",
    )


def test_ready_publishes_general_as_the_initial_section():
    bridge = SettingsBridge()

    payload = _last_payload(bridge)

    assert payload["activeSection"] == "General"
    assert payload["schemaVersion"] == SettingsBridge.SCHEMA_VERSION
    assert payload["revision"] == 1


def test_set_active_section_navigates_and_publishes():
    bridge = SettingsBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setActiveSection(config.MODE_OLLAMA_LOCAL)

    payload = json.loads(states[-1])
    assert payload["activeSection"] == config.MODE_OLLAMA_LOCAL
    assert payload["revision"] == 1


def test_set_active_section_to_the_same_section_is_a_no_op():
    bridge = SettingsBridge()
    bridge.setActiveSection("Integrations")
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setActiveSection("Integrations")

    assert states == []


def test_set_active_section_ignores_an_unrecognized_name():
    bridge = SettingsBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.setActiveSection("Not A Real Section")

    assert states == []


def test_python_side_set_active_section_is_equivalent_to_the_slot():
    bridge = SettingsBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.set_active_section(config.MODE_API_ENDPOINT)

    payload = json.loads(states[-1])
    assert payload["activeSection"] == config.MODE_API_ENDPOINT


def test_publish_is_a_no_op_after_dispose():
    bridge = SettingsBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.dispose()
    bridge.setActiveSection(config.MODE_OLLAMA_LOCAL)

    assert states == []
    assert bridge.disposed is True


def test_revision_increments_monotonically_across_calls():
    bridge = SettingsBridge()
    revisions = []
    bridge.stateChanged.connect(lambda payload: revisions.append(json.loads(payload)["revision"]))

    bridge.ready()
    bridge.setActiveSection(config.MODE_OLLAMA_LOCAL)
    bridge.setActiveSection("Integrations")

    assert revisions == [1, 2, 3]
