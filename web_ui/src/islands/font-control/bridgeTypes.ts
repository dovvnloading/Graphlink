/**
 * The font-control island's state contract.
 *
 * Carries only static configuration (`fontFamilies`/`colorPresets`/
 * `sizeMin`/`sizeMax`) - font state has always lived entirely on
 * ChatScene, never on this control, so there is no "current value" to
 * round-trip here (the legacy widget never read it back either). See
 * graphlink_font_control_payload.py's own docstring for the full rationale.
 */
export type { FontControlState } from "../../lib/bridge-core/generated/font-control-state";

import type { FontControlState } from "../../lib/bridge-core/generated/font-control-state";

export const initialFontControlState: FontControlState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  fontFamilies: ["Segoe UI"],
  colorPresets: ["#F0F0F0", "#C7C7C7", "#949494", "#818181"],
  sizeMin: 8,
  sizeMax: 16,
};
