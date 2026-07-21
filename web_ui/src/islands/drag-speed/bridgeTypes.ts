/**
 * The drag-speed island's state contract.
 *
 * Carries only static configuration (`percentPresets`/`percentMin`/
 * `percentMax`) - drag speed has always been a pure fire-and-forget
 * control, matching font-control's own precedent: the legacy slider never
 * read a live value back either (it always started at a hardcoded 100%).
 */
export type { DragSpeedState } from "../../lib/bridge-core/generated/drag-speed-state";

import type { DragSpeedState } from "../../lib/bridge-core/generated/drag-speed-state";

export const initialDragSpeedState: DragSpeedState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  percentPresets: [25, 50, 75, 100],
  percentMin: 10,
  percentMax: 100,
};
