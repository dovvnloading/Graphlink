import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

/**
 * How the minimap classifies a node - the information design of the map,
 * kept out of the component so it can be tested directly.
 *
 * React Flow's MiniMap draws only the nodes in ITS OWN store, which is
 * populated by measurement inside a live <ReactFlow>. Under jsdom nothing
 * measures, so no rect is ever emitted and a DOM-level assertion about the
 * map's contents can only ever be vacuous. The classification is the part
 * with real decisions in it; it lives here and is tested as a function,
 * while the drawing is verified in a browser.
 *
 * Its own module rather than an export from SceneMinimap.tsx: a component
 * file that also exports plain functions trips react-refresh's
 * only-export-components rule (the same reason AppBarIcon.tsx is separate
 * from AppBar.tsx).
 */

/** How a node is drawn on the map. */
export type MinimapCategory = "conversation" | "content" | "tool" | "group";

/** What a node currently needs, in ascending order of urgency. */
export type MinimapState = "idle" | "running" | "failed" | "attention";

export interface MinimapNodeMeta {
  category: MinimapCategory;
  state: MinimapState;
  /** A frame/container/note's own colour, when the user set one. */
  color?: string;
}

/**
 * Fifteen kinds, four treatments.
 *
 * This palette is a deliberate monochrome, so there is no hue to spend on
 * kind, and fifteen distinguishable greys do not exist at minimap scale.
 * Four categories do, and they are the four questions actually asked of a
 * map: where is the conversation, where is the material, where is the thing
 * that is doing work, and where are the boundaries I drew.
 */
const CATEGORY_BY_KIND: Record<string, MinimapCategory> = {
  chat: "conversation",
  conversation: "conversation",
  thinking: "conversation",
  code: "conversation",
  document: "content",
  image: "content",
  chart: "content",
  note: "content",
  artifact: "content",
  html: "content",
  plan: "tool",
  harness: "tool",
  web_research: "tool",
  gitlink: "tool",
  code_review: "tool",
  code_sandbox: "tool",
  frame: "group",
  container: "group",
};

/** Unknown kinds fall back rather than vanishing: a node the map cannot
 *  classify is still a node whose position matters. */
export function minimapCategory(kind: string): MinimapCategory {
  return CATEGORY_BY_KIND[kind] ?? "conversation";
}

/**
 * Run state, derived from the wire row rather than from any per-kind view
 * component - the map has to answer for kinds it knows nothing else about,
 * and a node parked on a human is worth surfacing whichever subsystem parked
 * it.
 *
 * Ordered by urgency, and the order is the point: a build that is both
 * running and waiting on an approval is WAITING. Reporting it as running
 * would hide the only state that needs someone to do something.
 */
export function minimapState(node: SceneNodeRow): MinimapState {
  if (
    node.builderAwaitingToolApproval ||
    node.harnessAwaitingApproval ||
    node.harnessAwaitingQuestion ||
    node.codeSandboxAwaitingApproval
  ) {
    return "attention";
  }
  if (
    node.builderStatus === "failed" ||
    node.harnessStatus === "failed" ||
    node.researchError ||
    node.codeSandboxError ||
    node.gitlinkError ||
    node.codeReviewError
  ) {
    return "failed";
  }
  if (
    node.pendingRequestId ||
    node.builderStatus === "running" ||
    node.builderStatus === "planning" ||
    node.harnessStatus === "running"
  ) {
    return "running";
  }
  return "idle";
}

export function minimapMeta(node: SceneNodeRow): MinimapNodeMeta {
  return {
    category: minimapCategory(node.kind),
    state: minimapState(node),
    color: node.color ?? undefined,
  };
}
