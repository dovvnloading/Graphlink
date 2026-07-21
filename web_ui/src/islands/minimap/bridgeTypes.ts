/**
 * The minimap island's state contract.
 *
 * `nodes` is the full, debounced snapshot of every ChatNode currently in
 * the scene (see graphlink_minimap_bridge.py's own docstring for the
 * debounce rationale) - unlike the legacy MinimapWidget, there is no
 * MAX_VISIBLE_NODES windowing/scroll-pagination here: a plain scrollable
 * list handles arbitrarily many nodes via native browser scrolling, which
 * doesn't carry the per-frame QPainter repaint cost that motivated the
 * original 25-node cap. `id` is a wire-only identifier
 * (`str(id(python_object))`), not a persisted node property - stable only
 * for the lifetime of the node within this running session.
 */
export type { MinimapState, MinimapNodeEntry } from "../../lib/bridge-core/generated/minimap-state";

import type { MinimapState } from "../../lib/bridge-core/generated/minimap-state";

export const initialMinimapState: MinimapState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  nodes: [],
};
