"""Plugin picker topic tests (Qt-removal plan R2.5, extended R5.1;
migrated onto the ADR-014 Plugin SDK at stage 14.3 - see
backend/tests/test_plugin_builtin_migration.py for the per-built-in
name/description/category/command_type/undo pinning tests this migration
added)."""

import asyncio
import tempfile
from pathlib import Path

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import discover_plugins
from backend.plugins import get_plugin_categories, plugins_payload, register_plugins
from graphlink_settings_store import SettingsManager


def _fresh_settings_manager() -> SettingsManager:
    # ADR-014 stage 14.4: register_plugins now requires a real SettingsManager
    # (the deny-by-default plugin-grant store). A bare stdlib tempfile dir -
    # explicitly sanctioned by conftest.py's own "tmp_path/TemporaryDirectory-
    # derived override" guard docstring - avoids threading a pytest tmp_path
    # fixture through every one of this file's test functions (every real
    # dispatch below is a BUILT-IN plugin, never grant-gated, so nothing here
    # actually needs the store to persist across calls).
    return SettingsManager(Path(tempfile.mkdtemp()) / "session.dat")


# ADR-014 stage 14.3: the 4 structural tests below used to call
# get_plugin_categories()/plugins_payload() with NO plugin_registry
# argument, relying on the (now-removed) hardcoded `_PLUGINS` list to
# supply entries even with no real registry. Since the 8 built-ins are now
# real discovered plugins themselves, there is no picker entry that exists
# independent of a real registry anymore - these tests now pass the real
# discovered registry (discover_plugins(), the production plugins/ root),
# which also picks up plugins/hello_node/ and plugins/counter_node/ (both
# registered under the HostContext default "More Plugins" category) - a
# genuine, expected structural difference from the pre-migration synthetic
# "just the hardcoded 8" listing, not a narrowing of intent.


def test_get_plugin_categories_groups_in_category_order_and_skips_empty():
    grouped = get_plugin_categories(discover_plugins())
    names = [category["name"] for category in grouped]

    # "Validation & Delivery" has no plugins mapped to it in the real
    # shipped plugin set today, so the empty-category-skip rule drops it
    # from the result entirely. "More Plugins" is appended last for
    # plugins/hello_node/'s and plugins/counter_node/'s uncategorized
    # entries.
    assert names == [
        "Branch Foundations", "Reasoning & Research", "Build & Execution",
        "Workflow & Drafting", "More Plugins",
    ]
    for category in grouped:
        assert category["plugins"]


def test_get_plugin_categories_pins_the_original_curated_within_category_order_for_the_8_builtins():
    # ADR-014 review-fix (finding 7): discover_plugins()'s sorted(glob(...))
    # (backend/plugin_sdk.py) walks plugin DIRECTORIES alphabetically - an
    # axis unrelated to the original hand-ordered _PLUGINS tuple literal
    # (deleted by the stage 14.3 migration; recovered verbatim via
    # `git show beb927b:backend/plugins.py`). Without _builtin_picker_
    # sort_key (backend/plugins.py), Build & Execution silently reflowed to
    # alphabetical-by-directory-name (Virtual Environment Runner's own dir
    # is "code_sandbox", so it sorted ahead of Gitlink) instead of this
    # curated sequence - a real, user-visible regression for a migration
    # meant to be byte-faithful. Pinned here against the REAL shipped
    # plugins/ root so a future directory rename/addition can't silently
    # reintroduce the drift without failing a test.
    grouped = get_plugin_categories(discover_plugins())
    by_category = {category["name"]: [p["name"] for p in category["plugins"]] for category in grouped}

    assert by_category["Branch Foundations"] == ["System Prompt", "Conversation Node"]
    assert by_category["Reasoning & Research"] == ["Web Research"]
    assert by_category["Build & Execution"] == [
        "Gitlink", "Py-Coder", "Virtual Environment Runner", "HTML Renderer",
    ]
    assert by_category["Workflow & Drafting"] == ["Artifact / Drafter"]


def test_get_plugin_categories_appends_more_plugins_only_if_uncategorized():
    # Real discovery includes plugins/hello_node/ and plugins/counter_node/,
    # both registered with the HostContext default category ("More
    # Plugins") - proves the catch-all activates for genuinely
    # uncategorized entries.
    grouped = get_plugin_categories(discover_plugins())
    more_plugins = next(c for c in grouped if c["name"] == "More Plugins")
    plugin_names = {p["name"] for p in more_plugins["plugins"]}
    assert {"Hello Node", "Counter Node"} <= plugin_names

    # When nothing is uncategorized (no registry at all -> zero entries),
    # the catch-all must not appear.
    grouped_empty = get_plugin_categories(None)
    assert "More Plugins" not in [category["name"] for category in grouped_empty]


def test_get_plugin_categories_covers_every_plugin_exactly_once():
    registry = discover_plugins()
    grouped = get_plugin_categories(registry)
    seen = [plugin["name"] for category in grouped for plugin in category["plugins"]]
    expected = {entry.name for entry in registry.picker_entries.values()} | {
        spec.name for spec in registry.builtin_actions.values()
    }
    assert set(seen) == expected
    assert len(seen) == len(set(seen))


def test_plugins_payload_shape_matches_generated_validator_shape():
    payload = plugins_payload(discover_plugins(), _fresh_settings_manager())
    assert set(payload) == {"categories", "grants"}
    for category in payload["categories"]:
        assert set(category) == {"name", "description", "plugins"}
        for plugin in category["plugins"]:
            assert set(plugin) == {"name", "description"}
    for grant in payload["grants"]:
        assert set(grant) == {"pluginId", "name", "scopes", "granted"}


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
    register_plugins(bus, notifications, SceneDocument(), _fresh_settings_manager())

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


# ADR-014 stage 14.3: test_execute_plugin_falls_through_to_the_generic_
# deferred_notice_for_an_unhandled_registered_name (formerly here) is
# REMOVED, not just updated - it exercised a code path
# (execute_plugin's `_PLUGINS`-membership-but-no-branch-yet "isn't
# available yet" info notice) that no longer exists in any form.
# `_PLUGINS` itself is gone: every name now either resolves to a real
# `plugin_registry.builtin_actions`/`picker_entries` entry or hits the
# generic "Unknown plugin" warning in _execute_discovered_plugin -
# covered immediately below by
# test_execute_plugin_shows_warning_notification_for_unknown_plugin. There
# is no longer a third state ("a known name with no branch yet") to test.


def test_execute_plugin_shows_warning_notification_for_unknown_plugin():
    bus = SessionBus("plugins-exec-unknown-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, SceneDocument(), _fresh_settings_manager())

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
    register_plugins(bus, notifications, canvas_document, _fresh_settings_manager())
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
    assert node.state.code_sandbox_sandbox_id, "a sandbox id must be minted at creation time"
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


def test_every_builtin_now_has_a_real_builtin_action_registration():
    # ADR-014 stage 14.3: R7.5a's old "every _PLUGINS entry has moved off
    # the generic deferred notice" claim is now expressed differently -
    # there is no more `_PLUGINS` list to compare against. Confirms
    # instead that all 8 migrated built-ins are real
    # `plugin_registry.builtin_actions` entries (the ADR-014 stage 14.3
    # escape hatch), not `picker_entries` (the generic PluginNodeSeed
    # path reserved for third-party plugins and the demo plugins).
    handled = {
        "Web Research", "Artifact / Drafter", "Gitlink", "Py-Coder", "Virtual Environment Runner",
        "System Prompt", "Conversation Node", "HTML Renderer",
    }
    registry = discover_plugins()
    assert handled == set(registry.builtin_actions)
    assert handled.isdisjoint(registry.picker_entries)
