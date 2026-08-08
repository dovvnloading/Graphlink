import { useStore } from "@xyflow/react";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/** ADR-011 stage 11.6 dedup: the "collapse node body below this zoom" check
 * (level-of-detail, the R1 seed of the Qt canvas's LOD thresholds) was
 * independently re-implemented at every one of the ~14 call sites - each its
 * own `useStore((s) => s.transform[2])` read plus its own
 * `zoom < LOD_ZOOM_THRESHOLD` comparison. This hook is a pure extraction of
 * that exact pair with zero behavior change: same store selector, same
 * threshold, same `<` comparison, returning the plain boolean every call site
 * already computed for itself (some name it `collapsed`, some `lodCollapsed`
 * and then OR it with a node-local collapse flag - either is unaffected,
 * since this only replaces the two lines that produced the boolean, not what
 * callers do with it afterward). */
export function useLodVisibility(): boolean {
  const zoom = useStore((s) => s.transform[2]);
  return zoom < LOD_ZOOM_THRESHOLD;
}
