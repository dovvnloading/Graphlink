"""ADR-014 stage 14.5 reference plugin - proves [runtime] isolation =
"out-of-process" end to end. This module's own code NEVER runs inside the
host process - only inside the worker subprocess backend/plugin_worker.py
spawns (`python -m backend.plugin_worker sandboxed_demo <this-dir>`). See
that module's own docstring for the isolation property this buys, and
backend/plugin_sdk.py's _discover_out_of_process_plugin for how the host
side reconstructs this plugin's registrations without ever importing this
file.

_make_env_probe is deliberately introspective FOR TEST OBSERVABILITY ONLY -
a real third-party plugin has no legitimate reason to read the FULL content
of every visible environment variable and report it back to the host; this
one does so on purpose, so backend/tests/test_plugin_worker.py can plant a
fake secret-shaped env var on the HOST process (e.g.
GRAPHLINK_OPENAI_API_KEY), trigger this plugin's node creation, and assert
the created node's content - which crossed the RPC boundary from the
WORKER's own os.environ, built by the host via graphlink_process_env.
safe_subprocess_env()'s ALLOWLIST at Popen time, never inherited wholesale
from the host - does NOT contain that secret's name or value. This is the
real, empirical proof of the "a third-party plugin cannot read secrets"
exit criterion, not an assertion about code structure alone."""

from __future__ import annotations

import os
from dataclasses import dataclass

from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginRunContext


def _make_env_probe(document, run_ctx: PluginRunContext, parent_id: str) -> PluginNodeSeed:
    parent = document.nodes[parent_id]
    visible = sorted(f"{name}={value}" for name, value in os.environ.items())
    return PluginNodeSeed(
        title="Sandboxed Env Probe",
        content=(
            f"Ran out-of-process, branched from '{parent.title}'. "
            f"Visible environment in this worker process: {' | '.join(visible)}"
        ),
    )


@dataclass
class PingArgs:
    """ADR-021 stage 21.4's reference argument schema - a plain dataclass,
    described and validated by the same graphlink_wire_schema machinery every
    wire payload uses. Declared HERE, inside the sandboxed plugin, and never
    imported by the host: the worker generates its JSON Schema and sends that
    up at registration time, so the host can describe this action's real
    parameters to a model without ever importing this module."""

    message: str
    # Optional in the generated schema too, not merely defaulted:
    # graphlink_wire_schema marks a field required unless its annotation is
    # Optional, independent of whether it has a Python default.
    times: int | None = None


def _ping(document, run_ctx: PluginRunContext, args: PingArgs):
    echoed = " ".join([args.message] * max(1, args.times or 1))
    return f"pong from {run_ctx.plugin_id}: {echoed}"


def register(host: HostContext) -> None:
    host.register_node_kind("env_probe", _make_env_probe, requires_parent=True)
    host.register_picker_entry(
        node_kind="env_probe",
        name="Sandboxed Env Probe",
        description=(
            "Runs out-of-process and reports what it can see in its own "
            "environment - proves the ADR-014 stage 14.5 secret-containment "
            "boundary."
        ),
        category="More Plugins",
    )
    host.register_intent("ping", _ping, args_schema=PingArgs)
