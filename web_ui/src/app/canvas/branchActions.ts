import type { ReactFlowInstance } from "@xyflow/react";
import type { SceneStore } from "./sceneStore";

/**
 * ADR-002 Workstream 1's two branch actions - Compare Branches and
 * Synthesize Branches - extracted here at ADR-021 stage 21.5.
 *
 * They shipped reachable ONLY by keyboard shortcut (Ctrl+Shift+C /
 * Ctrl+Shift+S), absent from the command palette and every menu, which made
 * two real agent surfaces effectively undiscoverable. Stage 21.5 registers
 * them in the palette too - and since applySynthesizeBranches carries
 * non-obvious, deliberately duplicated validation (see its own comment), the
 * two surfaces share ONE implementation rather than a copy each: a guard
 * that drifted between the shortcut and the palette would be worse than no
 * palette entry at all.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Flow = ReactFlowInstance<any, any>;

export function selectedNodeIdsFor(rf: Flow): string[] {
  return rf.getNodes().filter((n) => n.selected).map((n) => n.id);
}

/**
 * Forwards even a single selected id rather than bare-returning on anything
 * short of the real minimum - compare_branches's own backend validation
 * shows an informative notification ("Select at least 2 branches to
 * compare") for that near-miss case, which is more helpful than legacy's
 * silent "nothing selected" convention when the user very clearly attempted
 * a real action. Bare-returning is still correct for the genuine
 * zero-selected case: nothing to give feedback about there.
 */
export function applyCompareBranches(store: SceneStore, rf: Flow): void {
  const ids = selectedNodeIdsFor(rf);
  if (ids.length === 0) return;
  store.compareBranches(ids);
}

/**
 * STAGES the selection rather than firing an intent immediately (unlike
 * applyCompareBranches above) - synthesis needs the user's own free-text
 * instructions first, gathered by the Composer's very next Send (see
 * sceneStore.setSynthesizeTargetNodeIds's own comment).
 *
 * UNLIKE applyCompareBranches, this DOES duplicate synthesize_branches's own
 * "2+ ids, every one a real chat node" backend validation client-side rather
 * than deferring to it - a deliberate divergence from the app's usual "let
 * the backend validate" posture. The reason: an invalid selection here
 * doesn't just fail an immediate, nothing-lost action - it stages a pending
 * synthesis that the user then types real, possibly substantial instructions
 * against. Without this check, pressing Send on that staged-but-invalid
 * selection fires synthesizeBranches, and sceneStore.sendMessage /
 * Composer's send() both optimistically clear the staged selection and the
 * draft text immediately (the same fire-and-forget posture every WS intent
 * uses) - so by the time the backend's rejection notification arrives, the
 * user's typed instructions are already gone with no recovery. Catching the
 * same cases HERE, before any instructions get typed, closes that hole. The
 * backend's own validation stays exactly as-is (defense in depth for any
 * other caller); this is additive.
 */
export function applySynthesizeBranches(store: SceneStore, rf: Flow): void {
  const selected = rf.getNodes().filter((n) => n.selected);
  if (selected.length === 0) return; // legacy-style bare return: nothing attempted, nothing to report
  const allChat = selected.every((n) => n.type === "chat");
  const ids = selected.map((n) => n.id);
  if (!allChat) {
    store.showInfoNotification("Every selected node must be a real chat message to synthesize.");
    return;
  }
  if (ids.length < 2) {
    store.showInfoNotification("Select at least 2 branches to synthesize.");
    return;
  }
  store.setSynthesizeTargetNodeIds(ids);
}
