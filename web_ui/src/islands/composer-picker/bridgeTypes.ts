/**
 * The composer-picker island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). Filtering is pure client-side
 * (matching every prior list-bearing island's own precedent) - Python
 * always sends the full option list. `openToken` bumps once per open() call
 * so App.tsx can reset its local search query on a genuinely fresh open,
 * distinct from any other republish.
 */
export type { ComposerPickerState, ComposerPickerOption } from "../../lib/bridge-core/generated/composer-picker-state";

import type { ComposerPickerState } from "../../lib/bridge-core/generated/composer-picker-state";

export const initialComposerPickerState: ComposerPickerState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  kind: "model",
  title: "Choose a model",
  options: [],
  openToken: 0,
};
