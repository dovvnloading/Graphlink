"""ADR-007 stage 7.2: ToolRegistry - the capability/permission model gating
every tool a model may call.

Builds directly on stage 7.1's provider-neutral ToolSpec/ToolCall (backend/
providers/base.py): a provider normalizes a model's request into a ToolCall,
and THIS module decides whether it may actually run, against two independent
gates named in the ADR's own §2:

- **Scopes** (`graph.read`, `graph.mutate`, `fs.read`, `code.execute`,
  `net.fetch`, `provider.call`) - a run is granted a scope set (RunContext.
  granted_scopes); a tool registered outside it is denied BEFORE its handler
  ever runs, matching the exit criterion's own wording ("denied pre-handler").
- **Approval policy** (`auto` / `once` / `always`) - a per-tool choice the
  registrant makes at register() time, never inferred from scopes. `auto`
  tools (read-only/idempotent) run unprompted; `once`/`always` tools call
  RunContext.request_approval - the caller's job to route that to whatever
  UI/approval surface a real run uses (this module owns the GATE, not the
  human-facing panel, mirroring how RunHandle.approval_future in backend/
  agents.py is itself just a bookkeeping primitive that start_pycoder_run/
  start_code_sandbox_run route to the approval panel). `always` additionally
  remembers a fingerprint of (name, arguments) for the lifetime of the
  RunContext, so a repeated IDENTICAL call in the same run does not re-prompt
  - reusing _fingerprint_changes (graphlink_plugins/gitlink/agent.py, sha256
  of canonical sort_keys JSON) exactly as backend/agents.py's own pycoder/
  code_sandbox approval gates already do ("reused here rather than
  reinvented" - node_states.py's own PycoderState docstring), not a
  reinvented hash. Because the fingerprint is computed fresh from THIS call's
  own arguments at invoke() time (never a separately-stored snapshot checked
  again later), there is no TOCTOU window to guard against the way gitlink's
  own atomic check-and-freeze does - a changed argument simply produces a
  different fingerprint and is never considered pre-approved.

invoke() never raises for an EXPECTED denial (unknown tool, out-of-scope,
approval denied, handler exception) - each becomes an error ToolResult, the
shape a tool-use loop feeds back to the model as the tool's own result text,
not a crash of the whole turn. Cancellation is the one exception: mirroring
every Provider.stream()'s own first line, invoke() raises the same
RequestCancelledError a cancelled provider call would, so a tool-use loop's
cancellation handling stays the ONE mechanism regardless of whether the
in-flight step is a provider call or a tool call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from api_provider import RequestCancelledError, _raise_if_cancelled
from backend.providers.base import CancelToken, ToolCall, ToolSpec
from graphlink_plugins.gitlink.agent import _fingerprint_changes

GRAPH_READ = "graph.read"
GRAPH_MUTATE = "graph.mutate"
FS_READ = "fs.read"
# PLAN-2026-08-24 H2: writes confined to a harness workspace
# (backend/harness/tools_fs.py's fs.write/fs.edit) - a separate scope from
# FS_READ so a run's grant set can offer reads without writes; the plan's
# decision #1 keeps shell/python execution on CODE_EXECUTE rather than
# minting a third scope here.
FS_WRITE = "fs.write"
CODE_EXECUTE = "code.execute"
NET_FETCH = "net.fetch"
PROVIDER_CALL = "provider.call"
# ADR-017 stage 17.2: read-only access to the local knowledge store
# (backend/knowledge_store.py) - distinct from FS_READ, since it gates a
# tool that only ever reads FROM the already-ingested store, never an
# arbitrary path on disk.
KNOWLEDGE_READ = "knowledge.read"

# The ADR's own closed vocabulary (§2) - register() rejects anything outside
# it immediately, the same fail-fast posture EVENT_TYPES/ProviderEvent.type
# already take for their own closed vocabularies (backend/providers/base.py).
KNOWN_SCOPES = frozenset({
    GRAPH_READ, GRAPH_MUTATE, FS_READ, FS_WRITE, CODE_EXECUTE, NET_FETCH, PROVIDER_CALL, KNOWLEDGE_READ,
})

ApprovalPolicy = Literal["auto", "once", "always"]
_KNOWN_APPROVAL_POLICIES = frozenset({"auto", "once", "always"})


@dataclass(frozen=True)
class ToolResult:
    """What invoke() always returns - `content` is exactly the string a tool-
    use loop feeds back as the next turn's {"role": "tool", ..., "content":
    content} message (ChatRequest's own docstring, ADR-007 stage 7.1).
    `is_error` is informational for the loop/UI (e.g. to style a failed call
    differently on the canvas in stage 7.4) - it does not change the wire
    shape a denied/failed call still needs to hand back to the model."""

    content: str
    is_error: bool = False


# A handler is the registrant's own async implementation - it receives the
# normalized ToolCall and the run's RunContext (for anything scope/approval-
# independent it still needs, e.g. reading which node/session this run is
# for) and returns the ToolResult invoke() hands back untouched on success.
ToolHandler = Callable[[ToolCall, "RunContext"], Awaitable[ToolResult]]


@dataclass
class RunContext:
    """Everything ToolRegistry.invoke() needs beyond the call itself, all
    supplied by the tool-use loop that owns this run - this module never
    constructs one itself.

    - granted_scopes: the scope set THIS run was started with; static for
      the run's lifetime (a mid-run scope change is a new run, not a
      mutation - mirrors how a provider's capabilities are fixed at
      construction, never mutated by request).
    - request_approval: routes a `once`/`always` tool's approval prompt to
      whatever real surface the caller wires up (WS approval panel, a CLI
      prompt, an always-True fake in tests) - this module owns only the
      DECISION to call it, never the UI. Must not raise; a caller that wants
      "approval unavailable" to read as denial should have this return
      False, not throw.
    - cancel: optional, mirrors every Provider.stream()'s own CancelToken -
      None (the default) means this run has no cancellation affordance,
      exactly like a provider's `CancelToken()` with no wrapped event.
    - _approved_fingerprints: `always`-policy memory, private to this run -
      external code has no reason to read or seed it directly."""

    granted_scopes: frozenset[str]
    request_approval: Callable[[ToolCall], Awaitable[bool]]
    cancel: CancelToken | None = None
    _approved_fingerprints: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Registration:
    spec: ToolSpec
    handler: ToolHandler
    scopes: frozenset[str]
    approval: str


def _tool_call_fingerprint(call: ToolCall) -> str:
    # Deliberately excludes call.id: id correlates a specific call to its
    # result (ToolCall's own docstring) and is provider-synthesized noise
    # for approval-memory purposes - two calls with the same name+arguments
    # are the same DECISION to a human approver regardless of which id a
    # provider happened to mint for either one.
    return _fingerprint_changes({"name": call.name, "arguments": call.arguments})


class ToolRegistry:
    """One registry per process/session (not per-run - RunContext is what's
    per-run); registrations are typically made once at startup by whatever
    wires up the agent runtime (ADR-008), not per tool call."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        scopes: set[str] | frozenset[str],
        approval: str,
    ) -> None:
        if approval not in _KNOWN_APPROVAL_POLICIES:
            raise ValueError(
                f"Unknown approval policy {approval!r} for tool {spec.name!r} - "
                f"must be one of {sorted(_KNOWN_APPROVAL_POLICIES)}."
            )
        scope_set = frozenset(scopes)
        unknown_scopes = scope_set - KNOWN_SCOPES
        if unknown_scopes:
            raise ValueError(
                f"Unknown scope(s) {sorted(unknown_scopes)} for tool {spec.name!r} - "
                f"must be a subset of {sorted(KNOWN_SCOPES)}."
            )
        if spec.name in self._registrations:
            raise ValueError(f"Tool {spec.name!r} is already registered.")
        self._registrations[spec.name] = _Registration(spec, handler, scope_set, approval)

    def scopes_for(self, name: str) -> frozenset[str] | None:
        """The scope set `name` was registered with, or None for an unknown
        tool - ADR-008's mode-aware approval router keys autopilot's
        auto-approve decision on this (a call whose scopes fit inside the
        autopilot set proceeds; anything touching net.fetch still prompts)."""
        registration = self._registrations.get(name)
        return frozenset(registration.scopes) if registration is not None else None

    def approval_for(self, name: str) -> str | None:
        """The approval policy `name` was registered with ("auto" | "once" |
        "always"), or None for an unknown tool. SECURITY-FIX: builder.py's
        autopilot router used to decide purely on scopes_for(), never
        reading this - so a tool registered approval="always" precisely
        because its effect is destructive (graph.delete_node) was
        auto-approved in autopilot like any other graph.mutate tool. The
        router now refuses to auto-approve an "always" tool in any mode."""
        registration = self._registrations.get(name)
        return registration.approval if registration is not None else None

    def specs(self) -> tuple[ToolSpec, ...]:
        """Every registered tool's neutral spec, in registration order - what
        a caller passes as ChatRequest.tools for a run granted access to all
        of them (a real caller will usually filter this by RunContext.
        granted_scopes first; this method does not filter, since "what
        exists" and "what this run may use" are independent questions)."""
        return tuple(registration.spec for registration in self._registrations.values())

    async def invoke(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)

        registration = self._registrations.get(call.name)
        if registration is None:
            return ToolResult(content=f"Unknown tool: {call.name!r}.", is_error=True)

        # Scope check runs BEFORE any approval prompt - denied-by-scope
        # is a static, cheap decision that must never cost a human an
        # approval prompt for a call that was never going to be allowed.
        missing_scopes = registration.scopes - ctx.granted_scopes
        if missing_scopes:
            return ToolResult(
                content=(
                    f"Tool {call.name!r} requires scope(s) {sorted(missing_scopes)} "
                    "that this run was not granted."
                ),
                is_error=True,
            )

        if registration.approval != "auto":
            fingerprint = _tool_call_fingerprint(call) if registration.approval == "always" else None
            already_approved = fingerprint is not None and fingerprint in ctx._approved_fingerprints
            if not already_approved:
                approved = await ctx.request_approval(call)
                # The approval prompt is the one place this function can be
                # awaiting for a genuinely long time (a human deciding) -
                # re-check cancellation on return, same cooperative-
                # checkpoint posture cancel_pycoder/cancel_code_sandbox
                # already document (backend/agents.py): not a true
                # mid-await interrupt, but the run must not silently execute
                # a handler after the user cancelled while this was pending.
                _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)
                if not approved:
                    return ToolResult(content=f"Tool call {call.name!r} was denied approval.", is_error=True)
                if fingerprint is not None:
                    ctx._approved_fingerprints.add(fingerprint)

        try:
            return await registration.handler(call, ctx)
        except RequestCancelledError:
            # ADR-008 stage 8.2: a long-running handler (run_node parks in
            # code execution / model calls for minutes) observing the SAME
            # cancel event this function checks at its own checkpoints must
            # follow the same contract - cancellation propagates to the
            # loop, never fed back to the model as a tool "error" it would
            # then try to reason about.
            raise
        except Exception as exc:
            return ToolResult(content=f"Tool {call.name!r} raised {type(exc).__name__}: {exc}", is_error=True)
