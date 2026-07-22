"""Plugin picker topic tests (Qt-removal plan R2.5)."""

import asyncio

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
    import sys

    assert "PySide6" not in sys.modules


def test_register_plugins_publishes_on_the_app_plugins_topic():
    bus = SessionBus("plugins-test")
    notifications = NotificationState()
    register_plugins(bus, notifications)

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
    register_plugins(bus, notifications)

    asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Gitlink"]))
    assert notifications.visible is True
    assert notifications.msg_type == "info"
    assert "Gitlink" in notifications.message
    assert "R3" in notifications.message or "R5" in notifications.message


def test_execute_plugin_shows_warning_notification_for_unknown_plugin():
    bus = SessionBus("plugins-exec-unknown-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications)

    asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Not A Real Plugin"]))
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert "Not A Real Plugin" in notifications.message
