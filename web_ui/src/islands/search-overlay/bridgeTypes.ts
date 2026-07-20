/**
 * The search-overlay island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). The query text itself is NOT part of
 * this state - it's pure client-side React state (an uncontrolled input),
 * see App.tsx.
 */
export type { SearchOverlayState } from "../../lib/bridge-core/generated/search-overlay-state";

import type { SearchOverlayState } from "../../lib/bridge-core/generated/search-overlay-state";

export const initialSearchOverlayState: SearchOverlayState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  currentIndex: -1,
  totalMatches: 0,
};
