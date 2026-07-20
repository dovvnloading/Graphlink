"""Per-surface legacy/web renderer selection (migration plan section 3.6).

A surface mid-migration to a web island needs to keep its legacy Qt
implementation reachable while the web version is built out incrementally,
without exposing an incomplete web UI to real usage in between. Precedence,
highest first: an explicit GRAPHLINK_<SURFACE>_RENDERER environment
variable, then a persisted settings override (so support can toggle a
surface without shell access), then the caller's own default. An
unrecognized value at any tier is treated as absent and falls through to
the next tier rather than raising - a typo'd env var must not break
startup, it should just be ignored.

CURRENTLY NO PRODUCTION CONSUMER - and that is deliberate, not dead code:
settings (this helper's first real consumer, Phase 3) finished its
migration and deleted its flag in increment 10, exactly as this module's
own lifecycle prescribes. The helper stays because it IS the section-3.6
mechanism itself: the next surface swap (Phase 4's content dialogs onward)
gates its construction through this same function rather than re-inventing
it. If a future dead-code sweep lands here, the correct question is "is any
surface currently mid-migration," not "does anything import this today."
"""

import os

VALID_RENDERERS = ("legacy", "web")


def resolve_renderer_flag(surface: str, default: str, settings_override: str | None = None) -> str:
    """Resolve which renderer (``"legacy"`` or ``"web"``) a surface should use.

    ``surface`` names the env var read as ``GRAPHLINK_<SURFACE>_RENDERER``
    (e.g. ``"settings"`` -> ``GRAPHLINK_SETTINGS_RENDERER``). ``default`` is
    returned when neither the env var nor ``settings_override`` supplies a
    recognized value, and must itself be a recognized value.
    """
    if default not in VALID_RENDERERS:
        raise ValueError(f"resolve_renderer_flag: default must be one of {VALID_RENDERERS}, got {default!r}")

    env_var = f"GRAPHLINK_{surface.upper()}_RENDERER"
    env_value = os.environ.get(env_var, "").strip().lower()
    if env_value in VALID_RENDERERS:
        return env_value

    if settings_override:
        normalized_override = settings_override.strip().lower()
        if normalized_override in VALID_RENDERERS:
            return normalized_override

    return default
