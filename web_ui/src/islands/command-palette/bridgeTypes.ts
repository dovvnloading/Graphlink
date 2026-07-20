/**
 * The command-palette island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). What legitimately still lives here:
 * initialCommandPaletteState, the mock snapshot used for browser-preview/dev
 * and by jsdom tests.
 */
export type { CommandPaletteState, CommandEntry } from "../../lib/bridge-core/generated/command-palette-state";

import type { CommandPaletteState } from "../../lib/bridge-core/generated/command-palette-state";

export const initialCommandPaletteState: CommandPaletteState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  visible: false,
  commands: [],
  notice: null,
};
