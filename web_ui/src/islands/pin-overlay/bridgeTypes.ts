/**
 * The pin-overlay island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). Filtering is pure client-side
 * (matching ChatLibraryDialog's own precedent) - Python always sends the
 * full row list.
 */
export type { PinOverlayState, PinRow } from "../../lib/bridge-core/generated/pin-overlay-state";

import type { PinOverlayState } from "../../lib/bridge-core/generated/pin-overlay-state";

export const initialPinOverlayState: PinOverlayState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  rows: [],
  selectedPinId: null,
};
