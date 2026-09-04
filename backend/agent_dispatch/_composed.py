"""What every backend/agent_dispatch mixin relies on the composed
AgentDispatcher to provide.

The nine `*DispatchOps` classes are mixins, not standalone types: each is
composed exactly once, by
`class AgentDispatcher(DispatcherCoreOps, BuilderDispatchOps, ...)` in
backend/agents.py. They use `self._runs`, `self._settings_manager` and a few
of DispatcherCoreOps' own methods across the composition - correct at
runtime, invisible to a type checker reading one mixin on its own.

Same shape, and same fix, as settings_store/_composed.py's
SettingsManagerParts and backend/domain/_composed.py's SceneDocumentParts.
See the first of those for the full reasoning.

EVERYTHING HERE IS TYPE_CHECKING-ONLY: at runtime the class is empty, so
inheriting it adds no attributes, no methods and no `__init__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.events import SessionBus
    from graphlink_settings_store import SettingsManager
    from backend.tools import ToolRegistry


class DispatcherParts:
    """Type-only declaration of the composed AgentDispatcher's shared surface.

    DispatcherCoreOps inherits it too: it supplies most of what is declared
    here and consumes the rest, the same way PersistenceOps does in
    settings_store.
    """

    if TYPE_CHECKING:
        # Established by DispatcherCoreOps.__init__.
        _runs: Any                      # backend.run_lifecycle.RunRegistry
        _settings_manager: SettingsManager
        _provider_runtime: Any
        _diagnostics: Any

        def _runtime_kwargs(self) -> dict: ...

        def _cancel_with_pending_approval_denied(self, request_id: str, kind: str) -> bool: ...

        # Narrowed from `object`: BuilderDispatchOps returns a real
        # ToolRegistry, and declaring that here is what lets sibling
        # mixins call it without restating the type at each site.
        def builder_tool_registry(self, document: Any) -> "ToolRegistry": ...

        # The run engine every start_* surface funnels through, and the
        # plain-blocking-action skeleton the gitlink/code-review surfaces
        # share. Both live on DispatcherCoreOps.
        async def _dispatch(self, *args: Any, **kwargs: Any) -> Any: ...

        async def _run_node_blocking_action(
            self,
            *,
            bus: SessionBus,
            notifications_state: Any,
            node: Any,
            action: Any,
            timeout: float,
            timeout_message: str,
            error_log_message: str,
            error_notify_prefix: str,
            default: Any = None,
        ) -> Any: ...
