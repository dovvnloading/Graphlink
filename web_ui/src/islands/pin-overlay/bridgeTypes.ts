/**
 * The pin-overlay island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). Filtering is pure client-side
 * (matching ChatLibraryDialog's own precedent) - Python always sends the
 * full row list. `draft`/`error` are Phase 5 increment 2's async draft-edit
 * fields - see App.tsx for how they drive the in-panel editor view.
 */
export type { PinOverlayState, PinRow, PinDraft } from "../../lib/bridge-core/generated/pin-overlay-state";

import type { PinOverlayState } from "../../lib/bridge-core/generated/pin-overlay-state";

export const initialPinOverlayState: PinOverlayState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  rows: [],
  selectedPinId: null,
  draft: null,
  error: null,
};
