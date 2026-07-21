/**
 * The toolbar island's state contract.
 *
 * See composer/bridgeTypes.ts for the fuller rationale (re-export from the
 * generated file, not a hand mirror). `pinsChecked` is server-authoritative
 * (the pin overlay can close via paths other than this button, e.g. the
 * panel's own Close button); `controlsChecked` is deliberately NOT part of
 * this contract at all - it stays pure client-side React state, matching
 * the legacy button's own "nothing else reads or writes it back" behavior
 * confirmed by recon.
 */
export type { ToolbarState } from "../../lib/bridge-core/generated/toolbar-state";

import type { ToolbarState } from "../../lib/bridge-core/generated/toolbar-state";

export const initialToolbarState: ToolbarState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  pinsChecked: false,
  modeOptions: [],
  currentMode: "",
};
