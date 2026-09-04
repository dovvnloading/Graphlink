// ADR-019: the node views, code-split.
//
// SceneCanvas.tsx used to import all 17 *NodeView components statically, so
// every one of them landed in the initial chunk whether or not the open
// canvas contained a single node of that kind. That is the whole of the
// remaining gap against ADR-019's 500 KiB (512,000-byte) initial-chunk
// budget: React + React Flow are irreducible, the three dialogs and
// katex/highlight.js were split at ADR-011 stage 11.6, and the node views
// are what was left. Every bundle-ratchet amendment since has said so and
// then raised the ceiling instead, because "the eagerly-rendered node views
// code-split" was work no stage owned.
//
// Each kind is now its own chunk, fetched the first time a node of that kind
// is rendered. A canvas of chat and code nodes never parses the Gitlink,
// Review Lens, harness, chart or web-research views at all.
//
// WHY A SUSPENSE BOUNDARY PER NODE, NOT ONE AROUND THE CANVAS: React Flow
// renders node components itself, deep inside its own tree. A single
// boundary high up would suspend the WHOLE canvas - every node, plus the
// pane, controls and minimap - the first time any one kind loaded, which is
// a far worse flicker than the one it would be preventing. Wrapping each
// node component individually keeps a suspension local to the one card that
// is still loading; everything already resolved stays on screen.
//
// The fallback deliberately renders a card-shaped shell rather than nothing:
// React Flow has already positioned and sized the node by the time its
// component renders, so an empty fallback would collapse the card and let
// edges snap to a zero-size box for a frame or two.

import { lazy, Suspense, type ComponentType } from "react";

/** The shell shown while a kind's chunk is still loading. */
function NodeChunkFallback() {
  return (
    <div className="scene-node scene-node-loading" aria-busy="true">
      <div className="scene-node-title">Loading…</div>
    </div>
  );
}

/**
 * Wrap a lazily-imported node component in its own Suspense boundary.
 *
 * React Flow's nodeTypes map wants a plain component, and `lazy()` returns
 * one that throws a promise - so the boundary has to live between them,
 * here, rather than at any call site.
 */
function withNodeSuspense<P extends object>(Component: ComponentType<P>): ComponentType<P> {
  function LazyNodeView(props: P) {
    return (
      <Suspense fallback={<NodeChunkFallback />}>
        <Component {...props} />
      </Suspense>
    );
  }
  // Keeps React DevTools and any test that queries by displayName readable.
  LazyNodeView.displayName = `LazyNodeView(${Component.displayName || "chunk"})`;
  return LazyNodeView;
}

// Named exports, so each import() is unwrapped to a default for lazy().
export const ArtifactNodeView = withNodeSuspense(
  lazy(() => import("./ArtifactNodeView").then((m) => ({ default: m.ArtifactNodeView }))),
);
export const ChartNodeView = withNodeSuspense(
  lazy(() => import("./ChartNodeView").then((m) => ({ default: m.ChartNodeView }))),
);
export const ChatNodeView = withNodeSuspense(
  lazy(() => import("./ChatNodeView").then((m) => ({ default: m.ChatNodeView }))),
);
export const CodeNodeView = withNodeSuspense(
  lazy(() => import("./CodeNodeView").then((m) => ({ default: m.CodeNodeView }))),
);
export const CodeReviewNodeView = withNodeSuspense(
  lazy(() => import("./CodeReviewNodeView").then((m) => ({ default: m.CodeReviewNodeView }))),
);
export const CodeSandboxNodeView = withNodeSuspense(
  lazy(() => import("./CodeSandboxNodeView").then((m) => ({ default: m.CodeSandboxNodeView }))),
);
export const ConversationNodeView = withNodeSuspense(
  lazy(() => import("./ConversationNodeView").then((m) => ({ default: m.ConversationNodeView }))),
);
export const DocumentNodeView = withNodeSuspense(
  lazy(() => import("./DocumentNodeView").then((m) => ({ default: m.DocumentNodeView }))),
);
export const GitlinkNodeView = withNodeSuspense(
  lazy(() => import("./GitlinkNodeView").then((m) => ({ default: m.GitlinkNodeView }))),
);
export const GroupNodeView = withNodeSuspense(
  lazy(() => import("./GroupNodeView").then((m) => ({ default: m.GroupNodeView }))),
);
export const HarnessNodeView = withNodeSuspense(
  lazy(() => import("./HarnessNodeView").then((m) => ({ default: m.HarnessNodeView }))),
);
export const HtmlNodeView = withNodeSuspense(
  lazy(() => import("./HtmlNodeView").then((m) => ({ default: m.HtmlNodeView }))),
);
export const ImageNodeView = withNodeSuspense(
  lazy(() => import("./ImageNodeView").then((m) => ({ default: m.ImageNodeView }))),
);
export const NoteNodeView = withNodeSuspense(
  lazy(() => import("./NoteNodeView").then((m) => ({ default: m.NoteNodeView }))),
);
export const PlanNodeView = withNodeSuspense(
  lazy(() => import("./PlanNodeView").then((m) => ({ default: m.PlanNodeView }))),
);
export const ThinkingNodeView = withNodeSuspense(
  lazy(() => import("./ThinkingNodeView").then((m) => ({ default: m.ThinkingNodeView }))),
);
export const WebResearchNodeView = withNodeSuspense(
  lazy(() => import("./WebResearchNodeView").then((m) => ({ default: m.WebResearchNodeView }))),
);
