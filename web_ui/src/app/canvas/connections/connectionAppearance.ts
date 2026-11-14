import type { SceneEdgeRow } from "../../../lib/bridge-core/generated/scene-state";

export type ConnectionRole = SceneEdgeRow["role"] | "text-input" | "table-input";

// Lengths are in screen pixels. Legacy and primary edges form the solid
// branch structure. Cross-links keep a distinct texture in either theme.
export function connectionDashes(role: ConnectionRole, zoom: number): number[] {
  switch (role) {
    case "table-input": return [10 / zoom, 3 / zoom];
    case "text-input": return [7 / zoom, 3 / zoom, 1 / zoom, 3 / zoom];
    case "secondary": return [8 / zoom, 4 / zoom];
    case "reference": return [4 / zoom, 4 / zoom];
    case "association": return [1 / zoom, 4 / zoom];
    default: return [];
  }
}
