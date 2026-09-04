"""BuilderDispatchOps - the Builder agent loop: tool-registry assembly
(MCP + plugin tools) and the Builder planning run.

A MIXIN, not a standalone class: every method operates on the composing
class's shared state established by DispatcherCoreOps.__init__ - it is
composed exactly once, by backend/agents.py's
`class AgentDispatcher(DispatcherCoreOps, ...)`.

Method bodies are relocated VERBATIM from backend/agents.py; only the class
wrapper, imports, and the patch-seam rewrites below are new. Any name that
lives in backend/agents.py's module namespace (module helpers, constants,
names imported into it) is accessed late-bound as `agents_module.<name>`
through an in-body deferred import, NEVER via a module-top import here: a
top-level `from backend.agents import X` would be a circular import
(agents.py imports this module) AND would freeze the name at import time,
making the test suite's `monkeypatch.setattr(backend.agents, "X", ...)`
patches invisible to these methods. The deferred-import-then-attribute
pattern resolves the name on backend.agents at call time, so those patch
seams keep working with zero test changes.
"""

from __future__ import annotations

import asyncio
import threading

from backend.agent_dispatch._composed import DispatcherParts


class BuilderDispatchOps(DispatcherParts):
    """The Builder agent loop and its tool-registry assembly (mixin - see module docstring)."""

    def builder_tool_registry(self, document) -> "object":
        """The session's one ToolRegistry, built lazily on first builder
        start (ADR-007 shipped the registry with zero production
        constructors; the Builder is its designated first consumer).
        Cached: tools bind the session's own SceneDocument/dispatcher, and
        both live exactly as long as this dispatcher does."""
        if getattr(self, "_builder_registry", None) is None:
            from backend.builder import register_builder_control_tools
            from backend.tools import ToolRegistry
            from backend.tools_graph import register_graph_tools, register_run_node_tool
            from backend.tools_knowledge import register_knowledge_tools

            registry = ToolRegistry()
            register_graph_tools(registry, document)
            register_run_node_tool(registry, document, self)
            # SECURITY-FIX: document scopes knowledge.search to THIS
            # session's own current workspace - see that tool's own
            # docstring (backend/tools_knowledge.py) for why an unscoped
            # search tool handed to the model is a cross-workspace read
            # primitive for prompt-injected content.
            register_knowledge_tools(registry, document=document)
            register_builder_control_tools(registry)
            self._register_configured_mcp_tools(registry)
            self._register_plugin_tools(registry, document)
            self._builder_registry = registry
        return self._builder_registry

    def invalidate_builder_registry(self) -> None:
        """SECURITY-FIX: builder_tool_registry() above builds the registry
        ONCE and caches it for this dispatcher's entire lifetime, with
        nothing to invalidate it - so disabling or removing an MCP server,
        or narrowing its enabled_tools/scopes/approval, in Settings had no
        effect on a session whose Builder had already started once: the
        cached registry (and the already-connected McpStdioClient in
        self._mcp_clients) kept granting the OLD tools under the OLD scopes
        for the rest of the session, silently outliving the setting meant to
        revoke them. Called from the setMcpServers intent (backend/api/
        intents_settings_general.py) right after a save, so the Builder's
        NEXT start rebuilds from the just-persisted config. Deliberately
        just a cache-clear, not a live teardown: an in-flight Builder run
        keeps the registry (and MCP clients) it already bound - this only
        stops a STALE registry from surviving past the settings change that
        was supposed to retire it."""
        self._builder_registry = None

    def _register_configured_mcp_tools(self, registry) -> None:
        """ADR-008 stage 8.5: ADR-007's deferred MCP runtime wiring lands in
        its designated consumer. Reads the persisted server list, connects
        each ENABLED one, and registers its tools (namespaced, per-server
        scopes, approval="always" by config default - MCP servers are
        untrusted user-configured code). Per-server failure tolerance: a
        server that won't start/handshake is logged + surfaced as a warning
        and skipped - one broken config must not cost the Builder its graph
        tools. Connected clients are kept for disposal at session end."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        from backend.mcp_client import McpError, McpServerConfig, McpStdioClient, register_mcp_server_tools

        if self._settings_manager is None:
            return
        try:
            server_dicts = self._settings_manager.get_mcp_servers()
        except Exception:
            agents_module.logger.exception("could not read MCP server config")
            return
        self._mcp_clients: list = getattr(self, "_mcp_clients", [])
        for raw in server_dicts:
            try:
                config_entry = McpServerConfig.from_dict(raw)
            except Exception:
                agents_module.logger.exception("malformed MCP server entry skipped")
                continue
            if not config_entry.enabled:
                continue
            client = McpStdioClient(
                command=config_entry.command, args=tuple(config_entry.args),
                env=config_entry.env, timeout=config_entry.timeout,
            )
            try:
                client.connect()
                registered = register_mcp_server_tools(registry, client, config_entry)
                self._mcp_clients.append(client)
                agents_module.logger.info(
                    "MCP server connected", extra={"kind": "builder"},
                )
                _ = registered
            except (McpError, OSError, ValueError) as exc:
                # SECURITY-FIX: ValueError was NOT caught here, only McpError/
                # OSError. registry.register() raises ValueError on a
                # duplicate tool name, an unknown approval policy, or an
                # unknown scope - all of which a hostile-or-updated MCP
                # server (its tools/list returning two same-named tools -
                # threat (d)) or a hand-edited session.dat (an approval/scope
                # value outside the registry vocabulary - threat (c), since
                # the settings store passes those through unvalidated) can
                # trigger, as can an ordinary user configuring two servers
                # with the same name (the store allows it). It escaped this
                # loop into start_builder_run's task, which has only a
                # `finally: release` - so run_build never ran, no _land()/
                # notification fired, the Builder registry was never cached,
                # and every subsequent Start re-spawned every server and
                # crashed again, leaking a subprocess each time (client.close
                # below was skipped on this path). Catching it here restores
                # the per-server tolerance this loop's own contract promises:
                # the bad server is logged and skipped, its client closed,
                # and the Builder still starts with every other server.
                agents_module.logger.warning("MCP server %r unavailable: %s", config_entry.name, exc)
                try:
                    client.close()
                except Exception:
                    pass

    def _register_plugin_tools(self, registry, document) -> None:
        """ADR-014 stage 14.5: plugin-declared custom intents (HostContext.
        register_intent, both in-process and out-of-process plugins) become
        real Builder-loop tools - see backend/plugins.py's
        register_plugin_tools for the full contract (namespacing, scopes,
        approval, the install-time-grant re-check). Uses discover_plugins()'s
        own memoized registry (the SAME one register_plugins() populates for
        the session's bus-level executePlugin/invokePluginIntent dispatch,
        by resolved plugins_root path) - discovery itself already ran by the
        time any session can reach this point (register_plugins() runs at
        session activation, before the Builder can ever start), so this is
        just a second consumer of an already-populated registry, not a
        second discovery pass - matching _register_configured_mcp_tools'
        own "read the persisted config, tolerate per-item failure" shape
        directly above."""
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        if self._settings_manager is None:
            return
        from backend.plugin_sdk import discover_plugins
        from backend.plugins import register_plugin_tools

        try:
            plugin_registry = discover_plugins()
            register_plugin_tools(registry, plugin_registry, self._settings_manager, document)
        except Exception:
            agents_module.logger.exception("could not register plugin-declared tools")

    async def start_builder_run(
        self,
        *,
        bus,
        notifications_state,
        document,
        plan_node_id: str,
        phase: str,
        model_ref=None,
    ) -> str | None:
        """Claims the "builder" kind and runs one Builder phase as a
        background task: "plan" (one respond_json turn -> the checklist ->
        awaiting_start) or "execute" (backend/builder.run_build - the
        tool-use loop). One build per session at a time (the same
        kind-level busy guard every other run kind has). Stop lands
        through finalize: RunRegistry.cancel frees the slot immediately
        and finalize writes the terminal "stopped" state, so the UI
        returns to idle without waiting for the worker (6.2 posture)."""
        from backend.domain.node_states import PlanState

        if self._runs.is_busy("builder"):
            notifications_state.show("A build is already running.", "info")
            await bus.publish("notification")
            return None
        node = document.nodes.get(plan_node_id)
        if node is None or not isinstance(node.state, PlanState):
            notifications_state.show("This plan node no longer exists.", "warning")
            await bus.publish("notification")
            return None

        cancel_event = threading.Event()

        async def _finalize() -> None:
            if node.state.builder_status in ("planning", "running", "awaiting_start"):
                node.state.builder_status = "stopped"
                node.state.builder_status_detail = "Stopped by user."
            node.state.builder_awaiting_tool_approval = False
            node.state.builder_approval_tool_name = ""
            node.state.builder_approval_summary = ""
            if node.pending_request_id == handle.request_id:
                node.pending_request_id = None
            await bus.publish("scene")

        handle = self._runs.claim(
            "builder", node_id=plan_node_id, cancel_event=cancel_event, finalize=_finalize,
        )
        request_id = handle.request_id

        async def _run() -> None:
            from backend import builder as builder_module

            try:
                if phase == "plan":
                    await self._run_builder_planning(
                        bus, notifications_state, document, node, request_id, cancel_event,
                    )
                else:
                    await builder_module.run_build(
                        document=document, dispatcher=self,
                        registry=self.builder_tool_registry(document),
                        bus=bus, notifications=notifications_state,
                        plan_node_id=plan_node_id, request_id=request_id,
                        handle=handle, cancel_event=cancel_event,
                        model_ref=model_ref, settings_manager=self._settings_manager,
                        **self._runtime_kwargs(),
                    )
            finally:
                self._runs.release(request_id)

        self._runs.attach_task(handle, asyncio.create_task(_run()))
        return request_id

    async def _run_builder_planning(
        self, bus, notifications_state, document, node, request_id: str, cancel_event,
    ) -> None:
        from backend import agents as agents_module  # deferred: patch-seam + circular-import safety
        from backend import builder as builder_module

        node.state.builder_status = "planning"
        node.state.builder_run_id = request_id
        node.pending_request_id = request_id
        await bus.publish("scene")
        try:
            steps = await asyncio.wait_for(
                asyncio.to_thread(
                    builder_module.plan_steps_for_goal, node.state.plan_goal,
                    settings_manager=self._settings_manager, **self._runtime_kwargs(),
                ),
                timeout=agents_module.WATCHDOG_TIMEOUT_SECONDS,
            )
            if self._runs.get(request_id) is None:
                return
            if not steps:
                node.state.builder_status = "failed"
                node.state.builder_status_detail = "The planner produced no steps - refine the goal and try again."
            else:
                document.record_command(
                    "builderPlan", "agent",
                    lambda: document.set_plan_steps(node.id, steps),
                    node_ids=[node.id], run_id=request_id,
                )
                node.state.builder_status = "awaiting_start"
                node.state.builder_status_detail = ""
        except agents_module.api_provider.RequestCancelledError:
            return  # finalize owns the stopped transition
        except Exception as exc:
            if self._runs.get(request_id) is None:
                return
            node.state.builder_status = "failed"
            node.state.builder_status_detail = f"Planning failed: {exc}"
            notifications_state.show(f"Planning failed: {exc}", "error")
            await bus.publish("notification")
        finally:
            if node.pending_request_id == request_id:
                node.pending_request_id = None
            await bus.publish("scene")

    def cancel_builder(self, request_id: str) -> None:
        """Stop for the Builder: deny any parked approval first (cancel
        means deny - the code_sandbox precedent), then the standard
        release-on-cancel. See _cancel_with_pending_approval_denied for the
        shared shape and its own docstring for why the kind check inside it
        is load-bearing."""
        self._cancel_with_pending_approval_denied(request_id, "builder")

    # -- PLAN-2026-08-24 H1: the agent harness -------------------------------
