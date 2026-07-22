/**
 * The toolbar island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). `pinsChecked` is server-authoritative
 * (the pin overlay can close via paths other than this button, e.g. the
 * panel's own Close button). `activeSurface` (UI-refactor P1, audit B6) is
 * the server-published name of the surface currently open ("" when none) -
 * every chip renders its active visual from THIS, never from island-local
 * latched click state.
 */
export type { ToolbarState } from "../../lib/bridge-core/generated/toolbar-state";

import type { ToolbarState } from "../../lib/bridge-core/generated/toolbar-state";

export const initialToolbarState: ToolbarState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  pinsChecked: false,
  activeSurface: "",
  modeOptions: [],
  currentMode: "",
};
