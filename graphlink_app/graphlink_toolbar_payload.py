"""The toolbar island's outbound wire contract (Phase 6 increment 1).

Absorbs graphlink_window.py's native QToolBar/setup_toolbar() - 14 intents
(Library, Save, Pins-toggle, Organize, Zoom In/Out, Reset, Fit All,
Controls-toggle, Plugins-open, Mode-switch, Settings/About/Help-open).

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. `modeOptions` is just the 3 mode label strings
(`config.MODE_OLLAMA_LOCAL`/`MODE_LLAMACPP_LOCAL`/`MODE_API_ENDPOINT`) - the
legacy QComboBox's own per-item `userData` (`config.LOCAL_PROVIDER_OLLAMA`
etc.) was never actually read anywhere (`on_mode_changed` only ever calls
`itemText`, never `currentData`), so there is no separate value/label
distinction to carry over. `pinsChecked` is server-authoritative (not
client-tracked) because the legacy button's checked state was already
externally forced to unchecked by `_handle_pin_overlay_closed` whenever the
panel closed via a path other than the button itself - `controlsChecked` has
no such external-force path (confirmed by recon: stored only as a local
variable, nothing else in the repo reads or writes it back), so it is
deliberately NOT part of this payload at all - it stays pure client-side
React state, mirroring every prior island's own "no server round-trip for
state nothing else needs to know" precedent.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolbarStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    pinsChecked: bool
    modeOptions: list[str]
    currentMode: str
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
