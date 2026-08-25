"""ADR-014 stage 14.3 pinning tests: migrating the 7 built-in plugin picker
actions (backend/plugins.py's old hardcoded if-chain) onto
HostContext.register_builtin_plugin (backend/plugin_sdk.py), backed by 7
real packages under plugins/ (plugins/web_research/, plugins/gitlink/,
plugins/code_sandbox/, plugins/artifact/,
plugins/system_prompt/, plugins/conversation_node/,
plugins/html_renderer/).

Proves, for each migrated built-in:

- the picker categories payload lists it with the EXACT SAME name/
  description/category it had pre-migration (a regression here is a real
  UI regression);
- the exact same parent-validation warning text fires on a missing/invalid
  parent, and no node is created;
- invoking executePlugin with a valid parent still creates the exact same
  node kind, via the exact same command_type in record_command (undo-stack
  identity, not just node shape);
- undo still removes exactly what it created, and nothing more.

System Prompt (the one built-in whose shape genuinely differs - attaches
to the branch ROOT with a REVERSED note -> root edge, and dedups instead
of creating a second note) is handled separately below: its full
dedup/reversed-edge/branch-root-walk behavior is already covered end to
end by backend/tests/test_plugins.py's own R6.1 section (unchanged by this
migration - same command_type, same warning text, same logic, now sourced
from plugins/system_prompt/ instead of a hardcoded if-branch); this file
adds only the picker-entry pin and the command_type/undo pin that section
didn't previously need."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import discover_plugins
from backend.plugins import get_plugin_categories, register_plugins
from graphlink_settings_store import SettingsManager


def _make_bus():
    bus = SessionBus("plugin-builtin-migration-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    canvas_document = SceneDocument()
    bus.register_topic("scene", canvas_document.scene_payload)
    # ADR-014 stage 14.4: register_plugins now requires a real
    # SettingsManager - every case in this file dispatches a BUILT-IN
    # picker action (never grant-gated), so a throwaway store is enough;
    # see backend/tests/test_plugins.py's own _fresh_settings_manager for
    # the same "tempfile dir, explicitly sanctioned by conftest.py's own
    # guard docstring" posture.
    settings_manager = SettingsManager(Path(tempfile.mkdtemp()) / "session.dat")
    register_plugins(bus, notifications, canvas_document, settings_manager)
    return bus, notifications, canvas_document


# (picker_name, description, category, command_type, expected_kind,
#  warning_text, expected_title) - description/category/warning_text are
# byte-identical to the pre-migration hardcoded _PLUGINS list / if-chain.
_BUILTIN_CASES = [
    pytest.param(
        "Web Research",
        "Searches, retrieves, and summarizes cited web sources under a bounded network policy.",
        "Reasoning & Research",
        "pluginWebResearch",
        "web_research",
        "Please select a valid node to branch from before adding a Web Node.",
        "Web Research",
        id="web_research",
    ),
    pytest.param(
        "Gitlink",
        "Loads a GitHub repository into structured XML context, prepares file-level changes, and only writes after explicit approval.",
        "Build & Execution",
        "pluginGitlink",
        "gitlink",
        "Please select a valid node to branch from before adding a Gitlink node.",
        "Gitlink",
        id="gitlink",
    ),
    pytest.param(
        "Virtual Environment Runner",
        "Runs Python inside an isolated virtualenv with your full user-account privileges (isolates installed packages, not the operating system) and lets you declare per-node requirements.txt dependencies.",
        "Build & Execution",
        "pluginCodeSandbox",
        "code_sandbox",
        "Please select a valid node to branch from before adding a Virtual Environment Runner node.",
        "Virtual Environment Runner",
        id="code_sandbox",
    ),
    pytest.param(
        "Artifact / Drafter",
        "A split-pane node for iteratively drafting and refining living documents (Markdown).",
        "Workflow & Drafting",
        "pluginArtifact",
        "artifact",
        "Please select a valid node to branch from before adding an Artifact node.",
        "Artifact",
        id="artifact",
    ),
    pytest.param(
        "Conversation Node",
        "Adds a node for a self-contained, linear chat conversation.",
        "Branch Foundations",
        "pluginConversationNode",
        "conversation",
        "Please select a valid node to branch from before adding a Conversation Node.",
        "Conversation",
        id="conversation_node",
    ),
    pytest.param(
        "HTML Renderer",
        "Adds a node to render HTML code from a parent node.",
        "Build & Execution",
        "pluginHtmlRenderer",
        "html",
        "Please select a valid node to branch from before adding an HTML Renderer node.",
        "HTML",
        id="html_renderer",
    ),
]

_CASE_FIELDS = "picker_name, description, category, command_type, expected_kind, warning_text, expected_title"


@pytest.mark.parametrize(_CASE_FIELDS, _BUILTIN_CASES)
def test_builtin_picker_entry_pinned_in_categories_payload(
    picker_name, description, category, command_type, expected_kind, warning_text, expected_title,
):
    grouped = get_plugin_categories(discover_plugins())
    entry_category = next(
        (c for c in grouped if any(p["name"] == picker_name for p in c["plugins"])), None,
    )
    assert entry_category is not None, f'"{picker_name}" missing from the picker categories payload'
    assert entry_category["name"] == category
    plugin_entry = next(p for p in entry_category["plugins"] if p["name"] == picker_name)
    assert plugin_entry["description"] == description


@pytest.mark.parametrize(_CASE_FIELDS, _BUILTIN_CASES)
def test_builtin_requires_a_selected_parent_with_the_exact_warning(
    picker_name, description, category, command_type, expected_kind, warning_text, expected_title,
):
    bus, notifications, canvas_document = _make_bus()

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", [picker_name]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == warning_text
    assert not any(n.kind == expected_kind for n in canvas_document.nodes.values())


@pytest.mark.parametrize(_CASE_FIELDS, _BUILTIN_CASES)
def test_builtin_rejects_an_unknown_parent_id_with_the_exact_warning(
    picker_name, description, category, command_type, expected_kind, warning_text, expected_title,
):
    bus, notifications, canvas_document = _make_bus()

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", [picker_name, "ghost-node-id"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert notifications.message == warning_text
    assert not any(n.kind == expected_kind for n in canvas_document.nodes.values())


@pytest.mark.parametrize(_CASE_FIELDS, _BUILTIN_CASES)
def test_builtin_creates_the_exact_kind_with_the_exact_command_type_and_undoes_cleanly(
    picker_name, description, category, command_type, expected_kind, warning_text, expected_title,
):
    bus, notifications, canvas_document = _make_bus()
    parent = canvas_document.add_node(10, 20, "parent")

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", [picker_name, parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == expected_kind
    assert node.title == expected_title
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"
    assert canvas_document.command_log[-1].command_type == command_type
    assert canvas_document.can_undo()

    canvas_document.undo()

    assert result not in canvas_document.nodes
    assert not any(
        e.source == parent.id and e.target == result for e in canvas_document.edges.values()
    )
    assert parent.id in canvas_document.nodes


# -- System Prompt: the one built-in whose shape genuinely differs ----------


def test_system_prompt_picker_entry_pinned_in_categories_payload():
    grouped = get_plugin_categories(discover_plugins())
    entry_category = next(
        (c for c in grouped if any(p["name"] == "System Prompt" for p in c["plugins"])), None,
    )
    assert entry_category is not None
    assert entry_category["name"] == "Branch Foundations"
    plugin_entry = next(p for p in entry_category["plugins"] if p["name"] == "System Prompt")
    assert plugin_entry["description"] == (
        "Adds a special node to override the default system prompt for a "
        "conversation branch."
    )


def test_system_prompt_command_type_is_pinned_and_undo_removes_note_and_edge():
    bus, notifications, canvas_document = _make_bus()
    root = canvas_document.add_node(100, 200, "root")

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["System Prompt", root.id])
    )

    assert result is not None
    assert canvas_document.command_log[-1].command_type == "pluginSystemPrompt"
    assert canvas_document.can_undo()

    canvas_document.undo()

    assert result not in canvas_document.nodes
    assert not any(
        e.source == result and e.target == root.id for e in canvas_document.edges.values()
    )
    assert root.id in canvas_document.nodes
