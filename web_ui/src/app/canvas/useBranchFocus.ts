import { useCallback, useState } from "react";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

/**
 * R8a extraction: "Hide Other Branches" state + toggle, pulled out of
 * CanvasInner as its own hook - matching this directory's small-shared-
 * hook convention (see useLodVisibility.ts/useStreamBuffer.ts). Zero
 * behavior change: same state, same derivation, same callback.
 *
 * Local-only state (never scene state), same posture as
 * documentViewContent in CanvasInner - it is a pure display concern, not
 * something that needs to survive a reload or be shared with a
 * hypothetical second viewer.
 *
 * `effectiveBranchFocusOriginId` is derived, not stored: raw state alone
 * can go stale the moment its origin node is deleted while focus is
 * active, and "is this state still meaningful" is exactly the kind of
 * derivation React's own guidance says to compute during render rather
 * than reconcile via a setState-in-effect (the first version of this
 * self-heal DID use such an effect, and the react-hooks/set-state-in-
 * effect rule correctly flagged it as the cascading-render anti-pattern it
 * is - this replaces it, not suppresses it). Every consumer reads this
 * derived value, never the raw state directly, so a deleted origin
 * self-heals within the SAME render instead of flashing "Show All
 * Branches" for one extra frame first.
 *
 * `nodes` is passed in (rather than the whole scene) since that is the
 * only piece of scene state either the validity check or the toggle
 * itself reads.
 */
export function useBranchFocus(nodes: SceneNodeRow[]): {
  effectiveBranchFocusOriginId: string | null;
  onToggleBranchFocus: (nodeId: string) => void;
} {
  const [branchFocusOriginId, setBranchFocusOriginId] = useState<string | null>(null);
  const isBranchFocusOriginValid = branchFocusOriginId !== null && nodes.some((n) => n.id === branchFocusOriginId);
  const effectiveBranchFocusOriginId = isBranchFocusOriginValid ? branchFocusOriginId : null;
  const onToggleBranchFocus = useCallback(
    (nodeId: string) => {
      // Mirrors graphlink_scene.py's own toggle_branch_visibility exactly:
      // if focus is already active (from ANY origin), any click anywhere
      // clears it - the menu label is "Show All Branches" scene-wide once
      // active, not "focus a different branch". Only when focus is OFF -
      // including "was on, but its origin has since been deleted", which
      // reads as off per isBranchFocusOriginValid above - does the clicked
      // node become the new origin.
      setBranchFocusOriginId((current) => {
        const currentIsValid = current !== null && nodes.some((n) => n.id === current);
        return currentIsValid ? null : nodeId;
      });
    },
    [nodes],
  );

  return { effectiveBranchFocusOriginId, onToggleBranchFocus };
}
