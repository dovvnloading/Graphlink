"""Plugin picker topic tests (Qt-removal plan R2.5, extended R5.1)."""

import asyncio

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


def test_execute_plugin_falls_through_to_the_generic_deferred_notice_for_an_unhandled_registered_name(monkeypatch):
    # R7.5a closed the last 2 gaps (Conversation Node, HTML Renderer) - every
    # _PLUGINS entry today has its own real branch, so the generic fallback
    # at the bottom of execute_plugin has no live entry left to exercise
    # through the real registry. It stays in place as the established growth
    # path for the NEXT plugin added to _PLUGINS before its own branch lands
    # (same state every plugin above was once in) - proven still-correct
    # here by monkeypatching _PLUGINS into exactly that state for one call.
    import backend.plugins as plugins_module

    monkeypatch.setattr(
        plugins_module,
        "_PLUGINS",
        plugins_module._PLUGINS + [("Future Plugin", "Not yet wired.", "Build & Execution")],
    )
    bus = SessionBus("plugins-exec-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, SceneDocument())

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Future Plugin"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "info"
    assert notifications.message == '"Future Plugin" node creation isn\'t available yet.'


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


# -- R5.2: "Artifact / Drafter" - the second real node-creation plugin ------


def test_execute_plugin_artifact_drafter_requires_a_selected_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Artifact / Drafter"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding an Artifact node."
    )
    assert not any(n.kind == "artifact" for n in canvas_document.nodes.values())


def test_execute_plugin_artifact_drafter_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Artifact / Drafter", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding an Artifact node."
    )
    assert not any(n.kind == "artifact" for n in canvas_document.nodes.values())


def test_execute_plugin_artifact_drafter_creates_a_real_artifact_node():
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
        bus.dispatch_intent("app-plugins", "executePlugin", ["Artifact / Drafter", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "artifact"
    assert node.title == "Artifact"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


# -- R5.3: "Gitlink" - the third real node-creation plugin -------------------


def test_execute_plugin_gitlink_requires_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Gitlink"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Gitlink node."
    )
    assert not any(n.kind == "gitlink" for n in canvas_document.nodes.values())


def test_execute_plugin_gitlink_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Gitlink", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Gitlink node."
    )
    assert not any(n.kind == "gitlink" for n in canvas_document.nodes.values())


def test_execute_plugin_gitlink_creates_node():
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

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Gitlink", parent.id]))

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "gitlink"
    assert node.title == "Gitlink"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


# -- R5.4: "Py-Coder" - the fourth real node-creation plugin ------------------


def test_execute_plugin_pycoder_requires_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Py-Coder"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Py-Coder node."
    )
    assert not any(n.kind == "pycoder" for n in canvas_document.nodes.values())


def test_execute_plugin_pycoder_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Py-Coder", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert not any(n.kind == "pycoder" for n in canvas_document.nodes.values())


def test_execute_plugin_pycoder_creates_a_real_pycoder_node():
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

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Py-Coder", parent.id]))

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "pycoder"
    assert node.title == "Py-Coder"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


# -- R5.4: "Execution Sandbox" - the fifth real node-creation plugin ----------


def test_execute_plugin_execution_sandbox_requires_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Virtual Environment Runner"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Virtual Environment Runner node."
    )
    assert not any(n.kind == "code_sandbox" for n in canvas_document.nodes.values())


def test_execute_plugin_execution_sandbox_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Virtual Environment Runner", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert not any(n.kind == "code_sandbox" for n in canvas_document.nodes.values())


def test_execute_plugin_execution_sandbox_creates_a_real_code_sandbox_node():
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
        bus.dispatch_intent("app-plugins", "executePlugin", ["Virtual Environment Runner", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "code_sandbox"
    assert node.title == "Virtual Environment Runner"
    assert node.code_sandbox_sandbox_id, "a sandbox id must be minted at creation time"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


# -- R6.1: "System Prompt" - the sixth real node-creation plugin -------------
#
# Unlike Web Research/Artifact/Gitlink/Py-Coder/Execution Sandbox above (each
# a branch-point CHILD of parent_node_id), a System Prompt note attaches to
# parent_node_id's BRANCH ROOT (SceneDocument.get_branch_root) and connects
# note -> root (reversed from the child plugins' root -> child edges) - see
# backend/plugins.py's own "System Prompt" branch and backend/agents.py's
# _resolve_branch_system_prompt, which looks for that exact edge shape at
# send time.


def test_execute_plugin_system_prompt_requires_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a System Prompt node."
    )
    assert not any(n.kind == "note" for n in canvas_document.nodes.values())


def test_execute_plugin_system_prompt_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a System Prompt node."
    )
    assert not any(n.kind == "note" for n in canvas_document.nodes.values())


def test_execute_plugin_system_prompt_creates_a_note_attached_to_the_branch_root_not_the_selected_child():
    bus, notifications, canvas_document = _make_plugins_bus()
    # A 2-hop branch: root -> mid. Selecting `mid` must still resolve the
    # note to `root` (get_branch_root's own walk), proving this isn't just
    # attaching to whatever node_id was passed in directly.
    root = canvas_document.add_node(100, 200, "root")
    mid = canvas_document.add_node(100, 320, "mid")
    canvas_document.connect(root.id, mid.id)

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

        def topics_seen(self):
            return [m["topic"] for m in self.messages if m["kind"] == "state"]

    recorder = Recorder()
    bus.attach(recorder)

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", mid.id]))

    assert result is not None
    note = canvas_document.nodes[result]
    assert note.kind == "note"
    assert note.state.is_system_prompt is True
    # Positioned above the ROOT (not `mid`), roughly matching legacy's
    # "200px above" placement.
    assert note.x == root.x
    assert note.y == root.y - 150
    # note -> root, the exact direction _resolve_branch_system_prompt looks
    # for - NOT root -> note (the child plugins' own direction) and NOT
    # note -> mid (the selected node, not the resolved root).
    assert any(e.source == note.id and e.target == root.id for e in canvas_document.edges.values())
    assert not any(e.source == note.id and e.target == mid.id for e in canvas_document.edges.values())
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


def test_execute_plugin_system_prompt_on_a_rootless_node_attaches_to_itself():
    # A node with no parent edge at all IS its own branch root
    # (get_branch_root's own documented behavior) - selecting it directly
    # must still produce a valid note -> node edge, not a crash/no-op.
    bus, notifications, canvas_document = _make_plugins_bus()
    lone = canvas_document.add_node(0, 0, "lone")

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", lone.id]))

    assert result is not None
    note = canvas_document.nodes[result]
    assert note.kind == "note"
    assert note.state.is_system_prompt is True
    assert any(e.source == note.id and e.target == lone.id for e in canvas_document.edges.values())


def test_execute_plugin_system_prompt_reuses_an_existing_note_instead_of_creating_a_duplicate():
    # Post-review fix: a root can only ever have ONE effective system-prompt
    # note (_resolve_branch_system_prompt has no "which one wins" rule for
    # two at once) - a second invocation on the same branch must return the
    # SAME note, not silently create an inert duplicate.
    bus, notifications, canvas_document = _make_plugins_bus()
    root = canvas_document.add_node(100, 200, "root")

    first = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", root.id]))
    second = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", root.id]))

    assert first == second
    assert sum(1 for n in canvas_document.nodes.values() if n.kind == "note") == 1


# -- R7.5a: "Conversation Node" - the seventh real node-creation plugin ------
#
# Existed as a real, working node kind since R3.25 (add_conversation_node)
# with zero UI-reachable creation path - execute_plugin had no branch for
# it at all, silently falling through to the generic deferred notice despite
# the node itself being fully functional.


def test_execute_plugin_conversation_node_requires_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Conversation Node"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding a Conversation Node."
    )
    assert not any(n.kind == "conversation" for n in canvas_document.nodes.values())


def test_execute_plugin_conversation_node_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Conversation Node", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert not any(n.kind == "conversation" for n in canvas_document.nodes.values())


def test_execute_plugin_conversation_node_creates_a_real_conversation_node():
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
        bus.dispatch_intent("app-plugins", "executePlugin", ["Conversation Node", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "conversation"
    assert node.title == "Conversation"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


# -- R7.5a: "HTML Renderer" - the eighth real node-creation plugin -----------
#
# Same gap class as Conversation Node above: add_html_node has existed since
# R3.17 with zero creation path. Starts with empty html_content since the
# plugin picker has no field to source initial HTML from - same "create
# blank, then edit in place" posture as "Add Note".


def test_execute_plugin_html_renderer_requires_parent():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["HTML Renderer"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == (
        "Please select a valid node to branch from before adding an HTML Renderer node."
    )
    assert not any(n.kind == "html" for n in canvas_document.nodes.values())


def test_execute_plugin_html_renderer_rejects_unknown_parent_id():
    bus, notifications, canvas_document = _make_plugins_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["HTML Renderer", "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert not any(n.kind == "html" for n in canvas_document.nodes.values())


def test_execute_plugin_html_renderer_creates_a_real_html_node_with_empty_content():
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
        bus.dispatch_intent("app-plugins", "executePlugin", ["HTML Renderer", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "html"
    assert node.content == ""
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert "scene" in recorder.topics_seen()


def test_every_plugin_now_has_a_real_creation_branch():
    # R7.5a closes the last 2 gaps - confirms explicitly (rather than
    # letting the old parametrized non-regression test below silently
    # collect zero cases) that every _PLUGINS entry has moved off the
    # generic deferred notice.
    handled = {
        "Web Research", "Artifact / Drafter", "Gitlink", "Py-Coder", "Virtual Environment Runner",
        "System Prompt", "Conversation Node", "HTML Renderer",
    }
    assert handled == {name for name, _description, _category in _PLUGINS}
