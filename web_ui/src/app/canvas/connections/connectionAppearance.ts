import type { SceneEdgeRow } from "../../../lib/bridge-core/generated/scene-state";

export type ConnectionRole = SceneEdgeRow["role"];

// Lengths are in screen pixels. Legacy and primary edges form the solid
// branch structure. Cross-links keep a distinct texture in either theme.
export function connectionDashes(role: ConnectionRole, zoom: number): number[] {
  switch (role) {
    case "secondary": return [8 / zoom, 4 / zoom];
    case "reference": return [4 / zoom, 4 / zoom];
    case "association": return [1 / zoom, 4 / zoom];
    default: return [];
  }
}
