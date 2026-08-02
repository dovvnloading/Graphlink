"""THE domain-purity gate (ADR-002 stage 2.2). Permanent.

backend/domain/ holds the pure scene domain: data model, invariants, and
content codec. Its whole value as a boundary is that it can be understood,
tested, and eventually reused (ADR-003 deltas, ADR-009 persistence, ADR-010
undo) without dragging in backend infrastructure - so nothing under it may
import FastAPI, the event bus, notifications, agents, native dialogs, the
composer, or api_provider, and nothing in it may call bus-shaped methods
(publish/register_topic/register_intent/dispatch_intent).

AST-based, not regex: canvas-heritage code is dense with comments and
docstrings that MENTION publish/AgentDispatcher/bus - prose must never
false-positive this gate. Same permanent-gate philosophy as
tests/test_no_qt_anywhere.py: this is what makes "the domain layer is pure"
a machine-checked fact instead of a claim.

backend.token_counter is on the forbidden list deliberately even though its
estimate_tokens is itself pure: that module imports backend.events at
module level, so a domain import of it would transitively couple the domain
layer to the bus. Use graphlink_token_estimator directly instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "backend" / "domain"

FORBIDDEN_MODULES = (
    "fastapi",
    "uvicorn",
    "backend.app",
    "backend.events",
    "backend.notifications",
    "backend.agents",
    "backend.native_dialogs",
    "backend.composer",
    "backend.token_counter",
    "backend.session_context",
    "backend.assets",
    "backend.settings",
    "backend.chat_library",
    "backend.autosave",
    "api_provider",
)

FORBIDDEN_CALL_ATTRS = {"publish", "dispatch_intent", "register_topic", "register_intent"}


def _domain_trees():
    paths = sorted(DOMAIN_DIR.rglob("*.py"))
    assert paths, f"no python files found under {DOMAIN_DIR} - did the package move?"
    for path in paths:
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for forbidden in FORBIDDEN_MODULES
    )


def test_domain_imports_no_backend_infrastructure():
    offenders = []
    for path, tree in _domain_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports (level > 0) are within-domain and allowed.
                names = [] if node.level > 0 else [node.module or ""]
            else:
                continue
            offenders.extend(
                f"{path.name}:{node.lineno} imports {name}"
                for name in names
                if _is_forbidden(name)
            )
    assert not offenders, "domain purity violated:\n" + "\n".join(offenders)


def test_domain_never_calls_bus_shaped_methods():
    offenders = [
        f"{path.name}:{node.lineno} calls .{node.func.attr}(...)"
        for path, tree in _domain_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in FORBIDDEN_CALL_ATTRS
    ]
    assert not offenders, "domain purity violated:\n" + "\n".join(offenders)
