"""A new workspace-agent tool is a new tools_*.py file, not a prompt edit."""

from __future__ import annotations

import asyncio
import importlib
import sys

from backend.agent_dispatch.harness import HarnessDispatchOps
from backend.canvas import SceneDocument
from backend.harness import tool_catalog
from backend.harness.context import build_system_prompt
from backend.harness.shell_sessions import ShellSessionRegistry
from backend.harness.tools_python import PythonReplRegistry
from backend.providers.base import ToolCall
from backend.tools import (
    CODE_EXECUTE, FS_READ, FS_WRITE, GRAPH_MUTATE, GRAPH_READ,
    KNOWLEDGE_READ, PROVIDER_CALL, ToolRegistry,
)


class _Dispatcher:
    _settings_manager = None

    def __init__(self):
        self.registry = ToolRegistry()
        self.shell = ShellSessionRegistry()
        self.python = PythonReplRegistry()

    def builder_tool_registry(self, document):
        return self.registry

    def harness_shell_sessions(self):
        return self.shell

    def harness_python_repls(self):
        return self.python


def test_builtin_modules_keep_their_scopes_approvals_and_hints(tmp_path):
    dispatcher, document = _Dispatcher(), SceneDocument()
    try:
        registry = HarnessDispatchOps.harness_tool_registry(dispatcher, document)
        expected = {
            "fs.list": (FS_READ, "auto"),
            "fs.read": (FS_READ, "auto"),
            "fs.grep": (FS_READ, "auto"),
            "fs.write": (FS_WRITE, "once"),
            "fs.edit": (FS_WRITE, "once"),
            "shell.exec": (CODE_EXECUTE, "always"),
            "shell.session": (CODE_EXECUTE, "always"),
            "python.exec": (CODE_EXECUTE, "always"),
            "python.install": (CODE_EXECUTE, "always"),
            "plan.update": (GRAPH_READ, "auto"),
            "user.ask": (PROVIDER_CALL, "auto"),
            "subagent.spawn": (PROVIDER_CALL, "auto"),
            "knowledge.search": (KNOWLEDGE_READ, "auto"),
            "graph.list_nodes": (GRAPH_READ, "auto"),
            "graph.delete_node": (GRAPH_MUTATE, "always"),
        }
        for name, (scope, approval) in expected.items():
            assert registry.scopes_for(name) == frozenset({scope}), name
            assert registry.approval_for(name) == approval, name

        # File-discovered workspace publication needs both file-read and
        # canvas-mutate grants; it follows the other additive graph tools'
        # single-call approval policy.
        assert registry.scopes_for("graph.publish_file") == frozenset({FS_READ, GRAPH_MUTATE})
        assert registry.approval_for("graph.publish_file") == "once"

        parallel_reads = {
            "fs.list", "fs.read", "fs.grep", "knowledge.search",
            "graph.list_nodes", "graph.read_subgraph", "subagent.spawn",
        }
        for name in parallel_reads:
            assert registry.parallel_safe_for(name), name
        for name in (
            "fs.write", "fs.edit", "shell.exec", "python.exec", "python.install",
            "plan.update", "user.ask", "graph.delete_node", "graph.run_node",
            "graph.publish_file", "missing.tool",
        ):
            assert not registry.parallel_safe_for(name), name

        first = tuple(spec.name for spec in registry.specs())
        assert HarnessDispatchOps.harness_tool_registry(dispatcher, document) is registry
        assert tuple(spec.name for spec in registry.specs()) == first
        guide = tool_catalog.tool_guide()
        assert "fs.list" in guide and "shell.session" in guide
        assert "subagent.spawn" in guide and "knowledge.search" in guide
        assert "graph.publish_file" in guide
        prompt = build_system_prompt(tmp_path)
        assert guide in prompt
        assert prompt == build_system_prompt(tmp_path)

        # Builder registry invalidation creates another registry on this
        # dispatcher; the harness tools must not silently disappear.
        dispatcher.registry = ToolRegistry()
        rebuilt = HarnessDispatchOps.harness_tool_registry(dispatcher, document)
        assert rebuilt is not registry
        assert rebuilt.scopes_for("python.exec") == frozenset({CODE_EXECUTE})
        assert rebuilt.approval_for("fs.write") == "once"
    finally:
        dispatcher.shell.stop_all()
        dispatcher.python.stop_all()


def test_new_file_alone_adds_a_tool_and_its_prompt_hint(tmp_path, monkeypatch):
    import backend.harness as package

    module_path = tmp_path / "tools_example.py"
    module_path.write_text(
        '''
from backend.providers.base import ToolSpec
from backend.tools import FS_READ, ToolResult

USAGE_HINT = "example.ping responds with a one-word greeting."

def register_workspace_tools(registry, dispatcher, document):
    async def ping(call, ctx):
        return ToolResult(content="pong")
    registry.register(
        ToolSpec(name="example.ping", description="Ping.", input_schema={"type": "object"}),
        ping, scopes={FS_READ}, approval="auto",
    )
''',
        encoding="utf-8",
    )
    monkeypatch.setattr(package, "__path__", [str(tmp_path), *package.__path__])
    importlib.invalidate_caches()
    dispatcher, document = _Dispatcher(), SceneDocument()
    try:
        assert "example.ping responds" in tool_catalog.tool_guide()
        registry = HarnessDispatchOps.harness_tool_registry(dispatcher, document)
        assert registry.scopes_for("example.ping") == frozenset({FS_READ})
        assert registry.approval_for("example.ping") == "auto"
        assert "example.ping responds" in build_system_prompt(tmp_path)
        from backend.tools import RunContext

        async def allow(_call):
            return True

        reply = asyncio.run(registry.invoke(
            ToolCall(id="1", name="example.ping", arguments={}),
            RunContext(granted_scopes=frozenset({FS_READ}), request_approval=allow),
        ))
        assert reply.content == "pong" and not reply.is_error
    finally:
        dispatcher.shell.stop_all()
        dispatcher.python.stop_all()
        sys.modules.pop("backend.harness.tools_example", None)
        importlib.invalidate_caches()
