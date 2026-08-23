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

    def test_node_deleted_while_the_reply_was_in_flight_is_a_clean_no_op(self, monkeypatch):
        """REVIEW-FIX regression: an ordinary user can delete the chat node
        run_node is replying to while _call_chat_agent (the await below) is
        still in flight - remove_nodes has no special-casing for a chat
        node just because it has a pending run_node request. add_chat_node
        raises SceneError for a missing parent by design; uncaught, that
        used to propagate out of handler() into ToolRegistry.invoke's
        generic except Exception, discarding the reply as a confusing
        internal exception instead of a clean tool error."""
        document, dispatcher, registry = make_setup()
        root = document.add_chat_node(0, 0, "what is 6*7?", True)

        def _reply_then_delete(history, persona, cancel, **kwargs):
            document.remove_nodes([root.id])
            return "It is 42."

        monkeypatch.setattr(agents_module, "_call_chat_agent", _reply_then_delete)

        result = run_node(registry, make_ctx(), root.id)

        assert result.is_error
        assert "no longer exists" in result.content
        assert not any(n.content == "It is 42." for n in document.nodes.values())


class TestRunChart:
    def test_generates_a_chart_node_from_the_source_content(self, monkeypatch):
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "sales: Q1 10, Q2 20, Q3 15", False)
        monkeypatch.setattr(
            agents_module, "_call_chart_agent",
            lambda text, chart_type, cancel_event=None: {"labels": ["Q1", "Q2", "Q3"], "values": [10, 20, 15]},
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
            lambda text, chart_type, cancel_event=None: {"error": "no numeric series found"},
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

    def test_the_stored_chart_data_is_canonicalized_not_the_raw_model_output(self, monkeypatch):
        """review-fix: add_chart_node deliberately does NOT canonicalize
        itself - every caller must. The raw structured-output dict the
        model returns has no "type"/"title" keys; the stored chart_data
        must be canonicalize_chart_data's output shape (ChartState's own
        documented invariant), not the raw dict passed straight through."""
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "sales: Q1 10, Q2 20", False)
        raw = {"labels": ["Q1", "Q2"], "values": [10, 20]}
        monkeypatch.setattr(agents_module, "_call_chart_agent", lambda text, chart_type, cancel_event=None: dict(raw))

        result = run_node(registry, make_ctx(), source.id, action="chart", chart_type="bar")

        assert not result.is_error
        payload = json.loads(result.content)
        chart = document.nodes[payload["chart_node_id"]]
        assert chart.state.chart_data != raw, "the raw uncanonicalized dict must not be stored verbatim"
        assert chart.state.chart_data["type"] == "bar"
        assert chart.state.chart_data["title"]  # canonicalize_chart_data fills a default

    def test_non_finite_chart_values_are_rejected_not_stored(self, monkeypatch):
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "bad data", False)
        monkeypatch.setattr(
            agents_module, "_call_chart_agent",
            lambda text, chart_type, cancel_event=None: {"labels": ["a", "b"], "values": [1, float("nan")]},
        )

        result = run_node(registry, make_ctx(), source.id, action="chart", chart_type="bar")

        assert result.is_error
        assert not any(n.kind == "chart" for n in document.nodes.values())

    def test_node_deleted_while_the_chart_was_in_flight_is_a_clean_no_op(self, monkeypatch):
        """REVIEW-FIX regression: same missing-node race as TestRunChat's
        identical test above - a concurrent delete of `source` while
        _call_chart_agent (the await) is still running used to surface as
        an uncaught SceneError from add_chart_node instead of a clean tool
        error, since a chart with no source node has nothing to attach to."""
        document, dispatcher, registry = make_setup()
        source = document.add_chat_node(0, 0, "sales: Q1 10, Q2 20, Q3 15", False)

        def _chart_then_delete(text, chart_type, cancel_event=None):
            document.remove_nodes([source.id])
            return {"labels": ["Q1", "Q2", "Q3"], "values": [10, 20, 15]}

        monkeypatch.setattr(agents_module, "_call_chart_agent", _chart_then_delete)

        result = run_node(registry, make_ctx(), source.id, action="chart", chart_type="bar")

        assert result.is_error
        assert "no longer exists" in result.content
        assert not any(n.kind == "chart" for n in document.nodes.values())


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

    def test_node_deleted_mid_research_still_returns_the_completed_answer(self, monkeypatch):
        """REVIEW-FIX regression: unlike chat/chart above (which CREATE a
        new child node and so have nothing to land once the parent is
        gone), a completed research answer is already fully formed here -
        complete_web_research_run raises SceneError for a missing node by
        design, but the model should still get the answer it already paid
        for, mirroring _run_pycoder's own posture (complete_pycoder_run's
        silent no-op for a deleted node)."""
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import ResearchResult

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)

        def fake_run(self, request, *, token=None, progress=None):
            document.remove_nodes([node.id])
            return ResearchResult(
                request_id=request.request_id, original_query=request.original_query,
                effective_query=request.original_query, answer_markdown="Solar output doubled.",
            )

        monkeypatch.setattr(wr_service.WebResearchService, "run", fake_run)

        result = run_node(registry, make_ctx(scopes=ALL_SCOPES | frozenset({"net.fetch"})), node.id)

        assert not result.is_error
        assert "doubled" in json.loads(result.content)["answer"]
        assert node.id not in document.nodes

    def test_node_deleted_mid_research_failure_is_still_a_clean_tool_error(self, monkeypatch):
        """REVIEW-FIX regression: the failure-landing counterpart of the
        success-path test above - fail_web_research_run also raises
        SceneError for a missing node by design; a concurrent delete must
        not turn an ordinary research failure into an uncaught exception."""
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import ResearchFailure

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)

        def fake_run(self, request, *, token=None, progress=None):
            document.remove_nodes([node.id])
            raise ResearchFailure("No sources could be fetched.", code="no_sources")

        monkeypatch.setattr(wr_service.WebResearchService, "run", fake_run)

        result = run_node(registry, make_ctx(scopes=ALL_SCOPES | frozenset({"net.fetch"})), node.id)

        assert result.is_error
        assert "No sources" in result.content

    def test_research_requires_the_net_fetch_scope(self):
        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)
        ctx = make_ctx()  # ALL_SCOPES has no net.fetch

        result = run_node(registry, ctx, node.id)

        assert result.is_error
        assert "net.fetch" in result.content

    def test_a_real_request_cancelled_from_the_service_propagates_as_the_loops_cancellation(
        self, monkeypatch,
    ):
        """review-fix: the service's OWN CancellationToken.raise_if_cancelled
        raises graphlink_plugins.web_research.domain.RequestCancelled - a
        DIFFERENT class from api_provider's RequestCancelledError, same
        name, wrong module. Previously uncaught here, it fell into
        ToolRegistry.invoke's generic except and was fed back to the model
        as an ordinary tool error instead of propagating as a real
        cancellation, and the node was never landed."""
        import api_provider
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import RequestCancelled

        document, dispatcher, registry = make_setup()
        node = self._seed_research(document)

        def fake_run(self, request, *, token=None, progress=None):
            raise RequestCancelled("Web research was cancelled.")

        monkeypatch.setattr(wr_service.WebResearchService, "run", fake_run)

        with pytest.raises(api_provider.RequestCancelledError):
            run_node(registry, make_ctx(scopes=ALL_SCOPES | frozenset({"net.fetch"})), node.id)

        assert node.state.research_error
        assert node.pending_request_id is None


class TestRunNodeSchema:
    def test_the_action_enum_advertises_research(self):
        """review-fix: stage 8.5 added the research action to the handler
        and the autopilot net gate, but the tool's own advertised schema
        still said 'web_research nodes are not yet runnable' and omitted
        "research" from the action enum - a provider that validates tool
        arguments against the schema (or a model that trusts the enum as
        the exhaustive option list) could never explicitly request it."""
        from backend.tools_graph import RUN_NODE_SPEC

        assert "research" in RUN_NODE_SPEC.input_schema["properties"]["action"]["enum"]
        assert "not yet runnable" not in RUN_NODE_SPEC.description


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
