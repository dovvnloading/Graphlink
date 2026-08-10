"""ADR-008 stage 8.3: the Builder loop, budgets, approvals, and WS intents.

The loop's provider seam (api_provider.chat_turn_with_tools) is scripted
per test; EVERYTHING else is real - the real SceneDocument, real
ToolRegistry with the real graph/control tool handlers, a real RunRegistry
handle (so cancel/approval-future semantics are the shipped ones), and the
real run_build coroutine. Approvals are driven by a test coroutine playing
the human: it watches the plan node's awaiting flag and resolves the
handle's approval future, exactly as the approve/deny intents do.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import api_provider
from backend import builder as builder_module
from backend.builder import run_build
from backend.domain.graph import SceneDocument
from backend.providers.base import ToolCall
from backend.run_lifecycle import RunRegistry
from backend.tests.test_canvas import make_bus_with_dispatcher
from backend.tools import ToolRegistry
from backend.tools_graph import register_graph_tools, register_run_node_tool
from backend.builder import register_builder_control_tools


class FakeBus:
    def __init__(self):
        self.published: list[str] = []

    async def publish(self, topic: str):
        self.published.append(topic)


class LoopDispatcher:
    """Just enough dispatcher for run_build + run_node's seams."""

    def __init__(self):
        self._runs = RunRegistry()

    def get_pycoder_repl(self, node_id, repl_id):  # pragma: no cover - unused here
        raise AssertionError("no pycoder in these tests")

    async def dispose_pycoder_repl(self, node_id, **kwargs):  # pragma: no cover
        pass

    def _resolve_branch_system_prompt(self, document, node_id):
        return None

    def active_provider_model(self):
        return ("ollama", "fake-model")


def scripted_turns(monkeypatch, turns: list[dict], seen_messages: list):
    """Each entry: {"content": str, "tool_calls": [ToolCall...], "usage": {...}|None}.
    Popped per chat_turn_with_tools call; running out fails loudly."""
    remaining = list(turns)

    def fake_turn(task, messages, tools=(), **kwargs):
        seen_messages.append(list(messages))
        if not remaining:
            raise AssertionError("script exhausted - the loop asked for one more turn than scripted")
        entry = remaining.pop(0)
        return {
            "message": {"content": entry.get("content", ""), "role": "assistant"},
            "tool_calls": entry.get("tool_calls", []),
            "usage": entry.get("usage"),
        }

    monkeypatch.setattr(api_provider, "chat_turn_with_tools", fake_turn)


def call(cid, name, **arguments):
    return ToolCall(id=cid, name=name, arguments=arguments)


def make_harness():
    document = SceneDocument()
    dispatcher = LoopDispatcher()
    registry = ToolRegistry()
    register_graph_tools(registry, document)
    register_run_node_tool(registry, document, dispatcher)
    register_builder_control_tools(registry)
    bus = FakeBus()
    return document, dispatcher, registry, bus


def seed_plan(document, steps, *, mode="copilot", **budgets):
    node = document.add_plan_node(0, 0, "build a research summary", mode=mode, **budgets)
    node.state.plan_steps = [
        {"id": f"s{i+1}", "title": t, "status": "pending", "detail": ""}
        for i, t in enumerate(steps)
    ]
    node.state.builder_status = "awaiting_start"
    return node


async def drive_build(document, dispatcher, registry, bus, node, *, approve=True, deny_first=False):
    """Runs run_build while a driver coroutine plays the approving human.
    Returns (approvals_seen, denials_issued)."""
    cancel_event = threading.Event()
    handle = dispatcher._runs.claim("builder", node_id=node.id, cancel_event=cancel_event)
    approvals = []
    denials = {"count": 0}

    async def run():
        try:
            await run_build(
                document=document, dispatcher=dispatcher, registry=registry,
                bus=bus, notifications=None, plan_node_id=node.id,
                request_id=handle.request_id, handle=handle, cancel_event=cancel_event,
            )
        finally:
            dispatcher._runs.release(handle.request_id)

    task = asyncio.create_task(run())
    while not task.done():
        future = handle.approval_future
        if (
            node.state.builder_awaiting_tool_approval
            and future is not None
            and not future.done()
        ):
            approvals.append(node.state.builder_approval_tool_name)
            if deny_first and denials["count"] == 0:
                denials["count"] += 1
                future.set_result(False)
            else:
                future.set_result(approve)
        await asyncio.sleep(0)
    await task
    return approvals, denials["count"]


class TestExitCriterion:
    """8.3 exit: co-pilot builds a 4-node research->summary branch on the
    canvas, each step approved."""

    def test_copilot_builds_a_four_node_branch_each_step_approved(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["Gather the research", "Write the summary"])
        seen = []
        scripted_turns(monkeypatch, [
            {  # step 1, turn 1: two creates
                "tool_calls": [
                    call("c1", "graph.create_node", kind="chat", content="Topic: solar output trends", is_user=True),
                    call("c2", "graph.create_node", kind="note", content="Finding: output doubled since 2015"),
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            },
            {  # step 1, turn 2: complete
                "tool_calls": [call("c3", "builder.complete_step", summary="research gathered")],
                "usage": {"prompt_tokens": 60, "completion_tokens": 10},
            },
            {  # step 2: summary chat + a code note, connect, complete
                "tool_calls": [
                    call("c4", "graph.create_node", kind="chat", content="Summary: growth is exponential"),
                    call("c5", "graph.create_node", kind="code", content="print('totals')", language="python"),
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 30},
            },
            {
                "tool_calls": [call("c6", "builder.complete_step", summary="summary written")],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10},
            },
        ], seen)

        approvals, _ = asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "done"
        created = [n for n in document.nodes.values() if n.kind != "plan"]
        assert len(created) == 4, "the 4-node branch exists on the canvas"
        # Each mutating call was individually approved (creates only - the
        # control tools are auto and must never have prompted).
        assert approvals == ["graph.create_node"] * 4
        assert all(s["status"] == "done" for s in node.state.plan_steps)
        assert node.state.builder_spent_steps == 2
        assert node.state.builder_spent_tokens == 370
        # Every mutation is stamped with this run - one undo_run reverts
        # the whole build.
        run_id = node.state.builder_run_id
        assert run_id and all(c.run_id == run_id for c in document.command_log)
        reverted = document.undo_run(run_id)
        assert reverted == len(created)
        assert all(n.kind == "plan" for n in document.nodes.values())


class TestBudgets:
    def test_token_budget_breach_pauses_with_state_intact(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["step one", "step two"], max_tokens=100)
        scripted_turns(monkeypatch, [
            {
                "tool_calls": [call("c1", "graph.create_node", kind="note", content="x")],
                "usage": {"prompt_tokens": 90, "completion_tokens": 30},
            },
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "paused"
        assert "Token budget" in node.state.builder_status_detail
        assert node.state.builder_spent_tokens == 120
        assert node.pending_request_id is None

    def test_step_budget_pauses_before_starting_one_step_too_many(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["a", "b", "c"], max_steps=1)
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "builder.complete_step", summary="did a")]},
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "paused"
        assert "Step budget" in node.state.builder_status_detail
        assert node.state.plan_steps[0]["status"] == "done"
        assert node.state.plan_steps[1]["status"] == "pending"

    def test_turn_cap_fails_the_step_and_pauses(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["spin forever"])
        scripted_turns(monkeypatch, [
            {"content": "thinking about it..."} for _ in range(builder_module._STEP_TURN_CAP)
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "paused"
        assert node.state.plan_steps[0]["status"] == "failed"
        assert "no progress" in node.state.builder_status_detail


class TestApprovals:
    def test_a_denied_call_is_fed_back_and_the_model_adapts(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["one step"])
        seen: list = []
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "graph.create_node", kind="note", content="first try")]},
            {"tool_calls": [call("c2", "builder.complete_step", summary="ok, done without it")]},
        ], seen)

        approvals, denials = asyncio.run(
            drive_build(document, dispatcher, registry, bus, node, deny_first=True)
        )

        assert denials == 1
        assert node.state.builder_status == "done"
        assert not any(n.kind == "note" for n in document.nodes.values())
        denied_feedback = seen[1][-1]
        assert denied_feedback["role"] == "tool"
        assert "denied" in denied_feedback["content"]

    def test_the_awaiting_flags_are_cleared_after_resolution(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["one step"])
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "graph.create_node", kind="note", content="x")]},
            {"tool_calls": [call("c2", "builder.complete_step", summary="done")]},
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_awaiting_tool_approval is False
        assert node.state.builder_approval_tool_name == ""
        assert node.state.builder_approval_summary == ""


class TestAutopilot:
    def test_autopilot_auto_approves_graph_mutations_without_prompting(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["one step"], mode="autopilot")
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "graph.create_node", kind="note", content="x")]},
            {"tool_calls": [call("c2", "builder.complete_step", summary="done")]},
        ], [])

        approvals, _ = asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert approvals == [], "autopilot must not prompt for graph.mutate calls"
        assert node.state.builder_status == "done"
        assert any(n.kind == "note" for n in document.nodes.values())


class TestAutopilotNetworkGate:
    """The 8.5 exit criterion's 'no network unless approved': autopilot
    auto-approves by scope, and anything touching net.fetch still prompts -
    including run_node, whose REGISTERED scope is only graph.read (the
    exercised scope is derived per call from the target kind/action)."""

    def test_a_net_fetch_scoped_tool_still_prompts_in_autopilot(self, monkeypatch):
        from backend.providers.base import ToolSpec
        from backend.tools import NET_FETCH, ToolResult

        document, dispatcher, registry, bus = make_harness()

        async def net_handler(call, ctx):
            return ToolResult(content="fetched")

        registry.register(
            ToolSpec(name="net.probe", description="d", input_schema={"type": "object"}),
            net_handler, scopes={NET_FETCH}, approval="once",
        )
        node = seed_plan(document, ["one step"], mode="autopilot")
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "net.probe")]},
            {"tool_calls": [call("c2", "builder.complete_step", summary="done")]},
        ], [])

        approvals, _ = asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert approvals == ["net.probe"], "net.fetch must prompt even in autopilot"
        assert node.state.builder_status == "done"

    def test_run_node_research_prompts_in_autopilot_via_the_derived_scope(self, monkeypatch):
        from graphlink_plugins.web_research import service as wr_service
        from graphlink_plugins.web_research.domain import ResearchResult

        document, dispatcher, registry, bus = make_harness()
        parent = document.add_chat_node(0, 0, "ctx", True)
        research = document.add_web_research_node(0, 200, parent.id)
        research.content = "the query"
        monkeypatch.setattr(
            wr_service.WebResearchService, "run",
            lambda self, request, **kw: ResearchResult(
                request_id=request.request_id, original_query=request.original_query,
                effective_query=request.original_query, answer_markdown="answer",
            ),
        )
        node = seed_plan(document, ["research it"], mode="autopilot")
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "run_node", node_id=research.id)]},
            {"tool_calls": [call("c2", "builder.complete_step", summary="done")]},
        ], [])

        approvals, _ = asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert approvals == ["run_node"], (
            "run_node registers graph.read only - the router must derive the "
            "research action's net.fetch scope or autopilot silently reaches "
            "the network"
        )
        assert node.state.builder_status == "done"


class TestPlanPersistence:
    def test_round_trip_preserves_the_plan_and_normalizes_live_states(self):
        from backend.session_load import _restore_plan_payload
        from backend.session_save import _serialize_plan_node

        document = SceneDocument()
        node = document.add_plan_node(10, 20, "the goal", mode="autopilot", max_steps=7)
        node.state.plan_steps = [
            {"id": "s1", "title": "done step", "status": "done", "detail": "d"},
            {"id": "s2", "title": "mid-flight", "status": "running", "detail": ""},
            {"id": "s3", "title": "not yet", "status": "pending", "detail": ""},
        ]
        node.state.builder_status = "running"  # a live run when the app died
        node.state.builder_run_id = "run-9"
        node.state.builder_spent_tokens = 1234

        restored = _restore_plan_payload(_serialize_plan_node(node))

        assert restored.kind == "plan"
        assert restored.state.plan_goal == "the goal"
        assert restored.state.builder_mode == "autopilot"
        assert restored.state.builder_max_steps == 7
        assert restored.state.builder_spent_tokens == 1234
        assert restored.state.builder_run_id == "run-9"
        # The load-time normalization: no RunHandle survives a restart, so a
        # "running" build restores as interrupted (terminal, resumable) and
        # a mid-flight step as failed - never a spinner no run backs.
        assert restored.state.builder_status == "interrupted"
        statuses = [s["status"] for s in restored.state.plan_steps]
        assert statuses == ["done", "failed", "pending"]

    def test_terminal_states_round_trip_verbatim(self):
        from backend.session_load import _restore_plan_payload
        from backend.session_save import _serialize_plan_node

        document = SceneDocument()
        node = document.add_plan_node(0, 0, "g")
        node.state.builder_status = "done"
        node.state.builder_status_detail = "Build complete."

        restored = _restore_plan_payload(_serialize_plan_node(node))

        assert restored.state.builder_status == "done"
        assert restored.state.builder_status_detail == "Build complete."


class TestReplanAndAbort:
    def test_replan_replaces_pending_steps_and_preserves_history(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["first", "old second", "old third"])
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "builder.complete_step", summary="first done")]},
            # The replan lands mid-step: "old second" is RUNNING when the
            # pending tail is replaced - it must survive as the live step
            # and still complete normally afterwards.
            {"tool_calls": [call("c2", "builder.replan", steps=["new third"], reason="old third was redundant")]},
            {"tool_calls": [call("c3", "builder.complete_step", summary="second done")]},
            {"tool_calls": [call("c4", "builder.complete_step", summary="new third done")]},
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "done"
        titles = [(s["title"], s["status"]) for s in node.state.plan_steps]
        assert ("first", "done") in titles
        assert ("old second", "done") in titles
        assert ("new third", "done") in titles
        assert not any(t == "old third" for t, _ in titles)

    def test_abort_lands_failed_with_the_models_reason(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["impossible step"])
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "builder.abort", reason="the goal needs data I cannot access")]},
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "failed"
        assert "cannot access" in node.state.builder_status_detail


class TestStop:
    def test_cancel_frees_the_slot_immediately_and_finalize_lands_stopped(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["one step"])
        started = asyncio.Event()
        release = asyncio.Event()

        def slow_turn(task, messages, tools=(), **kwargs):
            asyncio.get_event_loop_policy()  # no-op; runs in a worker thread
            cancel_event = kwargs.get("cancellation_event")
            # Block until the test cancels, then honor the event like a
            # real provider checkpoint would.
            while not cancel_event.is_set():
                pass
            raise api_provider.RequestCancelledError("cancelled")

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", slow_turn)

        async def scenario():
            cancel_event = threading.Event()

            async def finalize():
                if node.state.builder_status in ("running", "awaiting_start"):
                    node.state.builder_status = "stopped"
                    node.state.builder_status_detail = "Stopped by user."
                if node.pending_request_id == handle.request_id:
                    node.pending_request_id = None

            handle = dispatcher._runs.claim(
                "builder", node_id=node.id, cancel_event=cancel_event, finalize=finalize,
            )

            async def run():
                await run_build(
                    document=document, dispatcher=dispatcher, registry=registry,
                    bus=bus, notifications=None, plan_node_id=node.id,
                    request_id=handle.request_id, handle=handle, cancel_event=cancel_event,
                )

            task = asyncio.create_task(run())
            dispatcher._runs.attach_task(handle, task)
            await asyncio.sleep(0.05)  # let the run start and enter the slow turn
            assert dispatcher._runs.is_busy("builder")

            dispatcher._runs.cancel(handle.request_id, kind="builder")
            # The slot is free the moment cancel lands - the <2s guarantee
            # is slot-release, not worker death.
            assert not dispatcher._runs.is_busy("builder")
            await asyncio.sleep(0.1)  # let finalize + the worker unwind
            assert node.state.builder_status == "stopped"

        asyncio.run(scenario())


class TestPlanning:
    def test_plan_steps_for_goal_normalizes_the_schema_payload(self, monkeypatch):
        import backend.structured_output as structured_output

        monkeypatch.setattr(
            structured_output, "respond_json",
            lambda task, messages, schema, **kwargs: {
                "steps": [{"title": "Research the topic"}, {"title": "  "}, {"title": "Summarize"}],
            },
        )
        steps = builder_module.plan_steps_for_goal("some goal")
        assert [s["title"] for s in steps] == ["Research the topic", "Summarize"]
        assert all(s["status"] == "pending" for s in steps)
        assert [s["id"] for s in steps] == ["s1", "s2"]


class TestBuilderIntents:
    """The WS surface end-to-end through the real registered intents."""

    def test_builder_start_creates_a_plan_and_lands_awaiting_start(self, monkeypatch):
        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            monkeypatch.setattr(
                builder_module, "plan_steps_for_goal",
                lambda goal, **kwargs: [
                    {"id": "s1", "title": "Research it", "status": "pending", "detail": ""},
                ],
            )
            node_id = await bus.dispatch_intent("builder", "start", ["research solar output"])
            from backend.tests.test_canvas import drain_runs

            await drain_runs(dispatcher, "builder")
            node = document.nodes[node_id]
            assert node.kind == "plan"
            assert node.state.builder_status == "awaiting_start"
            assert [s["title"] for s in node.state.plan_steps] == ["Research it"]
            # The plan-node creation command is stamped with the planning
            # run's id - undo_run reverts the plan node itself.
            creation = next(c for c in document.command_log if c.command_type == "builderPlan")
            assert creation.run_id == node.state.builder_run_id != ""

        asyncio.run(run())

    def test_set_plan_steps_edits_pending_steps_undoably(self):
        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            node = document.add_plan_node(0, 0, "goal")
            node.state.plan_steps = [
                {"id": "s1", "title": "old", "status": "pending", "detail": ""},
            ]
            node.state.builder_status = "awaiting_start"
            await bus.dispatch_intent("scene", "setPlanSteps", [
                node.id, [{"id": "s1", "title": "edited", "status": "pending", "detail": ""}],
            ])
            assert node.state.plan_steps[0]["title"] == "edited"
            document.undo()
            restored = document.nodes[node.id]
            assert restored.state.plan_steps[0]["title"] == "old"

        asyncio.run(run())

    def test_start_execution_refuses_a_non_resumable_status(self):
        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            node = document.add_plan_node(0, 0, "goal")
            node.state.builder_status = "done"
            result = await bus.dispatch_intent("builder", "startExecution", [node.id])
            assert result is None

        asyncio.run(run())

    def test_a_shipped_recipe_seeds_a_build_with_no_planning_run(self, monkeypatch):
        """8.6 exit: a shipped recipe seeds a run - the checklist lands
        immediately at awaiting_start with NO planning model call."""

        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            planning_calls = []
            monkeypatch.setattr(
                builder_module, "plan_steps_for_goal",
                lambda goal, **kwargs: planning_calls.append(goal) or [],
            )
            node_id = await bus.dispatch_intent("builder", "start", [
                "solar output trends", "copilot", None, None, None, "Research and summarize",
            ])
            node = document.nodes[node_id]
            assert node.state.builder_status == "awaiting_start"
            assert planning_calls == [], "a recipe-seeded build must not run the planner"
            titles = [s["title"] for s in node.state.plan_steps]
            assert titles == [
                "Create a web research node with the topic as its query and run it",
                "Write the findings into a summary note, citing sources",
            ]
            assert "solar output trends" in node.state.plan_goal

        asyncio.run(run())

    def test_save_recipe_then_start_from_it_seeds_the_saved_plan(self, monkeypatch):
        """8.6 exit: a user-saved build seeds a run."""

        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            source = document.add_plan_node(0, 0, "compare two datasets")
            source.state.plan_steps = [
                {"id": "s1", "title": "Load both datasets", "status": "done", "detail": ""},
                {"id": "s2", "title": "Chart the difference", "status": "done", "detail": ""},
            ]
            source.state.builder_status = "done"

            saved_name = await bus.dispatch_intent(
                "builder", "saveRecipe", [source.id, "My comparison"],
            )
            assert saved_name == "My comparison"

            listing = await bus.dispatch_intent("builder", "listRecipes", [])
            names = [r["name"] for r in listing["recipes"]]
            assert "My comparison" in names
            assert "Research and summarize" in names, "built-ins stay listed"

            monkeypatch.setattr(
                builder_module, "plan_steps_for_goal",
                lambda goal, **kwargs: (_ for _ in ()).throw(AssertionError("planner ran")),
            )
            node_id = await bus.dispatch_intent("builder", "start", [
                "", None, None, None, None, "My comparison",
            ])
            node = document.nodes[node_id]
            assert node.state.builder_status == "awaiting_start"
            assert [s["title"] for s in node.state.plan_steps] == [
                "Load both datasets", "Chart the difference",
            ]

        asyncio.run(run())

    def test_a_built_in_recipe_name_cannot_be_overwritten(self):
        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            source = document.add_plan_node(0, 0, "goal")
            source.state.plan_steps = [
                {"id": "s1", "title": "step", "status": "done", "detail": ""},
            ]
            result = await bus.dispatch_intent(
                "builder", "saveRecipe", [source.id, "Research and summarize"],
            )
            assert result is None

        asyncio.run(run())

    def test_completed_steps_cannot_be_rewritten_via_set_plan_steps(self):
        async def run():
            bus, document, recorder, dispatcher = make_bus_with_dispatcher()
            node = document.add_plan_node(0, 0, "goal")
            node.state.plan_steps = [
                {"id": "s1", "title": "already ran", "status": "done", "detail": "d"},
            ]
            await bus.dispatch_intent("scene", "setPlanSteps", [
                node.id, [{"id": "s1", "title": "rewritten history", "status": "done", "detail": ""}],
            ])
            # Refused with a notification, not applied.
            assert node.state.plan_steps[0]["title"] == "already ran"


class TestReviewFixRegressions:
    """Pinning coverage for the adversarial-review fix pass (2026-08-10)."""

    def test_autopilot_does_not_auto_approve_an_unscoped_mcp_style_tool(self, monkeypatch):
        """An empty scope set is a subset of every set, including the
        autopilot auto-approve set - it must NOT be treated as "declared
        safe". A tool registered with scopes=frozenset() (an MCP server
        configured with no scopes, the default) must still prompt."""
        from backend.providers.base import ToolSpec
        from backend.tools import ToolResult

        document, dispatcher, registry, bus = make_harness()

        async def unscoped_handler(call, ctx):
            return ToolResult(content="did something")

        registry.register(
            ToolSpec(name="mcp.unscoped_tool", description="d", input_schema={"type": "object"}),
            unscoped_handler, scopes=frozenset(), approval="always",
        )
        node = seed_plan(document, ["one step"], mode="autopilot")
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "mcp.unscoped_tool")]},
            {"tool_calls": [call("c2", "builder.complete_step", summary="done")]},
        ], [])

        approvals, _ = asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert approvals == ["mcp.unscoped_tool"], (
            "an unscoped tool must always prompt, even in autopilot - an "
            "empty scope set is a subset of the auto-approve set"
        )
        assert node.state.builder_status == "done"

    def test_two_consecutive_replans_do_not_mint_colliding_step_ids(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["s1", "s2", "s3", "s4", "s5"])
        scripted_turns(monkeypatch, [
            {"tool_calls": [call("c1", "builder.complete_step", summary="s1 done")]},
            # First replan: kept=[s1,s2], new ids minted past 5 -> s6, s7.
            {"tool_calls": [call("c2", "builder.replan", steps=["A", "B"], reason="r1")]},
            {"tool_calls": [call("c3", "builder.complete_step", summary="s2 done")]},
            {"tool_calls": [call("c4", "builder.complete_step", summary="A done")]},
            # Second replan: everything left is non-pending (kept=all 4) -
            # a length-based next_index would re-mint s6, colliding with
            # the surviving step already named s6.
            {"tool_calls": [call("c5", "builder.replan", steps=["C", "D"], reason="r2")]},
            {"tool_calls": [call("c6", "builder.complete_step", summary="B done")]},
            {"tool_calls": [call("c7", "builder.complete_step", summary="C done")]},
            {"tool_calls": [call("c8", "builder.complete_step", summary="D done")]},
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "done", node.state.builder_status_detail
        ids = [s["id"] for s in node.state.plan_steps]
        assert len(ids) == len(set(ids)), f"duplicate step ids minted across replans: {ids}"

    def test_mid_turn_budget_breach_does_not_clobber_an_already_completed_step(self, monkeypatch):
        """A turn that calls builder.complete_step THEN another tool: a
        breach discovered on the SECOND call must not reset the just-
        completed step back to pending."""
        # Token spend is credited ONCE per turn, before any of that turn's
        # tool calls are processed - only the WALL clock genuinely advances
        # between per-call breach checks within one turn (_sync_wall_spend
        # re-reads time.monotonic() on every check). A custom tool bumps a
        # fake clock as a side effect of c1's own invoke, so c1's OWN
        # breach check (before it runs) sees no breach, and c2's breach
        # check (after c1 landed) does.
        from backend.providers.base import ToolSpec
        from backend.tools import GRAPH_READ, ToolResult

        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["only step"], max_wall_seconds=30)
        fake_now = [1000.0]
        monkeypatch.setattr(builder_module.time, "monotonic", lambda: fake_now[0])

        async def complete_and_bump_clock(call, ctx):
            ctx.controls.step_completed = True
            ctx.controls.step_summary = "done"
            fake_now[0] += 10_000  # crosses max_wall_seconds AFTER c1 lands
            return ToolResult(content="Step marked complete.")

        registry.register(
            ToolSpec(name="test.complete_and_bump", description="d", input_schema={"type": "object"}),
            complete_and_bump_clock, scopes={GRAPH_READ}, approval="auto",
        )
        scripted_turns(monkeypatch, [
            {
                "tool_calls": [
                    call("c1", "test.complete_and_bump"),
                    call("c2", "graph.create_node", kind="note", content="x"),
                ],
            },
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.plan_steps[0]["status"] == "done", (
            "a breach discovered on a LATER call in the same turn must not "
            "clobber a step an EARLIER call in that turn already completed"
        )
        assert node.state.builder_status == "paused"

    def test_mid_turn_budget_breach_after_a_declared_finish_lands_done_not_paused(self, monkeypatch):
        from backend.providers.base import ToolSpec
        from backend.tools import GRAPH_READ, ToolResult

        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["only step"], max_wall_seconds=30)
        fake_now = [1000.0]
        monkeypatch.setattr(builder_module.time, "monotonic", lambda: fake_now[0])

        async def finish_and_bump_clock(call, ctx):
            ctx.controls.step_completed = True
            ctx.controls.step_summary = "done"
            ctx.controls.finished = True
            ctx.controls.finish_summary = "all done"
            fake_now[0] += 10_000  # crosses max_wall_seconds AFTER c1 lands
            return ToolResult(content="Build marked finished.")

        registry.register(
            ToolSpec(name="test.finish_and_bump", description="d", input_schema={"type": "object"}),
            finish_and_bump_clock, scopes={GRAPH_READ}, approval="auto",
        )
        scripted_turns(monkeypatch, [
            {
                "tool_calls": [
                    call("c1", "test.finish_and_bump"),
                    call("c2", "graph.create_node", kind="note", content="x"),
                ],
            },
        ], [])

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "done", (
            "a declared finish earlier in the turn must not be dropped by "
            "a budget breach discovered on a later call in that same turn"
        )

    def test_watchdog_timeout_resets_the_running_step_so_resume_does_not_skip_it(self, monkeypatch):
        from backend import agents as agents_module

        monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.05)
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["slow step"])

        def hang_turn(task, messages, tools=(), **kwargs):
            time.sleep(0.3)  # longer than the watchdog above
            raise AssertionError("should have hit the watchdog first")

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", hang_turn)

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "paused"
        assert node.state.plan_steps[0]["status"] == "pending", (
            "a step wedged at 'running' by a watchdog timeout is immutable "
            "history and permanently skipped by _current_step on resume"
        )

    def test_a_generic_provider_exception_lands_failed_but_stays_resumable(self, monkeypatch):
        document, dispatcher, registry, bus = make_harness()
        node = seed_plan(document, ["a step"])

        def boom_turn(task, messages, tools=(), **kwargs):
            raise RuntimeError("rate limited")

        monkeypatch.setattr(api_provider, "chat_turn_with_tools", boom_turn)

        asyncio.run(drive_build(document, dispatcher, registry, bus, node))

        assert node.state.builder_status == "failed"
        assert node.state.plan_steps[0]["status"] == "pending", (
            "the in-flight step must not stay wedged at 'running' behind a "
            "now-resumable 'failed' status"
        )

        from backend.api.intents_builder import _RESUMABLE_STATUSES

        assert "failed" in _RESUMABLE_STATUSES, (
            "a transient provider fault must not permanently kill a build "
            "whose goal/checklist/spent budgets are still on the canvas"
        )
