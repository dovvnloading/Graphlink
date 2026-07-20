"""Contract tests for the about-dialog island bridge (Phase 4 increment 1)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_about_bridge import (
    APP_NAME,
    COPYRIGHT_TEXT,
    DEVELOPER_GITHUB_URL,
    DEVELOPER_NAME,
    DEVELOPER_WEBSITE_URL,
    REPOSITORY_URL,
    AboutBridge,
)
from graphlink_update import APP_VERSION


def _last_payload(bridge: AboutBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_ready_publishes_every_static_field():
    bridge = AboutBridge()

    payload = _last_payload(bridge)

    assert payload["appName"] == APP_NAME
    assert payload["appVersion"] == APP_VERSION
    assert payload["repositoryUrl"] == REPOSITORY_URL
    assert payload["developerName"] == DEVELOPER_NAME
    assert payload["developerWebsiteUrl"] == DEVELOPER_WEBSITE_URL
    assert payload["developerGithubUrl"] == DEVELOPER_GITHUB_URL
    assert payload["copyrightText"] == COPYRIGHT_TEXT
    assert payload["schemaVersion"] == AboutBridge.SCHEMA_VERSION
    assert payload["revision"] == 1


def test_open_external_calls_webbrowser_open(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "graphlink_about_bridge.webbrowser.open",
        lambda url: opened.append(url),
    )
    bridge = AboutBridge()

    bridge.openExternal("https://example.com")

    assert opened == ["https://example.com"]


def test_open_external_does_not_publish_or_change_state(monkeypatch):
    monkeypatch.setattr("graphlink_about_bridge.webbrowser.open", lambda url: None)
    bridge = AboutBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.openExternal("https://example.com")

    # Matches the legacy dialog exactly: opening a link has no visible
    # effect on the dialog itself, so no republish is needed or expected.
    assert states == []


class _FakeParent(QObject):
    """Real QObject stand-in for the host WebIslandHost's setParent(self)
    call - bridge.close() reads self.parent(), a Qt-level ownership
    relationship that only ever holds a real QObject, never an arbitrary
    Python object."""

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


def test_close_calls_close_on_the_parent_when_one_exists():
    bridge = AboutBridge()
    parent = _FakeParent()
    bridge.setParent(parent)

    bridge.close()

    assert parent.closed is True


def test_close_is_a_no_op_without_a_parent():
    bridge = AboutBridge()

    # Must not raise even though nothing is attached to close.
    bridge.close()


def test_publish_is_a_no_op_after_dispose():
    bridge = AboutBridge()
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.dispose()
    bridge.ready()

    assert states == []
    assert bridge.disposed is True


def test_revision_increments_monotonically_across_calls():
    bridge = AboutBridge()
    revisions = []
    bridge.stateChanged.connect(lambda payload: revisions.append(json.loads(payload)["revision"]))

    bridge.ready()
    bridge.ready()

    assert revisions == [1, 2]
