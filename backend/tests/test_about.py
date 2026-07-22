"""About topic tests (Qt-removal plan R2.5)."""

from backend.about import (
    APP_NAME,
    COPYRIGHT_TEXT,
    DEVELOPER_GITHUB_URL,
    DEVELOPER_NAME,
    DEVELOPER_WEBSITE_URL,
    REPOSITORY_URL,
    about_payload,
)
from backend.events import SessionBus
from backend.about import register_about


def test_about_payload_matches_generated_validator_shape():
    payload = about_payload()
    assert set(payload) == {
        "appName",
        "appVersion",
        "repositoryUrl",
        "developerName",
        "developerWebsiteUrl",
        "developerGithubUrl",
        "copyrightText",
    }
    assert payload["appName"] == APP_NAME
    assert payload["repositoryUrl"] == REPOSITORY_URL
    assert payload["developerName"] == DEVELOPER_NAME
    assert payload["developerWebsiteUrl"] == DEVELOPER_WEBSITE_URL
    assert payload["developerGithubUrl"] == DEVELOPER_GITHUB_URL
    assert payload["copyrightText"] == COPYRIGHT_TEXT


def test_app_version_sourced_from_graphlink_version_directly():
    from graphlink_version import APP_VERSION

    assert about_payload()["appVersion"] == APP_VERSION


def test_about_never_imports_qt():
    import sys

    assert "PySide6" not in sys.modules


def test_register_about_publishes_on_the_app_about_topic():
    import asyncio

    bus = SessionBus("about-test")
    register_about(bus)

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-about"))
    assert recorder.messages[0]["topic"] == "app-about"
    assert recorder.messages[0]["payload"]["appName"] == APP_NAME
