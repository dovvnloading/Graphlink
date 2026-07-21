"""Shared z-order/reposition-timing arbiter for Phase 5's overlay hosts.

Every surface migrated in Phase 5 (search bar, pin panel, composer pickers,
token counter, notification banner) keeps its OWN WebIslandHost, exactly like
every island built so far - the design-panel decision recorded in the master
plan rejected a single shared multi-bridge host specifically because it would
require validating an unvalidated QWebChannel N>2-object registration
pattern instead of reusing the one every island already proves 8 times over.
What Phase 5 actually needed to dissolve was scattered `raise_()` ordering and
duplicate positioning logic (`ChatWindow._update_overlay_positions` and
`ChatView._update_overlay_positions` both moved the same `SearchOverlay`,
coordinated only by call order and a code comment) - this class is the single
arbiter for that, nothing else.

Deliberately NOT a widget. It holds no content, does no painting, and every
registered host keeps its own click behavior exactly as every other island
already has it: a small, precisely-bounded, corner/anchor-positioned QFrame
with native rounded-corner masking (`WebIslandHost._apply_native_mask`),
exactly like About/Help/Settings already are. None of Phase 5's real surfaces
are full-viewport, so the Phase 1 spike's binary `WA_TransparentForMouseEvents`
pass-through toggle does not apply here and is not built - an early design
pass proposed giving this class a viewport-sized widget shape mirroring the
spike's own throwaway overlay, but that would serve no purpose without
content or hit-testing of its own, so it was corrected away before
implementation.
"""

from __future__ import annotations


class OverlayCoordinator:
    def __init__(self):
        self._entries = []  # list of (host, reposition_fn, z_priority), sorted ascending

    def register(self, host, reposition_fn, z_priority: int = 0) -> None:
        self._entries.append((host, reposition_fn, z_priority))
        self._entries.sort(key=lambda entry: entry[2])

    def unregister(self, host) -> None:
        self._entries = [entry for entry in self._entries if entry[0] is not host]

    def reposition_all(self) -> None:
        """Position every visible registered host, then raise them in
        ascending z-priority order so the highest-priority host ends up on
        top - the single call site that replaces the scattered `raise_()`
        calls this phase's recon found."""
        for host, reposition_fn, _priority in self._entries:
            if host.isVisible():
                reposition_fn()
        for host, _reposition_fn, _priority in self._entries:
            if host.isVisible():
                host.raise_()
