import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

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
  fal_media: "tool",
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
