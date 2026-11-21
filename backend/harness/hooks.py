"""Opt-in, per-Agent-workspace command hooks.

Saved graphs and WebSocket inputs are not authorization. Configuration is
validated here; execution is performed only by the normal shell.exec registry
handler, with a fresh Ask-mode approval and a bounded process-tree timeout.
"""

from __future__ import annotations

PHASES = frozenset({"before_tool", "after_tool", "end_run"})
MAX_HOOKS = 8
MAX_COMMAND_CHARS = 4_000
HOOK_TIMEOUT_SECONDS = 10
HOOK_OUTPUT_CHARS = 2_000


def validated_hooks(value: object, *, strict: bool = False) -> list[dict]:
    """Return a canonical, independent snapshot or fail closed.

    When reading a saved/imported session, discard an invalid configuration
    in its entirety; the editable API reports the validation error instead.
    """
    def invalid(message: str) -> list[dict]:
        if strict:
            raise ValueError(message)
        return []

    if not isinstance(value, list) or len(value) > MAX_HOOKS:
        return invalid(f"Hooks must be a list of at most {MAX_HOOKS} entries.")
    hooks: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"phase", "command", "enabled"}:
            return invalid("Each hook needs phase, command and enabled.")
        phase, command, enabled = entry["phase"], entry["command"], entry["enabled"]
        if not isinstance(phase, str) or phase not in PHASES:
            return invalid("Unknown hook phase.")
        if (not isinstance(command, str) or not command.strip()
                or len(command) > MAX_COMMAND_CHARS or "\x00" in command):
            return invalid(f"Hook command must be 1–{MAX_COMMAND_CHARS} characters with no NUL.")
        if type(enabled) is not bool:
            return invalid("Hook enabled must be a boolean.")
        hooks.append({"phase": phase, "command": command.strip(), "enabled": enabled})
    return hooks
