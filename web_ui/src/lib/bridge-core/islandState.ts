/**
 * Why a payload was rejected, for a visible error state. `null` means the
 * last payload was fine.
 *
 * ADR-003 stage 3.5: this shape originally belonged to this module's own
 * parseIslandState() (the pre-consolidation islands architecture's generic
 * parse/reject shell) - that function was retired as dead (built, unit-
 * tested, never wired to anything live; see lib/ws/transport.ts's own
 * comment), but the shape it defined was real and still needed: transport.ts
 * reused it verbatim for the WebSocket schema-version rejection it actually
 * ships (onVersionRejection()), and lib/ui/BridgeErrorState.tsx renders it.
 * Kept here rather than moved, so neither of those two real call sites had
 * to change its import path.
 */
export interface BridgeRejection {
  kind: "version" | "shape" | "parse";
  reason: string;
  details: string[];
}
