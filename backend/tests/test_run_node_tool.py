"""ADR-008 stage 8.2: run_node - invoking a node's own action inline.

The dispatcher is faked at exactly the seams run_node consumes
(get_pycoder_repl / _resolve_branch_system_prompt / active_provider_model /
dispose_pycoder_repl) and the agent drivers are monkeypatched at the same
module attributes the real code binds late (_call_chat_agent /
_call_chart_agent) - everything else is real: the real SceneDocument, real
record_command underneath, real registry gating in front.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend import agents as agents_module
from backend.domain.graph import SceneDocument
from backend.providers.base import ToolCall
from backend.tools import (
    CODE_EXECUTE,
    GRAPH_MUTATE,
    GRAPH_READ,
    PROVIDER_CALL,
    RunContext,
    ToolRegistry,
)
from backend.tools_graph import register_run_node_tool

ALL_SCOPES = frozenset({GRAPH_READ, GRAPH_MUTATE, CODE_EXECUTE, PROVIDER_CALL})


class FakeRepl:
    def __init__(self, output: str = "42\n", fails: bool = False, hang_seconds: float = 0.0):
        self.output = output
        self.last_run_failed = fails
        self.hang_seconds = hang_seconds
        self.executed: list[str] = []

    def execute(self, code: str) -> str:
        import time

        self.executed.append(code)
        if self.hang_seconds:
            time.sleep(self.hang_seconds)
        return self.output


class FakeDispatcher:
    def __init__(self, repl: FakeRepl | None = None):
        self.repl = repl or FakeRepl()
        self.disposed: list[str] = []

    def get_pycoder_repl(self, node_id: str, repl_id: str) -> FakeRepl:
        return self.repl

    async def dispose_pycoder_repl(self, node_id: str, **kwargs) -> None:
        self.disposed.append(node_id)

    def _resolve_branch_system_prompt(self, document, node_id):
        return None

    def active_provider_model(self):
        return ("ollama", "fake-model")


def make_setup(repl: FakeRepl | None = None):
    document = SceneDocument()
    dispatcher = FakeDispatcher(repl)
    registry = ToolRegistry()
    register_run_node_tool(registry, document, dispatcher)
    return document, dispatcher, registry


def make_ctx(*, scopes=ALL_SCOPES, run_id: str | None = None) -> RunContext:
    async def approve(call: ToolCall) -> bool:
        return True

    ctx = RunContext(granted_scopes=frozenset(scopes), request_approval=approve)
    if run_id is not None:
        ctx.run_id = run_id
    return ctx


def run_node(registry, ctx, node_id, **extra):
    call = ToolCall(id="c1", name="run_node", arguments={"node_id": node_id, **extra})
    return asyncio.run(registry.invoke(call, ctx))


def seed_pycoder(document: SceneDocument, code: str = "print(6*7)"):
    parent = document.add_chat_node(0, 0, "root", True)
    node = document.add_pycoder_node(0, 200, parent.id)
    node.state.pycoder_code = code
    return parent, node


class TestRunPycoder:
    def test_executes_the_nodes_code_and_lands_results_on_the_node(self):
        repl = FakeRepl(output="42\n")
        document, dispatcher, registry = make_setup(repl)
        _, node = seed_pycoder(document)

        result = run_node(registry, make_ctx(), node.id)

        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["failed"] is False
        assert payload["output"] == "42\n"
        assert repl.executed == ["print(6*7)"]
        # Results landed through the same domain method the manual surface
        # uses - the node renders identically to a manual run.
        assert node.state.pycoder_output == "42\n"
        assert node.pending_request_id is None, "the pending stamp must clear"

    def test_a_failed_execution_reports_failed_true_but_is_not_a_tool_error(self):
        repl = FakeRepl(output="Traceback ...", fails=True)
        document, dispatcher, registry = make_setup(repl)
        _, node = seed_pycoder(document)

        result = run_node(registry, make_ctx(), node.id)

        assert not result.is_error, "the TOOL worked; the CODE failed - the model reads `failed`"
        assert json.loads(result.content)["failed"] is True

    def test_empty_code_is_a_clear_error(self):
        document, dispatcher, registry = make_setup()
        _, node = seed_pycoder(document, code="   ")

        result = run_node(registry, make_ctx(), node.id)

        assert result.is_error
        assert "no code" in result.content

    def test_a_timeout_disposes_the_repl_and_fails_the_run(self, monkeypatch):
        repl = FakeRepl(hang_seconds=0.5)
        document, dispatcher, registry = make_setup(repl)
        _, node = seed_pycoder(document)
        monkeypatch.setattr(agents_module, "PYCODER_EXECUTE_TIMEOUT_SECONDS", 0.05)

        result = run_node(registry, make_ctx(), node.id)

        assert result.is_error
        assert "timed out" in result.content
        assert dispatcher.disposed == [node.id]
        assert node.pending_request_id is None


class TestRunChat:
    def test_generates_an_assistant_reply_child_with_provenance(self, monkeypatch):
        document, dispatcher, registry = make_setup()
        root = document.add_chat_node(0, 0, "what is 6*7?", True)
        monkeypatch.setattr(
            agents_module, "_call_chat_agent",
            lambda history, persona, cancel, **kwargs: "It is 42.",
        )

        result = run_node(registry, make_ctx(run_id="build-7"), root.id)

        assert not result.is_error
        payload = json.loads(result.content)
        reply = document.nodes[payload["reply_node_id"]]
        assert reply.kind == "chat"
        assert reply.content == "It is 42."
        assert reply.state.is_user is False
        assert reply.state.provider == "ollama"
        assert reply.state.model == "fake-model"
        assert any(
            e.source == root.id and e.target == reply.id for e in document.edges.values()
        )
        assert document.command_log[-1].run_id == "build-7"

    def test_running_chat_on_a_non_chat_node_is_an_error(self):
        document, dispatcher, registry = make_setup()
        note = document.add_note(0, 0)

        result = run_node(registry, make_ctx(), note.id)

        assert result.is_error


class TestRunChart:
    def test_generates_a_chart_node_from_the_source_content(self, monkeypatch):
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "sales: Q1 10, Q2 20, Q3 15", False)
        monkeypatch.setattr(
            agents_module, "_call_chart_agent",
            lambda text, chart_type: {"labels": ["Q1", "Q2", "Q3"], "values": [10, 20, 15]},
        )

        result = run_node(registry, make_ctx(run_id="build-9"), source.id, action="chart", chart_type="bar")

        assert not result.is_error
        payload = json.loads(result.content)
        chart = document.nodes[payload["chart_node_id"]]
        assert chart.kind == "chart"
        assert document.command_log[-1].run_id == "build-9"

    def test_a_chart_agent_error_key_is_surfaced_as_a_tool_error(self, monkeypatch):
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "unchartable", False)
        monkeypatch.setattr(
            agents_module, "_call_chart_agent",
            lambda text, chart_type: {"error": "no numeric series found"},
        )

        result = run_node(registry, make_ctx(), source.id, action="chart", chart_type="bar")

        assert result.is_error
        assert "no numeric series" in result.content
        assert not any(n.kind == "chart" for n in document.nodes.values())

    def test_unsupported_chart_type_is_rejected_before_any_model_call(self, monkeypatch):
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "data", False)
        called = []
        monkeypatch.setattr(
            agents_module, "_call_chart_agent",
            lambda *a: called.append(a) or {},
        )

        result = run_node(registry, make_ctx(), source.id, action="chart", chart_type="hologram")

        assert result.is_error
        assert called == []


class TestRunResearch:
    def _seed_research(self, document):
        parent = document.add_chat_node(0, 0, "context", True)
        node = document.add_web_research_node(0, 200, parent.id)
        node.content = "solar output trends 2025"
        return node

    def test_research_runs_the_service_and_lands_results_on_the_node(self, monkeypatch):
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import ResearchResult, ResearchSource

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)

        def fake_run(self, request, *, token=None, progress=None):
            return ResearchResult(
                request_id=request.request_id, original_query=request.original_query,
                effective_query=request.original_query,
                answer_markdown="Solar output doubled since 2015 [s1].",
                sources=[ResearchSource(
                    source_id="s1", title="Report", url="https://example.com/r",
                    canonical_url="https://example.com/r", final_url="https://example.com/r",
                )],
            )

        monkeypatch.setattr(wr_service.WebResearchService, "run", fake_run)

        result = run_node(registry, make_ctx(scopes=ALL_SCOPES | frozenset({"net.fetch"})), node.id)

        assert not result.is_error
        payload = json.loads(result.content)
        assert "doubled since 2015" in payload["answer"]
        assert payload["sources"] == ["https://example.com/r"]
        assert node.state.research_stage == "completed"
        assert node.state.research_result is not None
        assert node.pending_request_id is None

    def test_a_research_failure_lands_on_the_node_and_returns_a_tool_error(self, monkeypatch):
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import ResearchFailure

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)

        def fake_run(self, request, *, token=None, progress=None):
            raise ResearchFailure("No sources could be fetched.", code="no_sources")

        monkeypatch.setattr(wr_service.WebResearchService, "run", fake_run)

        result = run_node(registry, make_ctx(scopes=ALL_SCOPES | frozenset({"net.fetch"})), node.id)

        assert result.is_error
        assert "No sources" in result.content
        assert node.state.research_error

    def test_an_empty_query_is_rejected_before_any_network(self, monkeypatch):
        from graphlink_plugins.web_research import service as wr_service

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)
        node.content = "   "
        called = []
        monkeypatch.setattr(
            wr_service.WebResearchService, "run",
            lambda self, request, **kw: called.append(1),
        )

        result = run_node(registry, make_ctx(scopes=ALL_SCOPES | frozenset({"net.fetch"})), node.id)

        assert result.is_error
        assert called == []

    def test_the_builders_cancel_event_bridges_onto_the_service_token(self, monkeypatch):
        import threading
        import time

        from backend.providers.base import CancelToken
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import ResearchFailure

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)
        cancel = threading.Event()

        def fake_run(self, request, *, token=None, progress=None):
            cancel.set()  # the user hits Stop mid-run
            deadline = time.monotonic() + 5
            while not token.cancelled:
                if time.monotonic() > deadline:
                    raise AssertionError("the bridge never cancelled the token")
                time.sleep(0.01)
            raise ResearchFailure("Cancelled.", code="cancelled")

        monkeypatch.setattr(wr_service.WebResearchService, "run", fake_run)

        async def approve(call):
            return True

        ctx = RunContext(
            granted_scopes=ALL_SCOPES | frozenset({"net.fetch"}),
            request_approval=approve, cancel=CancelToken(cancel),
        )
        result = asyncio.run(registry.invoke(
            ToolCall(id="c1", name="run_node", arguments={"node_id": node.id}), ctx,
        ))

        assert result.is_error
        assert node.state.research_error

    def test_research_requires_the_net_fetch_scope(self):
        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)
        ctx = make_ctx()  # ALL_SCOPES has no net.fetch

        result = run_node(registry, ctx, node.id)

        assert result.is_error
        assert "net.fetch" in result.content


class TestGuards:
    def test_kind_scope_is_enforced_dynamically(self):
        document, dispatcher, registry = make_setup()
        _, node = seed_pycoder(document)
        ctx = make_ctx(scopes=frozenset({GRAPH_READ, PROVIDER_CALL}))  # no code.execute

        result = run_node(registry, ctx, node.id)

        assert result.is_error
        assert "code.execute" in result.content
        assert dispatcher.repl.executed == []

    def test_a_busy_node_is_refused(self):
        document, dispatcher, registry = make_setup()
        _, node = seed_pycoder(document)
        node.pending_request_id = "someone-else"

        result = run_node(registry, make_ctx(), node.id)

        assert result.is_error
        assert "in flight" in result.content

    def test_cancellation_inside_the_handler_propagates_not_an_error_result(self, monkeypatch):
        import threading

        import api_provider

        document, dispatcher, registry = make_setup()
        root = document.add_chat_node(0, 0, "q", True)
        cancel = threading.Event()

        def _cancelled_chat(history, persona, cancel_event, **kwargs):
            cancel.set()
            raise api_provider.RequestCancelledError("cancelled")

        monkeypatch.setattr(agents_module, "_call_chat_agent", _cancelled_chat)
        from backend.providers.base import CancelToken

        async def approve(call):
            return True

        ctx = RunContext(
            granted_scopes=ALL_SCOPES, request_approval=approve, cancel=CancelToken(cancel),
        )

        with pytest.raises(api_provider.RequestCancelledError):
            asyncio.run(registry.invoke(
                ToolCall(id="c1", name="run_node", arguments={"node_id": root.id}), ctx,
            ))
        assert root.pending_request_id is None, "the pending stamp must clear on cancel too"


class TestStageExitCriterion:
    """8.2 exit: 'Agent runs a code node and reads its result within one
    loop' - the harness plays the loop: the model's scripted turn asks to
    run the pycoder node, the registry executes it for real (fake REPL),
    and the RESULT text is exactly what would be appended as the tool
    message the next model turn reads."""

    def test_agent_runs_a_code_node_and_reads_its_result(self):
        repl = FakeRepl(output="the answer is 42\n")
        document, dispatcher, registry = make_setup(repl)
        _, node = seed_pycoder(document, code="print('the answer is', 6*7)")
        ctx = make_ctx(run_id="build-exit-8-2")

        result = run_node(registry, ctx, node.id)

        assert not result.is_error
        tool_message = {
            "role": "tool", "tool_call_id": "c1", "name": "run_node",
            "content": result.content,
        }
        readable = json.loads(tool_message["content"])
        assert "the answer is 42" in readable["output"]
        assert node.state.pycoder_output == "the answer is 42\n"
