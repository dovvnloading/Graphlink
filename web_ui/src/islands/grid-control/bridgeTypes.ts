/**
 * The grid-control island's state contract.
 *
 * `gridSize`/`gridOpacityPercent`/`gridStyle`/`gridColor` reflect the real,
 * live `GridViewSettings` model on the desktop side - unlike the toolbar's
 * `controlsChecked`, this genuinely IS the authoritative state
 * ChatView.drawBackground() reads, so it round-trips for real.
 * `sizePresets`/`stylePresets`/`colorPresets` are Python-owned static
 * configuration (see graphlink_grid_control_bridge.py) - not hardcoded here.
 */
export type { GridControlState } from "../../lib/bridge-core/generated/grid-control-state";

import type { GridControlState } from "../../lib/bridge-core/generated/grid-control-state";

export const initialGridControlState: GridControlState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  gridSize: 10,
  gridOpacityPercent: 30,
  gridStyle: "Dots",
  gridColor: "#555555",
  sizePresets: [10, 20, 50, 100],
  stylePresets: ["Dots", "Lines", "Cross"],
  colorPresets: ["#404040", "#555555", "#3E7BFA", "#4CAF50", "#9C27B0"],
};
