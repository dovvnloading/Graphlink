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
  agents.py is itself just a bookkeeping primitive that
  start_code_sandbox_run routes to the approval panel). `always` additionally
  remembers a fingerprint of (name, arguments) for the lifetime of the
  RunContext, so a repeated IDENTICAL call in the same run does not re-prompt
  - reusing _fingerprint_changes (graphlink_plugins/gitlink/agent.py, sha256
  of canonical sort_keys JSON) exactly as backend/agents.py's own
  code_sandbox approval gate already does ("reused here rather than
  reinvented" - node_states.py's own CodeSandboxState docstring), not a
  reinvented hash. Because the fingerprint is computed fresh from THIS call's
  own arguments at invoke() time, changed arguments produce a different
  fingerprint. Python is deliberately stricter: every call re-prompts,
  arguments are copied before disclosure, and a pending mutation is refused.

Concurrent execution is an explicit, opt-in registration property
(`parallel_safe=True`), not inferred from `approval="auto"`: some
auto-approved tools change the run's state or wait for a human.

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

from copy import deepcopy
from dataclasses import dataclass, field
import time
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
# Approved ingestion into this run's current workspace collection. Never
# conflate a read-only knowledge grant with permission to retain new files.
KNOWLEDGE_WRITE = "knowledge.write"

# The ADR's own closed vocabulary (§2) - register() rejects anything outside
# it immediately, the same fail-fast posture EVENT_TYPES/ProviderEvent.type
# already take for their own closed vocabularies (backend/providers/base.py).
KNOWN_SCOPES = frozenset({
    GRAPH_READ, GRAPH_MUTATE, FS_READ, FS_WRITE, CODE_EXECUTE, NET_FETCH, PROVIDER_CALL,
    KNOWLEDGE_READ, KNOWLEDGE_WRITE,
})

ApprovalPolicy = Literal["auto", "once", "always"]
_KNOWN_APPROVAL_POLICIES = frozenset({"auto", "once", "always"})
# These registrations must never inherit name-wide consent, even if an
# approval callback returns a forged or stale "session" decision.
_NON_SESSION_GRANT_TOOLS = frozenset({
    "graph.delete_node", "python.exec", "python.install", "shell.exec",
    "shell.session", "web.fetch", "web.search", "knowledge.add",
})


@dataclass(frozen=True)
class ToolResult:
    """A text tool result, optionally carrying an ephemeral image preview.

    content is always the text returned to the model in its tool-result
    message (including a useful description for text-only models).
    image_bytes is a resized JPEG preview used only by the workspace harness:
    it is sent as a separate multimodal observation when the model supports
    vision and is NEVER written to the JSONL transcript.
    """

    content: str
    is_error: bool = False
    image_bytes: bytes | None = None


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
    # Returns False/None to deny, the string "session" to approve AND
    # remember for the agent session, or any other truthy value to approve
    # this call only (PLAN-2026-08-24 §2.4's graded consent). A plain bool -
    # what every pre-graded caller returns - behaves exactly as before.
    request_approval: Callable[[ToolCall], Awaitable["bool | str"]]
    cancel: CancelToken | None = None
    # Session-scoped grants: tool NAMES the user approved for the whole
    # agent session. Owned by the caller (the harness dispatcher keeps one
    # set per workspace, so it outlives any single run) and passed in here;
    # None means this run offers no session-scoped option at all, which is
    # the case for every non-harness surface today.
    session_grants: set[str] | None = None
    # None outside the workspace agent. The live node list is shared with the
    # harness so adding/removing a rule takes effect during the same run.
    shell_allow_rules: list[str] | None = None
    # Optional per-run catalog, narrower than the scope grant when needed.
    offered_tool_names: frozenset[str] | None = None
    _approved_fingerprints: set[str] = field(default_factory=set)
    # Human approval wait is not tool execution. The workspace activity
    # timer subtracts it per call; the registry records it at the actual
    # gate so streaming tools and normal tools use the same definition.
    approval_wait_seconds: dict[str, float] = field(default_factory=dict, repr=False)
    # Per-task network consent: domain grants never outlive this RunContext.
    # Web handlers manage their own domain-level prompts, not tool fingerprints.
    _approved_web_domains: set[str] = field(default_factory=set)
    # Reuse Web Research's per-origin crawl state within one workspace task,
    # without sharing fetchers or domain consent across RunContexts.
    _workspace_web_fetch_service: object | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Registration:
    spec: ToolSpec
    handler: ToolHandler
    scopes: frozenset[str]
    approval: str
    # PLAN-2026-08-24 §2.4: an optional per-call predicate that forces a
    # FRESH prompt even when this exact (name, arguments) pair was already
    # approved in this run - "a dangerous-command list always re-prompts and
    # defeats remembered grants". None leaves the existing fingerprint
    # memo unchanged; arbitrary execution tools opt into fresh consent.
    always_reprompt: "Callable[[ToolCall], bool] | None" = None
    # Only independent, read-only calls opt into bounded parallel execution
    # by the workspace harness. Default serial, including auto-approved
    # interaction tools (plan.update/user.ask).
    parallel_safe: bool = False


def _tool_call_fingerprint(call: ToolCall) -> str:
    # Deliberately excludes call.id: id correlates a specific call to its
    # result (ToolCall's own docstring) and is provider-synthesized noise
    # for approval-memory purposes - two calls with the same name+arguments
    # may reuse approval only when the tool's own policy permits it. Stateful
    # Python and executable shell calls always require a new decision.
    return _fingerprint_changes({"name": call.name, "arguments": call.arguments})


class ToolRegistry:
    """One catalog per agent consumer/session, not per run (RunContext is
    per-run). A consumer may fork the registered Builder catalog before adding
    its own tools, so scope overlap never publishes workspace tools to Builder."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def fork(self) -> "ToolRegistry":
        """Copy immutable registrations into an independent tool catalog.

        Handlers and their approval/scope metadata remain the same, but
        subsequent register() calls cannot publish tools to the source
        registry. This lets the workspace Agent reuse Builder graph/MCP
        handlers without leaking Agent-only tools into Builder turns.
        """
        registry = ToolRegistry()
        registry._registrations = self._registrations.copy()
        return registry

    def register(
        self,
        spec: ToolSpec,
        handler: ToolHandler,
        *,
        scopes: set[str] | frozenset[str],
        approval: str,
        always_reprompt: "Callable[[ToolCall], bool] | None" = None,
        parallel_safe: bool = False,
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
        if parallel_safe and (
            approval != "auto" or scope_set & {GRAPH_MUTATE, FS_WRITE, KNOWLEDGE_WRITE, CODE_EXECUTE}
        ):
            raise ValueError(
                "Parallel-safe tools must be auto-approved and may not have "
                "mutation or execution scopes."
            )
        if spec.name in self._registrations:
            raise ValueError(f"Tool {spec.name!r} is already registered.")
        self._registrations[spec.name] = _Registration(
            spec, handler, scope_set, approval, always_reprompt, parallel_safe,
        )

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

    def allows_session_grant(self, name: str) -> bool:
        """Only 'once' registrations may use tool-name-wide session consent.

        'always' is intentionally per-effect (including plugin/MCP tools).
        The registry enforces this independently of what the UI offers.
        """
        registration = self._registrations.get(name)
        return bool(
            registration is not None and registration.approval == "once"
            and name not in _NON_SESSION_GRANT_TOOLS
            and not name.startswith("fal.")
        )

    def parallel_safe_for(self, name: str) -> bool:
        """Only explicitly registered independent read tools run concurrently.

        An unknown tool or an auto-approved interactive tool remains serial.
        """
        registration = self._registrations.get(name)
        return bool(registration is not None and registration.parallel_safe)

    def specs(self) -> tuple[ToolSpec, ...]:
        """Every registered tool's neutral spec, in registration order - what
        a caller passes as ChatRequest.tools for a run granted access to all
        of them (a real caller will usually filter this by RunContext.
        granted_scopes first; this method does not filter, since "what
        exists" and "what this run may use" are independent questions)."""
        return tuple(registration.spec for registration in self._registrations.values())

    async def invoke(self, call: ToolCall, ctx: RunContext) -> ToolResult:
        _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)

        if ctx.offered_tool_names is not None and call.name not in ctx.offered_tool_names:
            return ToolResult(
                content=f"Tool {call.name!r} was not offered to this run.",
                is_error=True,
            )

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

        # A ToolCall is frozen only at the dataclass level: its arguments
        # dict is still mutable. Keep private execution arguments distinct
        # from both the caller's object and the object sent to the approval
        # callback. Refuse a changed pending request, without caching consent.
        original_shell_call = call if call.name == "shell.exec" else None
        if original_shell_call is not None:
            from backend.harness.shell_isolation import network_request_error

            error = network_request_error(call.arguments)
            if error is not None:
                return ToolResult(content=error, is_error=True)
            call = ToolCall(id=call.id, name=call.name, arguments=deepcopy(call.arguments))
        # Interactive session start and stdin write execute code too. Snapshot
        # their arguments independently of the disclosure callback.
        original_session_call = call if call.name == "shell.session" else None
        if original_session_call is not None:
            call = ToolCall(id=call.id, name=call.name, arguments=deepcopy(call.arguments))
        original_python_call = call if call.name in ("python.exec", "python.install") else None
        if original_python_call is not None:
            if call.name == "python.exec":
                from backend.harness.python_policy import source_error

                error = source_error(call.arguments)
            else:
                from backend.harness.python_install import install_requirements_error

                error = install_requirements_error(call.arguments)
            if error is not None:
                return ToolResult(content=error, is_error=True)
            call = ToolCall(id=call.id, name=call.name, arguments=deepcopy(call.arguments))

        # File paths and content are untrusted mutable dict entries too.
        # knowledge.add is also an approved path-to-persistent-data mutation.
        # Freeze them before policy evaluation and preview so neither a
        # pending approval nor Auto-edit can execute a changed request.
        original_file_call = call if call.name in ("fs.write", "fs.edit", "knowledge.add") else None
        if original_file_call is not None:
            # A provider may reuse a call id. Never inherit a precondition
            # from a prior denied/stale approval or an Auto-edit invocation.
            file_states = getattr(ctx, "harness_file_approval_states", None)
            if isinstance(file_states, dict):
                file_states.pop(call.id, None)
            call = ToolCall(id=call.id, name=call.name, arguments=deepcopy(call.arguments))

        # Only selected workspace operations may bypass an ordinary approval.
        # The scope gate and all handler-level confinement stay in force.
        from backend.harness.permission_modes import (
            may_skip_workspace_approval, requires_explicit_workspace_approval,
        )

        if registration.approval != "auto" and not may_skip_workspace_approval(call, ctx):
            fingerprint = _tool_call_fingerprint(call) if registration.approval == "always" else None
            # §2.4's dangerous-command rule: a predicate that says "always
            # ask" wins over the memo AND declines to write a new memo entry
            # below, so a second identical dangerous call asks again too.
            sensitive = requires_explicit_workspace_approval(call, ctx)
            forced = sensitive
            reprompt_failed = False
            if registration.always_reprompt is not None:
                try:
                    forced = bool(registration.always_reprompt(call)) or sensitive
                except Exception:
                    # A predicate that blows up must fail CLOSED (ask), never
                    # silently reuse a grant it was supposed to veto.
                    forced = True
                    reprompt_failed = True
            if forced:
                fingerprint = None
            # A session grant is a tool-name-wide convenience for eligible
            # 'once' registrations only. 'always' retains only its exact-call
            # fingerprint memo; forced approval defeats both kinds of memo.
            session_granted = (
                not forced
                and self.allows_session_grant(call.name)
                and ctx.session_grants is not None
                and call.name in ctx.session_grants
            )
            already_approved = session_granted or (
                fingerprint is not None and fingerprint in ctx._approved_fingerprints
            )
            if call.name == "shell.exec" and ctx.shell_allow_rules is not None:
                # The real harness never accepts a whole-tool grant or a
                # fingerprint for shell commands. Only explicit prefixes for
                # *every* segment, with no dangerous or ambiguous segment,
                # can bypass this command's fresh consent requirement. The
                # command and workspace/profile must match the rule exactly.
                from backend.harness.shell_policy import shell_command_allowed

                try:
                    already_approved = not sensitive and not reprompt_failed and shell_command_allowed(
                        str(call.arguments.get("command") or ""),
                        ctx.shell_allow_rules,
                        shell=getattr(ctx, "harness_shell", "auto"),
                        workspace_root=getattr(ctx, "harness_workspace_dir", None),
                        profile=getattr(ctx, "harness_execution_profile", "host"),
                    )
                except Exception:
                    already_approved = False
            # A saved safe shell prefix must not skip a fresh Isolated network disclosure.
            if (call.name == "shell.exec" and getattr(ctx, "harness_execution_profile", "host") == "isolated"
                    and call.arguments.get("network") is True
                    and not (getattr(ctx, "harness_network_always", False)
                             or getattr(ctx, "harness_network_task_approved", False)
                             or call.id in getattr(ctx, "harness_network_once_approved", set()))):
                already_approved = False
            if not already_approved:
                # §2.4: the decision is now GRADED, not binary. False/None
                # denies; the string "session" approves AND remembers for the
                # session; anything else truthy approves this call only. Every
                # pre-existing caller returns a plain bool, which lands on the
                # once-only branch exactly as it always did.
                disclosure_call = deepcopy(call) if (
                    original_python_call is not None or original_shell_call is not None
                    or original_file_call is not None or original_session_call is not None
                ) else call
                approval_started = time.monotonic()
                try:
                    decision = await ctx.request_approval(disclosure_call)
                finally:
                    ctx.approval_wait_seconds[call.id] = (
                        ctx.approval_wait_seconds.get(call.id, 0.0)
                        + time.monotonic() - approval_started
                    )
                approved = bool(decision)
                # The approval prompt is the one place this function can be
                # awaiting for a genuinely long time (a human deciding) -
                # re-check cancellation on return, same cooperative-
                # checkpoint posture cancel_code_sandbox already documents
                # (backend/agents.py): not a true
                # mid-await interrupt, but the run must not silently execute
                # a handler after the user cancelled while this was pending.
                _raise_if_cancelled(ctx.cancel.event if ctx.cancel is not None else None)
                if not approved:
                    return ToolResult(content=f"Tool call {call.name!r} was denied approval.", is_error=True)
                original_approved_call = (
                    original_python_call if original_python_call is not None
                    else original_shell_call if original_shell_call is not None
                    else original_file_call if original_file_call is not None
                    else original_session_call
                )
                if original_approved_call is not None and (
                    original_approved_call.arguments != call.arguments
                    or disclosure_call.arguments != call.arguments
                ):
                    if original_file_call is not None:
                        file_states = getattr(ctx, "harness_file_approval_states", None)
                        if isinstance(file_states, dict):
                            file_states.pop(call.id, None)
                    if original_shell_call is not None:
                        network_once = getattr(ctx, "harness_network_once_approved", None)
                        if network_once is not None:
                            network_once.discard(call.id)
                        if decision == "network-task":
                            setattr(ctx, "harness_network_task_approved", False)
                    return ToolResult(
                        content="Execution request changed while approval was pending; submit it for fresh approval.",
                        is_error=True,
                    )
                if (decision == "session" and self.allows_session_grant(call.name)
                        and ctx.session_grants is not None and not forced):
                    ctx.session_grants.add(call.name)
                elif fingerprint is not None:
                    ctx._approved_fingerprints.add(fingerprint)

        try:
            # The run's workspace is an identity-bound capability, not a
            # mutable pathname. Check after approval (which can take minutes)
            # and before host shell/Python execution, then again before
            # returning data from a worker that may have run during a swap.
            bound = getattr(ctx, "harness_workspace_dir", None)
            verify = getattr(bound, "verify_bound_identity", None)
            if callable(verify):
                verify()
            result = await registration.handler(call, ctx)
            if callable(verify):
                verify()
                if getattr(ctx, "harness_workspace_dir", None) is not bound:
                    raise ValueError("Workspace binding changed during this tool call.")
            return result
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
