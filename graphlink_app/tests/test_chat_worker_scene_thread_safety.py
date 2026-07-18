"""Tests that chat requests resolve the branch system prompt on the GUI thread.

Regression coverage for a cross-thread scene read: ChatWorker.run used to walk live
QGraphicsScene objects (current_node.parent_node -> .scene() -> system_prompt_connections
-> prompt_note.content) from inside the worker thread. Since QGraphicsScene is not
thread-safe, a concurrent node deletion / scene.clear() on the GUI thread could race that
walk and crash or read torn state.

The fix resolves the branch system prompt via resolve_branch_system_prompt() on the GUI
thread (in ChatWorkerThread.__init__, which runs on the caller's thread) and hands the
resulting string to the worker. ChatWorker.run, given a resolved_system_prompt, must NOT
touch the node/scene at all.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_agents_core import (
    ChatAgent,
    ChatWorker,
    ChatWorkerThread,
    resolve_branch_system_prompt,
)


class _FakeNote:
    def __init__(self, content):
        self.content = content


class _FakeConnection:
    def __init__(self, start_node, end_node):
        self.start_node = start_node
        self.end_node = end_node


class _FakeScene:
    def __init__(self, connections):
        self.system_prompt_connections = connections


class _FakeNode:
    """Stand-in for a chat node's scene-graph surface (no real Qt objects)."""
    def __init__(self, parent_node=None, scene=None):
        self.parent_node = parent_node
        self._scene = scene

    def scene(self):
        return self._scene


class _ExplodingNode:
    """Any scene access on this raises - proves the worker path never touches it."""
    @property
    def parent_node(self):
        raise AssertionError("worker thread walked the scene graph (regression)")

    def scene(self):
        raise AssertionError("worker thread called scene() (regression)")


class TestResolveBranchSystemPrompt:
    def test_uses_attached_note_content_over_the_default(self):
        root = _FakeNode()
        note = _FakeNote("CUSTOM BRANCH PROMPT")
        root._scene = _FakeScene([_FakeConnection(note, root)])

        resolved = resolve_branch_system_prompt(root, "default prompt")

        assert resolved == "CUSTOM BRANCH PROMPT"

    def test_falls_back_to_default_when_no_note_attached(self):
        root = _FakeNode(scene=_FakeScene([]))
        assert resolve_branch_system_prompt(root, "default prompt") == "default prompt"

    def test_walks_up_to_the_branch_root(self):
        root = _FakeNode()
        note = _FakeNote("ROOT PROMPT")
        root._scene = _FakeScene([_FakeConnection(note, root)])
        child = _FakeNode(parent_node=root, scene=root._scene)
        grandchild = _FakeNode(parent_node=child, scene=root._scene)

        assert resolve_branch_system_prompt(grandchild, "default") == "ROOT PROMPT"

    def test_empty_default_short_circuits_without_touching_scene(self):
        # No system prompt configured -> nothing to override, don't walk the scene.
        assert resolve_branch_system_prompt(_ExplodingNode(), "") == ""


class TestWorkerThreadResolvesOnConstruction:
    def test_thread_constructor_resolves_the_prompt_from_the_scene(self):
        root = _FakeNode()
        note = _FakeNote("BRANCH PROMPT")
        root._scene = _FakeScene([_FakeConnection(note, root)])

        agent = ChatAgent("Assistant", "default persona")
        thread = ChatWorkerThread(agent, [], root)

        assert thread.resolved_system_prompt == "BRANCH PROMPT"


class TestWorkerRunDoesNotTouchScene:
    def test_run_with_resolved_prompt_never_walks_the_node(self):
        worker = ChatWorker("default system prompt")
        captured = {}

        def _fake_chat(task, messages, cancellation_event=None):
            captured["messages"] = messages
            return {"message": {"content": "ok"}}

        # If run() touches _ExplodingNode's scene graph, the test fails via AssertionError.
        with patch("graphlink_agents_core.api_provider.chat", side_effect=_fake_chat):
            result = worker.run(
                [{"role": "user", "content": "hi"}],
                _ExplodingNode(),
                resolved_system_prompt="RESOLVED PROMPT",
            )

        assert result == "ok"
        # The resolved prompt was used as the system message, not the default.
        assert captured["messages"][0] == {"role": "system", "content": "RESOLVED PROMPT"}

    def test_empty_resolved_prompt_sends_no_system_message(self):
        worker = ChatWorker("default system prompt")
        captured = {}

        def _fake_chat(task, messages, cancellation_event=None):
            captured["messages"] = messages
            return {"message": {"content": "ok"}}

        with patch("graphlink_agents_core.api_provider.chat", side_effect=_fake_chat):
            worker.run([{"role": "user", "content": "hi"}], _ExplodingNode(), resolved_system_prompt="")

        assert all(m.get("role") != "system" for m in captured["messages"])
