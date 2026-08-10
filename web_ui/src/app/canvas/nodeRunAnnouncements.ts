import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

/**
 * ADR-012 stage 12.3: derives a screen-reader announcement (or null, for "say
 * nothing") from a node's before/after wire rows during a patch application.
 * A pure function, not a class method, so sceneStore.ts's own patch-apply
 * loop can call it inline without this needing store access, and so it can
 * be unit-tested without standing up a store/transport at all.
 *
 * `pendingRequestId` is the ONE field shared across every kind that can be
 * "running" (see SceneNodeRow's own doc) - null/undefined -> truthy is a run
 * starting; truthy -> null/undefined is a run ending. Only pycoder/
 * code_sandbox/gitlink get a kind-specific error check on the ending edge:
 * those three are the sandboxed/external-side-effect kinds where "did it
 * actually work" is the thing a non-visual user most needs confirmed (a
 * failed chat reply already gets its own screen-reader-visible retry
 * affordance in the transcript; these three don't have an equivalent without
 * this). `builderStatus` (the Plan node) is a second, independent field this
 * also watches, since Plan runs don't use pendingRequestId at all.
 */
export function describeNodeRunTransition(prev: SceneNodeRow | undefined, next: SceneNodeRow): string | null {
  if (!prev) return null; // a brand-new node, not a running->done transition

  const wasPending = !!prev.pendingRequestId;
  const isPending = !!next.pendingRequestId;
  if (wasPending !== isPending) {
    const label = KIND_LABELS[next.kind] ?? next.kind;
    if (isPending) return `${label} started`;
    if (next.kind === "pycoder" && (next.pycoderError || next.pycoderLastRunFailed)) return `${label} failed`;
    if (next.kind === "code_sandbox" && next.codeSandboxError) return `${label} failed`;
    if (next.kind === "gitlink" && next.gitlinkError) return `${label} failed`;
    return `${label} completed`;
  }

  if (prev.builderStatus !== next.builderStatus) {
    if (next.builderStatus === "running") return "Build running";
    if (next.builderStatus === "done") return "Build complete";
    if (next.builderStatus === "failed") return "Build failed";
  }

  return null;
}

const KIND_LABELS: Record<string, string> = {
  pycoder: "Python run",
  code_sandbox: "Code sandbox run",
  gitlink: "Git operation",
  chat: "Chat response",
  artifact: "Artifact generation",
  web_research: "Web research",
  conversation: "Conversation reply",
};
