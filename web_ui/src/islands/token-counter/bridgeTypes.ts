/**
 * The token-counter island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). What legitimately still lives here:
 * initialTokenCounterState, the mock snapshot used for browser-preview/dev
 * and by jsdom tests.
 */
export type { TokenCounterState } from "../../lib/bridge-core/generated/token-counter-state";

import type { TokenCounterState } from "../../lib/bridge-core/generated/token-counter-state";

export const initialTokenCounterState: TokenCounterState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  inputTokens: 0,
  outputTokens: 0,
  contextTokens: 0,
  totalTokens: 0,
};
