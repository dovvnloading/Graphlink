/**
 * The help-dialog island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). This island has zero Python-side
 * content fields - the envelope alone is the whole payload; see
 * data/sections.ts for the actual (static, client-side-only) content.
 */
export type { HelpState } from "../../lib/bridge-core/generated/help-state";

import type { HelpState } from "../../lib/bridge-core/generated/help-state";

export const initialHelpState: HelpState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
};
