"""Workspace tool-catalog parity: every dynamically offered tool uses one neutral contract.

The provider-specific wire layers translate ToolSpec and ToolCall only; they
must never decide tool authorization. This test derives its inventory from
the live registry so newly merged tool modules are covered automatically.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.agent_dispatch.harness import HarnessDispatchOps
from backend.canvas import SceneDocument
from backend.harness.loop import _offered_harness_specs
from backend.harness.shell_sessions import ShellSessionRegistry
from backend.harness.tools_python import PythonReplRegistry
from backend.providers.json_tool_protocol import parse_tool_turn, tool_turn_schema
from backend.providers.tool_names import cloud_tool_names
from backend.tools import RunContext, ToolRegistry


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


@pytest.mark.parametrize("execution_profile", ("host", "isolated"))
def test_every_offered_workspace_tool_has_portable_names_json_schema_and_scope_gate(execution_profile):
    dispatcher = _Dispatcher()
    try:
        registry = HarnessDispatchOps.harness_tool_registry(dispatcher, SceneDocument())
        offered = _offered_harness_specs(registry, execution_profile)
        names = [spec.name for spec in offered]
        assert len(names) >= 20, "The suite must exercise the merged workspace tool inventory"
        assert len(set(names)) == len(names)

        # Constrained JSON tool calling (llama.cpp, non-tools Ollama and
        # custom OpenAI-compatible servers) must offer exactly these names.
        schema = tool_turn_schema(offered)
        assert schema["additionalProperties"] is False
        assert schema["properties"]["tool_calls"]["items"]["properties"]["name"]["enum"] == names

        # Native cloud providers receive function-name-safe aliases, then
        # restore canonical names before the registry sees a ToolCall.
        wire_tools, wire_messages, from_wire = cloud_tool_names(offered, [])
        assert wire_messages == []
        assert len(wire_tools) == len(offered)
        assert len({spec.name for spec in wire_tools}) == len(offered)
        assert len(from_wire) == len(offered)
        assert [from_wire[spec.name] for spec in wire_tools] == names
        assert [spec.input_schema for spec in wire_tools] == [
            spec.input_schema for spec in offered
        ], "Wire translation must not weaken or rewrite a tool's argument schema"

        # No provider can turn a decoded call into an approval bypass:
        # every offered tool must first pass the shared scope gate.
        approvals = []

        async def approve(call):
            approvals.append(call.name)
            return True

        context = RunContext(
            granted_scopes=frozenset(),
            offered_tool_names=frozenset(names),
            request_approval=approve,
        )
        for spec in offered:
            raw = json.dumps({
                "tool_calls": [{"name": spec.name, "arguments_json": "{}"}],
                "final_answer": "",
            })
            _, calls = parse_tool_turn(raw, offered)
            assert len(calls) == 1
            call = calls[0]
            assert (call.name, call.arguments) == (spec.name, {})
            result = asyncio.run(registry.invoke(call, context))
            assert result.is_error, spec.name
            assert "requires scope" in result.content, spec.name
        assert approvals == [], "No scope-denied call should reach an approval callback"
    finally:
        dispatcher.shell.stop_all()
        dispatcher.python.stop_all()
