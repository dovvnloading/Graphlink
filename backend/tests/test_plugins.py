"""Plugin picker topic tests (Qt-removal plan R2.5, extended R5.1)."""

import asyncio

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugins import _CATEGORY_META, _PLUGINS, get_plugin_categories, plugins_payload, register_plugins


def test_get_plugin_categories_groups_in_category_order_and_skips_empty():
    grouped = get_plugin_categories()
    names = [category["name"] for category in grouped]

    # "Validation & Delivery" has no plugins in _PLUGINS today, so the
    # empty-category-skip rule drops it from the result entirely.
    non_empty_meta_names = [
        meta["name"]
        for meta in _CATEGORY_META
        if any(category_name == meta["name"] for _name, _description, category_name in _PLUGINS)
    ]
    assert names == non_empty_meta_names
    for category in grouped:
        assert category["plugins"]


def test_get_plugin_categories_appends_more_plugins_only_if_uncategorized():
    grouped = get_plugin_categories()
    assert "More Plugins" not in [category["name"] for category in grouped]


def test_get_plugin_categories_covers_every_plugin_exactly_once():
    grouped = get_plugin_categories()
    seen = [plugin["name"] for category in grouped for plugin in category["plugins"]]
    assert sorted(seen) == sorted(name for name, _description, _category in _PLUGINS)
    assert len(seen) == len(set(seen))


def test_plugins_payload_shape_matches_generated_validator_shape():
    payload = plugins_payload()
    assert set(payload) == {"categories"}
    for category in payload["categories"]:
        assert set(category) == {"name", "description", "plugins"}
        for plugin in category["plugins"]:
            assert set(plugin) == {"name", "description"}


def test_plugins_never_imports_qt():
    # A plain `assert "PySide6" not in sys.modules` is only meaningful in a
    # process where nothing else has imported PySide6 - running under the
    # full repo-wide pytest suite (alongside graphlink_app/tests' real Qt
    # widget tests), sys.modules is already contaminated regardless of what
    # this module itself imports. Only a fresh subprocess importing ONLY
    # backend.plugins actually answers "does this transitively pull in Qt".
    import subprocess
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [_sys.executable, "-c", "import backend.plugins, sys; assert 'PySide6' not in sys.modules"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_register_plugins_publishes_on_the_app_plugins_topic():
    bus = SessionBus("plugins-test")
    notifications = NotificationState()
    register_plugins(bus, notifications, SceneDocument())

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-plugins"))
    assert recorder.messages[0]["topic"] == "app-plugins"
    assert recorder.messages[0]["payload"]["categories"]


def test_execute_plugin_shows_info_notification_for_known_plugin():
    bus = SessionBus("plugins-exec-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, SceneDocument())

    asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Gitlink"]))
    assert notifications.visible is True
    assert notifications.msg_type == "info"
    assert "Gitlink" in notifications.message
    assert "R3" in notifications.message or "R5" in notifications.message


def test_execute_plugin_shows_warning_notification_for_unknown_plugin():
    bus = SessionBus("plugins-exec-unknown-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, SceneDocument())

    asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Not A Real Plugin"]))
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert "Not A Real Plugin" in notifications.message


# -- R5.1: "Web Research" - the first real node-creation plugin --------------


def _make_plugins_bus():
    bus = SessionBus("plugins-web-research-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    canvas_document = SceneDocument()
    bus.register_topic("scene", canvas_document.scene_payload)
    register_plugins(bus, notifications, canvas_document)
    return bus, notifications, canvas_document


def test_execute_plugin_web_research_with_no_parent_shows_the_exact_warning():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Web Research"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Web Node."
    )
    assert not any(n.kind == "web_research" for n in canvas_document.nodes.values())


def test_execute_plugin_web_research_with_unknown_parent_shows_the_same_warning():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Web Research", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Web Node."
    )
    assert not any(n.kind == "web_research" for n in canvas_document.nodes.values())


def test_execute_plugin_web_research_with_a_valid_parent_creates_a_real_node_and_publishes_scene():
    bus, notifications, canvas_document = _make_plugins_bus()
    parent = canvas_document.add_node(10, 20, "parent")

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

        def topics_seen(self):
            return [m["topic"] for m in self.messages if m["kind"] == "state"]

    recorder = Recorder()
    bus.attach(recorder)

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Web Research", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "web_research"
    assert node.title == "Web Research"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


# -- R5.1: non-regression - every OTHER plugin name is still an honest,
# unchanged deferred notice ---------------------------------------------------


@pytest.mark.parametrize(
    "name", [n for n in (p[0] for p in _PLUGINS) if n != "Web Research"]
)
def test_execute_plugin_every_other_plugin_name_still_shows_the_unchanged_deferred_notice(name):
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", [name]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "info"
    assert notifications.message == f'"{name}" node creation lands in R3/R5.'
    assert not any(n.kind == "web_research" for n in canvas_document.nodes.values())
