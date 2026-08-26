import {
  Background,
  BackgroundVariant,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ViewportPortal,
  useReactFlow,
  useStoreApi,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { SceneEdgeRow, SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";
import type { StreamListener } from "../../lib/ws/transport";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";
import { ArtifactNodeView, type ArtifactFlowNode } from "./ArtifactNodeView";
import { ChartNodeView, type ChartFlowNode } from "./ChartNodeView";
import { ChatNodeView, type ChatFlowNode } from "./ChatNodeView";
import { CodeNodeView, type CodeFlowNode } from "./CodeNodeView";
import { CodeSandboxNodeView, type CodeSandboxFlowNode } from "./CodeSandboxNodeView";
import { ConversationNodeView, type ConversationFlowNode, type ConversationMessage } from "./ConversationNodeView";
import { DocumentNodeView, type DocumentFlowNode } from "./DocumentNodeView";
import { GitlinkNodeView, type GitlinkFlowNode } from "./GitlinkNodeView";
import { GroupNodeView, type GroupFlowNode } from "./GroupNodeView";
import { HtmlNodeView, type HtmlFlowNode } from "./HtmlNodeView";
import { ImageNodeView, type ImageFlowNode } from "./ImageNodeView";
import { NoteNodeView, type NoteFlowNode } from "./NoteNodeView";
import { OrthogonalEdge } from "./OrthogonalEdge";
import { ThinkingNodeView, type ThinkingFlowNode } from "./ThinkingNodeView";
import { WebResearchNodeView, type WebResearchFlowNode } from "./WebResearchNodeView";
import { PlanNodeView, type PlanFlowNode, type PlanStepData } from "./PlanNodeView";
import { HarnessNodeView, type HarnessFlowNode } from "./HarnessNodeView";
import {
  FIT_VIEW_MAX_ZOOM,
  GROUP_FALLBACK_HEIGHT,
  GROUP_FALLBACK_WIDTH,
  VIEWPORT_REPORT_DEBOUNCE_MS,
} from "./canvasConstants";
import { handleKeyboardContextMenu } from "./keyboardContextMenu";
import { SceneStore } from "./sceneStore";
import { computeSmartGuideSnap, type GuideLine, type Rect } from "./smartGuides";
import { ConnectionCanvas, type ConnectionSpec } from "./connections/ConnectionCanvas";
import type { ConnectionPath } from "./connections/connectionGeometry";
import { isPointOnConnection } from "./connections/connectionGeometry";
import { useLodVisibility } from "./useLodVisibility";
import { useCanvasFontVars } from "./useCanvasFontVars";
import { useBranchFocus } from "./useBranchFocus";
import { useViewportReporting } from "./useViewportReporting";
import { useCanvasPan } from "./useCanvasPan";
import { motionDuration } from "../reducedMotion";
import { useNodeDragAndSizeSync } from "./useNodeDragAndSizeSync";

/**
 * The React Flow canvas (Qt-removal plan R1) - the QGraphicsScene/ChatView
 * successor. R1 scope: pan/zoom, model-driven grid (size/style/color/opacity
 * + snap), node drag with the drag-speed factor, edges, selection + delete,
 * minimap, an LOD threshold, navigation pins. R3.1/R3.2 add the first real
 * node type (chat); R3.5/R3.6 add code. Every other kind still renders as a
 * placeholder.
 */

const GRID_VARIANTS: Record<string, BackgroundVariant> = {
  Dots: BackgroundVariant.Dots,
  Lines: BackgroundVariant.Lines,
  Cross: BackgroundVariant.Cross,
};

type PlaceholderNode = Node<{ title: string }, "placeholder">;
export type SceneFlowNode =
  | PlaceholderNode
  | ChatFlowNode
  | CodeFlowNode
  | DocumentFlowNode
  | ThinkingFlowNode
  | HtmlFlowNode
  | ImageFlowNode
  | ConversationFlowNode
  | WebResearchFlowNode
  | ArtifactFlowNode
  | GitlinkFlowNode
  | CodeSandboxFlowNode
  | NoteFlowNode
  | GroupFlowNode
  | ChartFlowNode
  | PlanFlowNode
  | HarnessFlowNode;

function PlaceholderNodeView({ data, selected }: NodeProps<PlaceholderNode>) {
  // ADR-011 stage 11.6/11.1 dedup: was its own local
  // `useStore((s) => s.transform[2]); zoom < LOD_ZOOM_THRESHOLD` pair (one of
  // the 14 duplicated call sites) - now the shared extraction. Zero behavior
  // change, see useLodVisibility.ts's own doc.
  const collapsed = useLodVisibility();
  return (
    <div className={`scene-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}>
      {/* Connection endpoints mirror the Qt canvas's flow: children hang off
          the bottom of a parent (vertical layout), so target on top, source
          on bottom. */}
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title">{data.title}</div>
      {!collapsed && <div className="scene-node-body">Placeholder node</div>}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
    </div>
  );
}

const NODE_TYPES = {
  placeholder: PlaceholderNodeView,
  chat: ChatNodeView,
  code: CodeNodeView,
  document: DocumentNodeView,
  thinking: ThinkingNodeView,
  html: HtmlNodeView,
  image: ImageNodeView,
  conversation: ConversationNodeView,
  web_research: WebResearchNodeView,
  artifact: ArtifactNodeView,
  gitlink: GitlinkNodeView,
  code_sandbox: CodeSandboxNodeView,
  note: NoteNodeView,
  // R6.1: one shared component backs both "frame" and "container" NODE_TYPES
  // entries - see GroupNodeView.tsx's own module doc for why a single
  // data.groupKind-parameterized component was chosen over two near-
  // duplicate files.
  frame: GroupNodeView,
  container: GroupNodeView,
  // R6.2: real chart nodes (bar/line/pie/histogram/sankey) - a backend-
  // rendered PNG card, see ChartNodeView.tsx's own module doc.
  chart: ChartNodeView,
  // ADR-008 stage 8.3: the Builder's plan node - see PlanNodeView.tsx.
  plan: PlanNodeView,
  // PLAN-2026-08-24 H1: the workspace agent - see HarnessNodeView.tsx.
  harness: HarnessNodeView,
};

// R7.5b-2: the first custom edge type registered in this codebase - see
// OrthogonalEdge.tsx's own module doc. Edges not classified as "orthogonal"
// by isOrthogonalEligible below fall through to defaultEdgeOptions's stock
// bezier via an `undefined` type, same as before this feature existed.
const EDGE_TYPES = {
  orthogonal: OrthogonalEdge,
};

// UI-perf fix: fully static <ReactFlow> props, hoisted so these are the
// SAME array/object reference on every CanvasInner render instead of a
// fresh literal every time - React Flow reads these by reference in its
// own internal effects, so a fresh literal every render meant those
// effects re-ran on every unrelated re-render for no behavioral reason.
// defaultNodes is read once when React Flow initialises its store; a stable
// module constant keeps that unambiguous and allocation-free.
const EMPTY_NODES: SceneFlowNode[] = [];
// React Flow renders no connections at all now - ConnectionCanvas draws
// every one of them. Handing it a stable empty array keeps its own edge
// machinery inert rather than merely invisible.
const EMPTY_EDGES: Edge[] = [];
const DELETE_KEY_CODES = ["Delete", "Backspace"];
const PRO_OPTIONS = { hideAttribution: true };
const DEFAULT_EDGE_OPTIONS = { type: "default" as const };

// R8a follow-up: legacy's graphlink_window.py show_document_view() showed
// this exact message (via notification_banner.show_message(..., "info"))
// when a node had nothing to show, rather than silently doing nothing - the
// SPA's first cut of "don't open on nothing" (both onOpenDocumentView guards
// below) dropped that half of the behavior. Restored via SceneStore's
// showInfoNotification, the one frontend-triggerable entry point into the
// shared notification banner.
const NO_DOCUMENT_VIEW_CONTENT_MESSAGE = "No document view content is available for this node yet.";

// R8a: ports the deleted Qt app's own `_history_to_markdown` +
// `_build_document_section("Conversation Transcript", ...)`
// (graphlink_window.py) byte-for-byte - the exact markdown a conversation
// node's "Open Document View" menu item renders. Numbering is 1-based over
// ALL messages (including blank ones that get skipped), since legacy's own
// `enumerate(history, start=1)` numbers BEFORE filtering - a skipped message
// still consumes its number. Returns "" when no message survives (empty
// history, or every message blank) - this is what toFlowNodes' own
// "don't open on nothing" guard below keys off of.
export function conversationHistoryToDocumentMarkdown(history: ConversationMessage[]): string {
  const blocks: string[] = [];
  history.forEach((message, index) => {
    const trimmed = message.content.trim();
    if (!trimmed) return;
    const role = message.role === "user" ? "User" : "Assistant";
    blocks.push(`### ${index + 1}. ${role}\n\n${trimmed}`);
  });
  if (blocks.length === 0) return "";
  return `## Conversation Transcript\n\n${blocks.join("\n\n")}`;
}

// R8a: "Hide Other Branches" - restores graphlink_scene.py's
// toggle_branch_visibility (line 1097). The legacy name is misleading: it
// never removes anything, it DIMS every node outside the clicked node's
// branch down to a low opacity, and restores full opacity on toggle-off.
// 0.18 is legacy's own BRANCH_DIM_OPACITY constant (graphlink_scene.py:23),
// ported unchanged. This file already established the "restore a legacy dim
// effect via React Flow's own style.opacity, not a new CSS class or DOM
// removal" convention for FADED_CONNECTION_OPACITY below - this reuses it.
const BRANCH_DIM_OPACITY = 0.18;

// Only these five node kinds ever carried this menu item in the legacy app
// (one dedicated *_menu.py file each - graphlink_node_chat_menu.py,
// _code_menu.py, _document_menu.py, _image_menu.py, _thinking_menu.py).
// Every other kind - including Conversation, which ConversationNodeView.tsx's
// own docstring already documents as a deliberate exclusion from this
// feature - is left untouched by computeDimmedNodeIds below, exactly as if
// the feature did not exist for it.
const BRANCH_FOCUS_KINDS = new Set(["chat", "code", "document", "thinking", "image"]);

/**
 * Computes which node ids should be dimmed while branch focus is active,
 * given the node id the user invoked "Hide Other Branches" from
 * (`originId`). Returns an empty set when focus is off (`originId === null`)
 * or the origin node no longer exists (deleted while focus was active - this
 * is how focus self-heals to "show everyone" rather than pointing at
 * nothing; SceneCanvas below derives its own effectiveBranchFocusOriginId
 * with the identical existence check, so the menu label un-flips in the
 * same render rather than one render behind).
 *
 * Ported from graphlink_scene.py's toggle_branch_visibility/
 * _branch_anchor_nodes, adapted to this app's uniform (source, target) edge
 * model. Legacy modeled "chat A replied by chat B" and "chat A owns code
 * block B" as different attribute pairs (parent_node/children vs.
 * parent_content_node); the modern backend models both identically as one
 * edge (backend/canvas.py's SceneDocument.connect), so this tells them apart
 * by KIND instead: a node's "chat anchor" is itself if it is chat-kind, else
 * its nearest parent if THAT is chat-kind, else - an orphaned content node
 * with no chat parent at all, not reachable through today's UI since every
 * creation path supplies one, but not impossible after a manual edge edit -
 * itself, isolating just that one node rather than crashing or silently
 * dimming nothing.
 *
 * The active branch is the set of CHAT nodes reachable from the anchor by
 * walking chat-to-chat edges only, in both directions (ancestors up,
 * descendants down) - a content node attached to any branch member is
 * counted as active via its anchor, not by the walk expanding through it
 * (content nodes are leaves: they never have children of their own in
 * current usage, and are never treated as branch-tree interior nodes even
 * if they somehow did).
 *
 * Both the ancestor walk and the descendant BFS are visited-set-guarded, a
 * deliberate hardening legacy's own equivalent walks do NOT have:
 * SceneDocument.connect() allows creating a cycle or a second incoming edge
 * to the same node with no validation at all (there is no such thing as a
 * malformed QGraphicsScene the same way), so an unguarded walk here could
 * infinite-loop where legacy's structurally never could.
 */
export function computeDimmedNodeIds(scene: SceneState, originId: string | null): Set<string> {
  if (originId === null) return new Set();

  const nodesById = new Map(scene.nodes.map((n) => [n.id, n]));
  if (!nodesById.has(originId)) return new Set();

  // parentOf/childrenOf are built fresh here rather than reusing toFlowNodes'
  // own nodesById map below - this function is exported standalone for
  // direct unit testing (same posture as toFlowEdges/
  // conversationHistoryToDocumentMarkdown above), and threading maps between
  // functions to save one more Map construction over a scene of at most a
  // few hundred nodes is not worth the coupling.
  const parentOf = new Map<string, string>();
  const childrenOf = new Map<string, string[]>();
  for (const e of scene.edges) {
    // First edge whose target is this node wins - a deliberate tie-break for
    // the (currently UI-unreachable, but structurally possible via
    // connectNodes) case of a node with more than one incoming edge. Legacy
    // has no multi-parent concept to fall back on for comparison: parent_node
    // is a single pointer set once at creation, so this is this port's own
    // reasonable interpretation, not a legacy behavior being matched.
    if (!parentOf.has(e.target)) parentOf.set(e.target, e.source);
    const siblings = childrenOf.get(e.source);
    if (siblings) siblings.push(e.target);
    else childrenOf.set(e.source, [e.target]);
  }

  const isChat = (id: string) => nodesById.get(id)?.kind === "chat";

  function chatAnchorOf(id: string): string {
    if (isChat(id)) return id;
    const parentId = parentOf.get(id);
    return parentId && isChat(parentId) ? parentId : id;
  }

  const anchorId = chatAnchorOf(originId);
  const activeChatIds = new Set<string>([anchorId]);

  if (isChat(anchorId)) {
    let cursor = parentOf.get(anchorId);
    while (cursor !== undefined && isChat(cursor) && !activeChatIds.has(cursor)) {
      activeChatIds.add(cursor);
      cursor = parentOf.get(cursor);
    }
    const queue = [anchorId];
    while (queue.length > 0) {
      const current = queue.shift() as string;
      for (const childId of childrenOf.get(current) ?? []) {
        if (activeChatIds.has(childId) || !isChat(childId)) continue;
        activeChatIds.add(childId);
        queue.push(childId);
      }
    }
  }

  const dimmed = new Set<string>();
  for (const node of scene.nodes) {
    if (!BRANCH_FOCUS_KINDS.has(node.kind)) continue;
    if (!activeChatIds.has(chatAnchorOf(node.id))) dimmed.add(node.id);
  }
  return dimmed;
}

/**
 * ADR-002 Workstream 1 ("Branch status and lifecycle") - "reduce a complex
 * graph to its accepted paths." A genuinely separate feature from
 * computeDimmedNodeIds above (that one is keyed off a single clicked
 * origin node; this one is keyed off the persisted branchStatus field
 * across the WHOLE graph, with no origin at all), but architecturally
 * identical: same downward chat-to-chat BFS shape, same chatAnchorOf-based
 * inclusion of non-chat content nodes, same opacity-only/edges-untouched
 * application in toFlowNodes below, same "view-only, not persisted, never
 * touches scene.nodes' own set" posture.
 *
 * Seeds "excluded" from every chat node whose own branchStatus is
 * "rejected" or "superseded", then walks their descendants (chat-to-chat
 * edges only) marking each excluded too - UNLESS a descendant's own
 * branchStatus is "accepted", which is an explicit override: the walk does
 * not enqueue that node (so nothing below it is excluded via THIS path
 * either - not via a second BFS pass, simply by never being visited from
 * here), reactivating a sub-branch without requiring every intermediate
 * node to be individually re-marked. A LATER rejection further down the
 * same subtree still re-excludes correctly, since the seeding loop scans
 * every node's own status independently of BFS reachability, not just
 * origins the walk happens to pass through.
 *
 * The descent from a node to its children follows ONLY the edge parentOf
 * above also recognizes as canonical for that child (the same "first edge
 * whose target is this node wins" tie-break computeDimmedNodeIds documents
 * for the structurally-possible-but-UI-unreachable multi-parent case) -
 * found necessary by adversarial review: without this check, a chat node
 * with one legitimate active parent AND a second, unrelated incoming edge
 * from a rejected/superseded node anywhere else in the graph would get
 * excluded via that second edge alone, even though its real (tie-break-
 * winning) parent is perfectly healthy. Only propagating along the
 * canonical edge keeps this consistent with parentOf's own resolution
 * elsewhere in this function (chatAnchorOf) rather than disagreeing with
 * it. A node with no rejected/superseded ancestor on its OWN canonical
 * chain is never touched, regardless of its own status - being "active" is
 * not itself excluding, only "rejected"/"superseded" (or a canonical
 * ancestor being one) is.
 */
export function computeNonAcceptedNodeIds(scene: SceneState): Set<string> {
  const nodesById = new Map(scene.nodes.map((n) => [n.id, n]));
  const parentOf = new Map<string, string>();
  const childrenOf = new Map<string, string[]>();
  for (const e of scene.edges) {
    if (!parentOf.has(e.target)) parentOf.set(e.target, e.source);
    const siblings = childrenOf.get(e.source);
    if (siblings) siblings.push(e.target);
    else childrenOf.set(e.source, [e.target]);
  }

  const isChat = (id: string) => nodesById.get(id)?.kind === "chat";
  function chatAnchorOf(id: string): string {
    if (isChat(id)) return id;
    const parentId = parentOf.get(id);
    return parentId && isChat(parentId) ? parentId : id;
  }

  const excludedChatIds = new Set<string>();
  const visited = new Set<string>();
  const queue: string[] = [];
  for (const node of nodesById.values()) {
    if (node.kind !== "chat") continue;
    if (node.branchStatus !== "rejected" && node.branchStatus !== "superseded") continue;
    excludedChatIds.add(node.id);
    visited.add(node.id);
    queue.push(node.id);
  }

  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const childId of childrenOf.get(current) ?? []) {
      if (!isChat(childId) || visited.has(childId)) continue;
      // Only follow the edge parentOf also treats as canonical for this
      // child - see this function's own doc comment for why (a second,
      // unrelated incoming edge from elsewhere must never exclude a node
      // whose real parent is healthy).
      if (parentOf.get(childId) !== current) continue;
      visited.add(childId);
      // Explicit override: an "accepted" descendant reactivates this
      // sub-branch - it is never added to excludedChatIds, and the walk
      // does not descend further from it (anything strictly between the
      // rejected/superseded ancestor and this override stays excluded).
      if (nodesById.get(childId)?.branchStatus === "accepted") continue;
      excludedChatIds.add(childId);
      queue.push(childId);
    }
  }

  const excluded = new Set<string>();
  for (const node of scene.nodes) {
    if (!BRANCH_FOCUS_KINDS.has(node.kind)) continue;
    if (excludedChatIds.has(chatAnchorOf(node.id))) excluded.add(node.id);
  }
  return excluded;
}

/**
 * ADR-012 stage 12.5 ("node filter-by-kind/status"): every real content kind
 * a node card can be, in the order ViewPopover.tsx's own FILTER section
 * renders its toggle buttons. "frame"/"container" are deliberately absent -
 * the same exclusion NodeShell.tsx's own doc gives for the shared node-view
 * shell: they are the structural backdrop a filtered-down set of content
 * nodes still sits inside, not content a user would filter for themselves.
 */
export const FILTERABLE_NODE_KINDS = [
  "chat",
  "code",
  "document",
  "thinking",
  "html",
  "image",
  "conversation",
  "web_research",
  "plan",
  "artifact",
  "gitlink",
  "code_sandbox",
  "note",
  "chart",
  "harness",
] as const;

/**
 * Computes which node ids the live kind/status filter (ViewPopover.tsx's
 * FILTER section, backed by sceneStore's own filterKinds/filterStatuses -
 * see those fields' own comment) excludes - dimmed via the SAME isDimmed
 * union toFlowNodes already composes computeDimmedNodeIds/
 * computeNonAcceptedNodeIds through below, not a separate hide/show
 * mechanism. Frame/container nodes are exempt from both axes (never
 * excluded here) for the same reason FILTERABLE_NODE_KINDS omits them.
 *
 * The two axes are ANDed, not ORed: a node is excluded if it fails EITHER
 * an active kind filter OR an active status filter, so a user who has
 * narrowed to kind="code" and then additionally narrows to status=
 * "accepted" sees the intersection (accepted code nodes only), not the
 * union - the ordinary meaning of stacking two facets. An axis with an
 * empty set never excludes anything on its own (mirrors focusAcceptedPaths'
 * own "off by default" semantics); both empty short-circuits to an empty
 * result without walking scene.nodes at all.
 */
export function computeFilteredOutNodeIds(
  scene: SceneState,
  filterKinds: ReadonlySet<string>,
  filterStatuses: ReadonlySet<string>,
): Set<string> {
  const excluded = new Set<string>();
  if (filterKinds.size === 0 && filterStatuses.size === 0) return excluded;
  for (const node of scene.nodes) {
    if (node.kind === "frame" || node.kind === "container") continue;
    const kindExcluded = filterKinds.size > 0 && !filterKinds.has(node.kind);
    const statusExcluded = filterStatuses.size > 0 && !filterStatuses.has(node.branchStatus);
    if (kindExcluded || statusExcluded) excluded.add(node.id);
  }
  return excluded;
}

/**
 * ADR-011 stage 11.1: one shared O(N+E) index-map pass, consumed by both
 * toFlowNodes and toFlowEdges below in place of the three separate ad hoc
 * maps they used to build on their own (toFlowNodes' own nodesById;
 * toFlowEdges' own kindOf + dockedNodeIds) - and, more importantly, in
 * place of the O(N*E) per-node edge scans that used to live INSIDE
 * toFlowNodes' own per-node loop: the chat branch's dockedChildren used to
 * do `for (const e of scene.edges)` (a full O(E) scan) for EVERY chat node,
 * and the code branch did `scene.edges.find(...)` (another O(E) scan) for
 * EVERY code node. edgesBySource/edgesByTarget below group every edge by
 * its endpoint ONCE, so a per-node lookup is O(out-/in-degree) instead of
 * O(E) - and since the sum of every node's out-/in-degree over the whole
 * loop is exactly E, the total cost across the WHOLE function is O(N+E),
 * not O(N*E).
 *
 * computeDimmedNodeIds/computeNonAcceptedNodeIds above deliberately build
 * their OWN separate nodesById/parentOf/childrenOf maps rather than reusing
 * this one - see computeDimmedNodeIds' own doc comment for why (exported
 * standalone for direct unit testing, not worth coupling to save one more
 * Map construction over a scene of at most a few hundred nodes).
 */
interface SceneIndexes {
  nodesById: Map<string, SceneNodeRow>;
  edgesBySource: Map<string, SceneEdgeRow[]>;
  edgesByTarget: Map<string, SceneEdgeRow[]>;
  dockedNodeIds: Set<string>;
}

function buildSceneIndexes(scene: SceneState): SceneIndexes {
  const nodesById = new Map<string, SceneNodeRow>();
  const dockedNodeIds = new Set<string>();
  for (const n of scene.nodes) {
    nodesById.set(n.id, n);
    if (n.isDocked) dockedNodeIds.add(n.id);
  }
  const edgesBySource = new Map<string, SceneEdgeRow[]>();
  const edgesByTarget = new Map<string, SceneEdgeRow[]>();
  for (const e of scene.edges) {
    const bySource = edgesBySource.get(e.source);
    if (bySource) bySource.push(e);
    else edgesBySource.set(e.source, [e]);
    const byTarget = edgesByTarget.get(e.target);
    if (byTarget) byTarget.push(e);
    else edgesByTarget.set(e.target, [e]);
  }
  return { nodesById, edgesBySource, edgesByTarget, dockedNodeIds };
}

/**
 * ADR-011 stage 11.1: a stable per-node-id callback dispatcher.
 *
 * Every *NodeView callback (onDelete, onToggleCollapse, ...) used to be a
 * fresh inline closure minted on EVERY toFlowNodes call - ~20 allocations
 * per chat node alone, on every single scene snapshot/patch, regardless of
 * whether that node changed at all. React.memo on the node-view side
 * (landing alongside this change on the other *NodeView.tsx files) can
 * never see stable props while that keeps happening, no matter what else
 * this function does.
 *
 * Fix: ONE function object per callback is created the FIRST time a given
 * node id is seen (getDispatcher below, backed by cache.dispatchers), then
 * reused for that id for as long as the node exists. Each function closes
 * over the node's `id` (immutable for the dispatcher entry's whole
 * lifetime - a node's kind/id never changes after creation) and a mutable
 * `liveRef` that toFlowNodes updates on EVERY call, cache-hit or not,
 * before any function in `fns` can run - so a click on a long-lived stable
 * callback always observes the CURRENT n/store/onOpenDocumentView/
 * onToggleBranchFocus, never a stale snapshot from whenever the closure
 * happened to be created. This is the "read current values through a ref
 * rather than closing over values that change every call" pattern.
 */
interface DispatcherLive {
  n: SceneNodeRow;
  store: SceneStore;
  onOpenDocumentView: (markdown: string, sourceLabel: string) => void;
  onToggleBranchFocus: (nodeId: string) => void;
  // ADR-018 stage 18.3: optional - only the chat dispatcher's own call
  // site below supplies it; every other kind's getDispatcher call omits
  // it exactly like they already omit `extra` above.
  getComposerRoute?: () => { provider: string; modelId: string };
  // Kind-specific derived value(s) a dispatcher needs beyond `n` itself,
  // NOT themselves part of `n` (e.g. the code branch's parentChatNodeId,
  // resolved from edges rather than n's own fields) - cast to the concrete
  // shape inside each dispatcher factory below that actually uses it.
  extra?: unknown;
}

interface DispatcherEntry {
  liveRef: { current: DispatcherLive };
  fns: Record<string, unknown>;
}

export interface ToFlowNodesCache {
  dispatchers: Map<string, DispatcherEntry>;
  // Keyed by the SceneNode's OWN object reference - ADR-003 deltas
  // guarantee an unchanged node keeps that EXACT reference (sceneStore.ts's
  // applyScenePatch: "Untouched nodes keep their EXACT existing object
  // references"), so a WeakMap hit here doubles as the "did this node
  // change" check for free: an unchanged node's `n` is literally the same
  // key it was cached under last call; a changed one is a brand-new object
  // the WeakMap has never seen, so `.get` misses automatically. Using a
  // WeakMap (not a plain Map keyed by node id) means a replaced node's old
  // entry is reclaimed by GC on its own - no separate eviction pass needed
  // for the fast-changing case (e.g. a streaming chat node's `content`
  // changing every token, which mints a new SceneNodeRow every time).
  //
  // `extraSig` covers everything the emitted flow node depends on BESIDES
  // `n` itself - dimming/branch-focus flags, dockedChildren, the code
  // branch's parentChatNodeId, group memberKinds (see each kind's own
  // comment in toFlowNodes below) - so a cache hit only fires when NOTHING
  // relevant changed, not merely when `n`'s reference didn't.
  flowNodes: WeakMap<SceneNodeRow, { extraSig: string; flowNode: SceneFlowNode }>;
}

export function createToFlowNodesCache(): ToFlowNodesCache {
  return { dispatchers: new Map(), flowNodes: new WeakMap() };
}

// Drops dispatcher entries for node ids no longer in the scene - a node
// once deleted never comes back under the same id, so its dispatcher (and
// the liveRef it closes over) would otherwise sit in the Map forever for
// the lifetime of the canvas. flowNodes above needs no equivalent: it is a
// WeakMap keyed by the node object itself, so a deleted node's entry is
// simply unreachable and reclaimed by GC on its own.
function pruneDispatcherCache(cache: ToFlowNodesCache, nodesById: Map<string, SceneNodeRow>): void {
  if (cache.dispatchers.size === 0) return;
  for (const id of cache.dispatchers.keys()) {
    if (!nodesById.has(id)) cache.dispatchers.delete(id);
  }
}

function getDispatcher<T extends Record<string, unknown>>(
  cache: ToFlowNodesCache,
  id: string,
  live: DispatcherLive,
  factory: (id: string, liveRef: { current: DispatcherLive }) => T,
): T {
  const existing = cache.dispatchers.get(id);
  if (existing) {
    existing.liveRef.current = live;
    return existing.fns as T;
  }
  const liveRef = { current: live };
  const fns = factory(id, liveRef);
  cache.dispatchers.set(id, { liveRef, fns: fns as Record<string, unknown> });
  return fns;
}

function makeChatFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.deleteChatNode(id),
    onUndockChild: (childId: string) => liveRef.current.store.setNodeDocked(childId, false),
    onRegenerate: () => liveRef.current.store.regenerateResponse(id),
    onGenerateImage: () => liveRef.current.store.generateImage(id),
    // R6.2: real chart generation - fires the new generateChart intent with
    // this chat node as the parent (see ChatNodeView.tsx's own Generate
    // Chart submenu). Fire-and-forget, same posture as onGenerateImage.
    onGenerateChart: (chartType: string) => liveRef.current.store.generateChart(id, chartType),
    // R8a: the two note agents, restored from the deleted Qt app. Same
    // fire-and-forget posture as onGenerateImage above.
    onGenerateKeyTakeaway: () => liveRef.current.store.generateKeyTakeaway(id),
    onGenerateExplainerNote: () => liveRef.current.store.generateExplainerNote(id),
    // R8a: Open Document View - shows this node's own message text verbatim
    // in the read-only document panel. Guards on non-blank content, matching
    // legacy's own document-view action exactly: a notification on nothing,
    // not a silent no-op.
    onOpenDocumentView: () => {
      const { n, onOpenDocumentView, store } = liveRef.current;
      if (n.content.trim()) onOpenDocumentView(n.content, n.isUser ? "Your message" : "Assistant message");
      else store.showInfoNotification(NO_DOCUMENT_VIEW_CONTENT_MESSAGE);
    },
    onToggleBranchFocus: () => liveRef.current.onToggleBranchFocus(id),
    // ADR-002 Workstream 1: stages this node as sceneStore's
    // replyTargetNodeId - the composer's next Send then reads and consumes
    // it (see sceneStore.ts's sendMessage).
    onBranchFromHere: () => liveRef.current.store.setReplyTargetNodeId(id),
    // ADR-002 Workstream 1 ("Branch status and lifecycle"). Fire-and-forget,
    // same posture as onBranchFromHere/onGenerateKeyTakeaway above.
    onSetBranchStatus: (status: string) => liveRef.current.store.setBranchStatus(id, status),
    onSetFinalDeliverable: (isFinal: boolean) => liveRef.current.store.setFinalDeliverable(id, isFinal),
    onCollapseBranch: (collapsed: boolean) => liveRef.current.store.collapseBranch(id, collapsed),
    // R6.3: the node's own scroll position within its content area - read on
    // mount by ChatNodeView (restore) and reported (debounced) via the
    // setChatScrollValue intent on every scroll.
    onScrollChange: (value: number) => liveRef.current.store.setChatScrollValue(id, value),
    // ADR-006 stage 6.4 (universal streaming): a Regenerate for this node now
    // streams - subscribeStream is the same generic transport passthrough
    // the code_sandbox branch below also injects.
    subscribeStream: (requestId: string, listener: StreamListener) =>
      liveRef.current.store.subscribeStream(requestId, listener),
    // ADR-006 stage 6.4 review fix: per-node Stop for an in-flight streamed
    // regenerate. Reuses cancelConversationRequest (fires the generic
    // cancelChatRequest intent by requestId) rather than adding a new
    // intent. Same null-guard pattern as the conversation branch's own
    // onCancel.
    onCancelRegenerate: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelConversationRequest(n.pendingRequestId);
    },
    // ADR-018 stage 18.3: pins this node to whichever model is CURRENTLY
    // active in the Composer (getComposerRoute reads composerStore.
    // getComposer().route at CLICK time - see toFlowNodes' own comment on
    // why that's a plain getter, not a subscribed value). A blank
    // provider/modelId (no route resolved yet, e.g. no provider
    // configured) is a genuine no-op rather than pinning to nothing.
    onPinToCurrentModel: () => {
      const { store, getComposerRoute } = liveRef.current;
      const route = getComposerRoute?.() ?? { provider: "", modelId: "" };
      if (route.provider && route.modelId) store.setModelOverride(id, route.provider, route.modelId);
    },
    onClearModelOverride: () => liveRef.current.store.clearModelOverride(id),
  };
}

function makeCodeFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    // parentChatNodeId (this code node's own one-hop parent lookup) arrives
    // via `extra`, resolved by toFlowNodes' edgesByTarget index below - see
    // that call site's own comment for why it can't just be `id`.
    onRegenerate: () => {
      const { store, extra } = liveRef.current;
      const parentChatNodeId = extra as string | null;
      if (parentChatNodeId) store.regenerateResponse(parentChatNodeId);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onToggleBranchFocus: () => liveRef.current.onToggleBranchFocus(id),
  };
}

function makeDocumentFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    // setChatCollapsed's backend handler (backend/canvas.py) looks up ANY
    // node by id and sets is_collapsed - it does not special-case "chat"
    // kind despite the intent's name - so it is reused as-is here rather
    // than inventing a setDocumentCollapsed intent the backend doesn't
    // register.
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDock: () => liveRef.current.store.setNodeDocked(id, true),
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onToggleBranchFocus: () => liveRef.current.onToggleBranchFocus(id),
  };
}

function makeThinkingFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onDock: () => liveRef.current.store.setNodeDocked(id, true),
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onToggleBranchFocus: () => liveRef.current.onToggleBranchFocus(id),
  };
}

function makeHtmlFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    // R6.3: the Source/Preview split position - read on mount by
    // HtmlNodeView (restore) and reported (debounced) via the
    // setHtmlSplitterState intent once a drag settles.
    onSplitterChange: (value: number) => liveRef.current.store.setHtmlSplitterState(id, value),
  };
}

function makeImageFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onDelete: () => liveRef.current.store.removeNodes([id]),
    // R4.4a: unlike CodeNodeView's onRegenerate, no client-side parent
    // lookup/null-guard is needed here - the backend resolves the image's
    // parent chat node internally.
    onRegenerate: () => liveRef.current.store.regenerateImage(id),
    onToggleBranchFocus: () => liveRef.current.onToggleBranchFocus(id),
  };
}

function makeConversationFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onSend: (text: string) => liveRef.current.store.sendConversationMessage(id, text),
    onDeleteMessage: (index: number) => liveRef.current.store.deleteConversationMessage(id, index),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelConversationRequest(n.pendingRequestId);
    },
    // R8a: Open Document View - the node's ENTIRE message history, formatted
    // as a numbered transcript. Guards on a non-empty formatted result the
    // same way the chat branch's own onOpenDocumentView guards on non-blank
    // content.
    onOpenDocumentView: () => {
      const { n, onOpenDocumentView, store } = liveRef.current;
      const markdown = conversationHistoryToDocumentMarkdown(n.history);
      if (markdown) onOpenDocumentView(markdown, "Conversation transcript");
      else store.showInfoNotification(NO_DOCUMENT_VIEW_CONTENT_MESSAGE);
    },
    subscribeStream: (requestId: string, listener: StreamListener) =>
      liveRef.current.store.subscribeStream(requestId, listener),
  };
}

function makeWebResearchFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onRun: (query: string) => liveRef.current.store.runWebResearch(id, query),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelWebResearchRequest(n.pendingRequestId);
    },
    // ADR-021 stage 21.5: the per-node knowledge-retention opt-in.
    onSetRetainToKnowledge: (retain: boolean) =>
      liveRef.current.store.setWebResearchRetainToKnowledge(id, retain),
  };
}

function makePlanFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onStartExecution: () => liveRef.current.store.startBuilderExecution(id),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelBuilderRun(n.pendingRequestId);
    },
    // Zero-argument approve/deny closed over the CURRENT snapshot's
    // pendingRequestId - the CodeExecutionApprovalPanel posture: the
    // panel structurally cannot name a different request than the one
    // this row shows.
    onApproveTool: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.approveBuilderTool(n.pendingRequestId);
    },
    onDenyTool: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.denyBuilderTool(n.pendingRequestId);
    },
    // ADR-008 stage 8.4: "undo this build" - the ADR-010 stage-10.5
    // machinery (scene/undoRun) finally gets its affordance. Keyed on the
    // ROW's builderRunId, not pendingRequestId: the run is over when this
    // is offered.
    onUndoBuild: () => {
      const { n, store } = liveRef.current;
      if (n.builderRunId) store.undoRun(n.builderRunId);
    },
    // ADR-008 stage 8.6: save-your-build - the backend derives the name
    // from the goal when none is given.
    onSaveRecipe: () => liveRef.current.store.saveBuilderRecipe(id),
    // ADR-021 stage 21.3: the checklist edit ADR-008 decided on. The whole
    // replacement list rides in one intent (setPlanSteps is a replace, not
    // a patch), so PlanNodeView commits a finished draft rather than
    // streaming keystrokes.
    onSetPlanSteps: (steps: PlanStepData[]) => liveRef.current.store.setPlanSteps(id, steps),
  };
}

function makeHarnessFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    // Node-id-shaped: which AGENT, not which run - the backend claims a
    // fresh run per follow-up task (the startBuilderExecution shape).
    onSend: (text: string) => liveRef.current.store.sendHarnessMessage(id, text),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelHarnessRun(n.pendingRequestId);
    },
    // Zero-argument approve/deny closed over the CURRENT snapshot's
    // pendingRequestId - the CodeExecutionApprovalPanel posture.
    onApproveTool: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.approveHarnessTool(n.pendingRequestId);
    },
    onDenyTool: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.denyHarnessTool(n.pendingRequestId);
    },
    onApproveToolForSession: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.approveHarnessToolForSession(n.pendingRequestId);
    },
    // Same current-snapshot posture as approve/deny: the run being answered
    // is whichever one this node currently has parked, never an id the view
    // captured earlier and might replay against a newer run.
    onAnswerQuestion: (answer: string) => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.answerHarnessQuestion(n.pendingRequestId, answer);
    },
    onPickWorkspace: () => liveRef.current.store.pickHarnessWorkspace(id),
    onUseScratch: () => liveRef.current.store.useHarnessScratch(id),
  };
}

function makeArtifactFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onSubmit: (text: string) => liveRef.current.store.sendArtifactMessage(id, text),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelArtifactRequest(n.pendingRequestId);
    },
  };
}

function makeGitlinkFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onFetchRepositories: () => liveRef.current.store.fetchGitlinkRepositories(id),
    onLoadTree: (repo: string, branch: string) => liveRef.current.store.loadGitlinkRepoTree(id, repo, branch),
    onSetLocalRoot: (localRoot: string) => liveRef.current.store.setGitlinkLocalRoot(id, localRoot),
    onBrowseLocalRoot: () => liveRef.current.store.pickGitlinkLocalRoot(id),
    onImportSnapshot: (repo: string, branch: string) => liveRef.current.store.importGitlinkSnapshot(id, repo, branch),
    onBuildContext: (scopeMode: string, selectedPaths: string[]) =>
      liveRef.current.store.buildGitlinkContext(id, scopeMode, selectedPaths),
    onFetchContext: () => liveRef.current.store.fetchGitlinkContext(id),
    onRun: (taskPrompt: string) => liveRef.current.store.runGitlinkChangeSet(id, taskPrompt),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelGitlinkRequest(n.pendingRequestId);
    },
    onApply: (fingerprint: string) => liveRef.current.store.applyGitlinkChanges(id, fingerprint),
  };
}

function makeCodeSandboxFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleCollapse: () => {
      const { n, store } = liveRef.current;
      store.setChatCollapsed(id, !n.isCollapsed);
    },
    onDelete: () => liveRef.current.store.removeNodes([id]),
    onSetRequirements: (requirementsText: string) =>
      liveRef.current.store.setCodeSandboxRequirements(id, requirementsText),
    onToggleAllowSourceBuilds: (allow: boolean) => liveRef.current.store.setCodeSandboxAllowSourceBuilds(id, allow),
    onRun: (inputText: string) => liveRef.current.store.runCodeSandbox(id, inputText),
    onCancel: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.cancelCodeSandboxRequest(n.pendingRequestId);
    },
    // CRITICAL (see CodeExecutionApprovalPanel.tsx's own module doc): these
    // read n.pendingRequestId - the CURRENT scene snapshot's own value for
    // THIS node via liveRef, never anything the UI layer could supply as a
    // distinct argument.
    onApprove: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.approveCodeExecution(n.pendingRequestId);
    },
    onDeny: () => {
      const { n, store } = liveRef.current;
      if (n.pendingRequestId) store.denyCodeExecution(n.pendingRequestId);
    },
    // Generic passthrough to the transport's own stream fan-out - the live
    // terminal pane keys its subscription off data.pendingRequestId itself,
    // this closure only needs to exist so the component never touches the
    // store/transport directly.
    subscribeStream: (requestId: string, listener: StreamListener) =>
      liveRef.current.store.subscribeStream(requestId, listener),
  };
}

function makeNoteFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onSetContent: (content: string) => liveRef.current.store.setNoteContent(id, content),
    onSetColor: (color: string | null, headerColor: string | null) =>
      liveRef.current.store.setGroupColor(id, color, headerColor),
    onDelete: () => liveRef.current.store.removeNodes([id]),
  };
}

function makeGroupFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onSetLabel: (text: string) => liveRef.current.store.setGroupLabel(id, text),
    onToggleCollapsed: () => liveRef.current.store.toggleGroupCollapsed(id),
    onToggleLock: () => liveRef.current.store.toggleFrameLock(id),
    onSetColor: (color: string | null, headerColor: string | null) =>
      liveRef.current.store.setGroupColor(id, color, headerColor),
    onResize: (width: number, height: number) => liveRef.current.store.resizeFrame(id, width, height),
    onFitToContent: () => liveRef.current.store.fitFrameToContent(id),
    onUngroup: () => liveRef.current.store.ungroup(id),
  };
}

function makeChartFns(id: string, liveRef: { current: DispatcherLive }) {
  return {
    onToggleAspectLock: () => liveRef.current.store.toggleChartAspectLock(id),
    onResize: (width: number, height: number) => liveRef.current.store.resizeChart(id, width, height),
  };
}

// Exported standalone for direct unit testing (same posture as
// toFlowEdges below) - covers the parentChatNodeId
// derivation below without needing a full <ReactFlow> mount.
export function toFlowNodes(
  scene: SceneState,
  store: SceneStore,
  onOpenDocumentView: (markdown: string, sourceLabel: string) => void = () => {},
  branchFocusOriginId: string | null = null,
  onToggleBranchFocus: (nodeId: string) => void = () => {},
  // ADR-002 Workstream 1 ("Branch status and lifecycle") - "Focus Accepted
  // Paths" (ViewPopover.tsx's checkbox, backed by sceneStore's own
  // focusAcceptedPaths field - see that field's own comment for why it
  // lives there rather than as component state here).
  focusAcceptedPaths = false,
  // ADR-011 stage 11.1: threaded from CanvasInner's own useRef (see
  // CanvasInner below) so the SAME cache survives across every call for the
  // canvas's whole lifetime, which is what actually makes the per-node
  // dispatcher/whole-flow-node memoization below effective across
  // snapshots. Callers that omit it - every existing direct unit test in
  // SceneCanvas.test.tsx - get a fresh, single-use cache instead: still
  // fully correct (a fresh cache just means nothing has been seen before,
  // so every node "misses" and gets built the same way it always did), just
  // not memoized across separate calls, which none of those tests need.
  cache: ToFlowNodesCache = createToFlowNodesCache(),
  // ADR-018 stage 18.3: read at CLICK time inside makeChatFns' own
  // onPinToCurrentModel (never subscribed/rendered), so a plain getter -
  // not a value - keeps this out of every cache key/memo comparator above,
  // matching onOpenDocumentView/onToggleBranchFocus's own "callback, not
  // data" posture. Default returns "" so every existing caller (every
  // direct unit test in SceneCanvas.test.tsx) that omits it keeps working:
  // the resulting Pin action would just pin to an empty ref, exactly as
  // unreachable in a real session as this default itself.
  getComposerRoute: () => { provider: string; modelId: string } = () => ({ provider: "", modelId: "" }),
  // ADR-012 stage 12.5: ViewPopover.tsx's own FILTER section, backed by
  // sceneStore's filterKinds/filterStatuses - see computeFilteredOutNodeIds'
  // own doc. Both default to an empty set (feature off), same posture as
  // focusAcceptedPaths defaulting to false, so every existing caller that
  // omits these two keeps working unchanged.
  filterKinds: ReadonlySet<string> = new Set(),
  filterStatuses: ReadonlySet<string> = new Set(),
): SceneFlowNode[] {
  // ADR-011 stage 11.1: ONE upfront O(N+E) pass replacing this function's
  // old standalone `nodesById` map build, PLUS the O(N*E) per-node edge
  // scans that used to live below (dockedChildren's full scene.edges scan
  // per chat node; the code branch's scene.edges.find per code node) - see
  // buildSceneIndexes' own doc above for the full reasoning.
  const { nodesById, edgesBySource, edgesByTarget } = buildSceneIndexes(scene);
  // R8a: computed once per call, then just a Set.has() per node below -
  // see computeDimmedNodeIds' own doc for why it builds its own maps rather
  // than reusing nodesById above.
  const dimmedIds = computeDimmedNodeIds(scene, branchFocusOriginId);
  // ADR-002 Workstream 1: only computed when the toggle is actually on -
  // an empty Set otherwise, same "cheap no-op when the feature isn't
  // active" posture as dimmedIds above already has via branchFocusOriginId
  // defaulting to null. Composed (unioned) with dimmedIds below rather
  // than picked between - both dimming lenses can be active at once.
  const nonAcceptedIds = focusAcceptedPaths ? computeNonAcceptedNodeIds(scene) : new Set<string>();
  // ADR-012 stage 12.5: a third dimming lens, same "cheap no-op when off"
  // posture as nonAcceptedIds above (computeFilteredOutNodeIds itself
  // short-circuits when both filter sets are empty).
  const filteredOutIds = computeFilteredOutNodeIds(scene, filterKinds, filterStatuses);
  const isDimmed = (id: string) => dimmedIds.has(id) || nonAcceptedIds.has(id) || filteredOutIds.has(id);
  // BRANCH_FOCUS_KINDS-wide flag (chat/code/document/thinking/image only -
  // every other kind's data below never reads it): whether "Hide Other
  // Branches" is active from ANY origin, scene-wide. Computed once here
  // (not per node) purely so each of those 5 kinds' cache key below can
  // fold it in cheaply alongside isDimmed(n.id).
  const isBranchFocusActive = branchFocusOriginId !== null;
  const flowNodes: SceneFlowNode[] = [];

  for (const n of scene.nodes) {
    // A docked node (any kind) is fully removed from the canvas (mirrors the
    // legacy scene's own behavior for both ThinkingNode and DocumentNode,
    // which the legacy code lets dock via the same attachment_kind-routed
    // mechanism) - not rendered-but-hidden. This check is deliberately
    // generic rather than "thinking"-only: backend/canvas.py's
    // setNodeDocked has no kind restriction, and toFlowEdges below already
    // filters a docked node's edges generically - a kind-specific check here
    // would leave a docked non-thinking node rendered with no edge to it the
    // moment any future node type wires up a real onDock action.
    if (n.isDocked) continue;
    if (n.kind === "chat") {
      // dockedChildren: this chat node's own edges whose target is currently
      // docked - the new stack's equivalent of the legacy scene's per-node
      // docked-children list. title is the closest faithful stand-in for a
      // per-node-type "docked label" concept (none exists in the new stack).
      // ADR-011 stage 11.1: reads ONLY this node's own outgoing edges via
      // edgesBySource (O(out-degree)) instead of scanning the full
      // scene.edges array (O(E)) - see buildSceneIndexes' own doc above.
      const dockedChildren: { id: string; label: string }[] = [];
      for (const e of edgesBySource.get(n.id) ?? []) {
        const target = nodesById.get(e.target);
        if (target?.isDocked) dockedChildren.push({ id: target.id, label: target.title });
      }
      const dimmedVal = isDimmed(n.id);
      // dockedChildren depends on OTHER nodes' isDocked/title (via edges),
      // not on n's own reference - a child docking/undocking never touches
      // the PARENT chat node's own object, so n-reference-only invalidation
      // would go stale here without this signature folded into the cache
      // key (see ToFlowNodesCache.flowNodes' own doc above).
      const dockedChildrenSig = dockedChildren.map((c) => `${c.id}:${c.label}`).join("|");
      const extraSig = `${dimmedVal ? 1 : 0}${isBranchFocusActive ? 1 : 0}|${dockedChildrenSig}`;
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(
        cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus, getComposerRoute }, makeChatFns,
      );
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "chat" as const,
        position: { x: n.x, y: n.y },
        // R8a: "Hide Other Branches" dimming - see computeDimmedNodeIds' own
        // doc above. undefined (not an explicit opacity: 1) when not dimmed,
        // so this never overrides anything else that might set style later.
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          content: n.content,
          isUser: n.isUser,
          isCollapsed: n.isCollapsed,
          dockedChildren,
          // ADR-011 stage 11.1: the ~17 onXxx/subscribeStream callbacks that
          // used to be minted inline here on every call now come from the
          // stable per-node-id dispatcher (fns) built above - see
          // makeChatFns' own doc for why that keeps the SAME function
          // references across calls instead of allocating fresh closures.
          ...fns,
          // R8a: "Hide Other Branches" - isBranchFocusActive is scene-wide
          // (not per-node), purely so the menu button can flip its own label
          // to "Show All Branches" once ANY branch focus is active, matching
          // legacy's own `"Show All Branches" if is_branch_hidden else
          // "Hide Other Branches"` regardless of which node's menu is open.
          isBranchFocusActive,
          // ADR-002 Workstream 1 ("Synthesize Branches"): provenance carried
          // by the result node. provider/model are None/absent for every
          // ordinary chat node (the vast majority), rendered as no badge at
          // all - see ChatNodeView.tsx's own guard.
          provider: n.provider ?? null,
          model: n.model ?? null,
          // ADR-018 stage 18.3: the model-override PIN, opposite direction
          // from provider/model above - see ChatNodeData's own comment.
          overrideProvider: n.overrideProvider,
          overrideModelId: n.overrideModelId,
          isBranchSynthesis: n.isBranchSynthesis,
          synthesisInstructions: n.synthesisInstructions,
          synthesisSourceNodeIds: n.itemIds,
          // ADR-002 Workstream 1 ("Branch status and lifecycle"). Fire-and-
          // forget, same posture as onBranchFromHere/onGenerateKeyTakeaway
          // above - the new value arrives through the next scene snapshot.
          branchStatus: n.branchStatus,
          isFinalDeliverable: n.isFinalDeliverable,
          // R6.3: the node's own scroll position within its content area -
          // read on mount by ChatNodeView (restore) and reported (debounced)
          // via the setChatScrollValue intent on every scroll.
          chatScrollValue: n.chatScrollValue,
          // ADR-006 stage 6.4 (universal streaming): a Regenerate for this
          // node now streams - the in-flight request id arrives on the
          // node's OWN row (published on the scene topic, never via the
          // composer), and ChatNodeView keys its live subscription off it.
          pendingRequestId: n.pendingRequestId ?? null,
          // ADR-006 stage 6.4 (partial-output preservation): true when the
          // content field is a partial response the backend committed after
          // a killed stream - ChatNodeView renders its "interrupted" banner.
          responseIncomplete: n.responseIncomplete,
          // ADR-007 stage 7.4: this turn's tool calls + results, in call
          // order - [] for the overwhelming majority of chat nodes (see
          // ToolInvocationRow's own comment, contracts/graphlink_scene_
          // payload.py). Rendered as a collapsible section in
          // ChatNodeView.tsx.
          toolInvocations: n.toolCalls,
          // ADR-016 stage 16.2: real usage + the cost snapshot taken when it
          // was stamped - null for user messages and replies whose provider
          // reports nothing. See ChatNodeView.tsx's own render guard.
          promptTokens: n.promptTokens ?? null,
          completionTokens: n.completionTokens ?? null,
          estimatedCostUsd: n.estimatedCostUsd ?? null,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "code") {
      // parentChatNodeId: this code node's own one-hop parent lookup - the
      // new stack's equivalent of legacy's CodeNode.parent_content_node,
      // resolved client-side (same one-hop-via-edges style as
      // dockedChildren above) since the backend never kind-sniffs a code
      // node id back to its parent chat node (see regenerateResponse's own
      // comment above and SceneCanvas's regenerate-response design notes).
      // ADR-011 stage 11.1: edgesByTarget.get(n.id)?.[0] replaces
      // `scene.edges.find((e) => e.target === n.id)` - both return the
      // FIRST edge in scene.edges' own order whose target is this node
      // (edgesByTarget's per-target arrays are built by iterating
      // scene.edges in order in buildSceneIndexes above, so `[0]` is
      // exactly what `.find` would have returned), just without re-scanning
      // the full array for every code node.
      const parentEdge = edgesByTarget.get(n.id)?.[0];
      const parentChatNodeId = parentEdge ? parentEdge.source : null;
      const dimmedVal = isDimmed(n.id);
      const extraSig = `${dimmedVal ? 1 : 0}${isBranchFocusActive ? 1 : 0}|${parentChatNodeId ?? ""}`;
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(
        cache,
        n.id,
        { n, store, onOpenDocumentView, onToggleBranchFocus, extra: parentChatNodeId },
        makeCodeFns,
      );
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "code" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          code: n.code,
          language: n.language,
          parentChatNodeId,
          // R8a: "Hide Other Branches" - see the chat branch above for why
          // isBranchFocusActive is scene-wide rather than per-node.
          isBranchFocusActive,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "document") {
      const dimmedVal = isDimmed(n.id);
      const extraSig = `${dimmedVal ? 1 : 0}${isBranchFocusActive ? 1 : 0}`;
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeDocumentFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "document" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          title: n.title,
          content: n.content,
          attachmentKind: n.attachmentKind,
          filePath: n.filePath,
          mimeType: n.mimeType,
          // Generated SceneNodeRow marks these `?: number | null` (optional
          // Python field), so the read can be `undefined`; DocumentNodeData
          // is strictly `number | null` (matches the wire value, which is
          // always present - the backend dataclass always serializes both
          // keys). Coalesce here rather than loosening DocumentNodeData.
          durationSeconds: n.durationSeconds ?? null,
          byteSize: n.byteSize ?? null,
          previewLabel: n.previewLabel,
          isCollapsed: n.isCollapsed,
          // R8a: "Hide Other Branches" - see the chat branch above.
          isBranchFocusActive,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "thinking") {
      // Docked-hiding is handled by the generic check above; once undocked,
      // it resurfaces as a badge + "Reveal Docked Items" entry on its parent
      // chat node (dockedChildren above).
      const dimmedVal = isDimmed(n.id);
      const extraSig = `${dimmedVal ? 1 : 0}${isBranchFocusActive ? 1 : 0}`;
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeThinkingFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "thinking" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          thinkingText: n.content,
          // R8a: "Hide Other Branches" - see the chat branch above.
          isBranchFocusActive,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "html") {
      // No onDock here (unlike thinking/document) - HtmlNodeView never
      // offers a "dock into parent" action, so this kind never sets
      // isDocked=true through any UI path of its own. It still passes
      // through the generic `if (n.isDocked) continue` guard above
      // untouched: that check is is_docked-field-generic, not kind-gated,
      // so an html node docked via a direct WS call (setNodeDocked has no
      // kind restriction backend-side) would still be omitted correctly -
      // it would just have no UI-driven way back (no "Reveal Docked Items"
      // entry exists on this node's own header, and ChatNodeView's own
      // dockedChildren/undock badge is kind-agnostic already, so undocking
      // it is still possible from the parent chat node's side).
      // No isBranchFocusActive here - HtmlNodeView never carried this menu
      // item (see BRANCH_FOCUS_KINDS above). isDimmed IS wired below as of
      // ADR-012 stage 12.5 - dimmedIds/nonAcceptedIds are always empty for
      // this kind (both are BRANCH_FOCUS_KINDS-gated), so this only ever
      // reflects the node filter's own dimming.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeHtmlFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "html" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          htmlContent: n.content,
          isCollapsed: n.isCollapsed,
          // R6.3: the Source/Preview split position - read on mount by
          // HtmlNodeView (restore; null means "no saved value, use the
          // component's own 50/50 default") and reported (debounced) via the
          // setHtmlSplitterState intent once a drag settles. See
          // canvasConstants.ts's own HTML_SPLIT_* doc for why this exists
          // now despite being scoped OUT back in R3.17/R3.18.
          htmlSplitterState: n.htmlSplitterState ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "image") {
      // No onDock here either (same reasoning as the html branch above) -
      // ImageNodeView never offers a "dock into parent" action, so this kind
      // never sets isDocked=true through any UI path of its own. The generic
      // `if (n.isDocked) continue` guard above still covers it correctly if
      // it were ever docked via a direct WS call, same as html.
      const dimmedVal = isDimmed(n.id);
      const extraSig = `${dimmedVal ? 1 : 0}${isBranchFocusActive ? 1 : 0}`;
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeImageFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "image" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          imageAssetId: n.imageAssetId,
          prompt: n.content,
          // R8a: "Hide Other Branches" - see the chat branch above.
          isBranchFocusActive,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "conversation") {
      // No onDock here either (same reasoning as the html/image branches
      // above) - ConversationNodeView never offers a dock-into-parent
      // action, so this kind never sets isDocked=true through any UI path
      // of its own; the generic `if (n.isDocked) continue` guard above still
      // covers it correctly if it were ever docked via a direct WS call.
      // No isBranchFocusActive here either - see the html branch's own
      // comment above (ConversationNodeView is also outside
      // BRANCH_FOCUS_KINDS). isDimmed IS wired below as of ADR-012 stage
      // 12.5 - see the html branch's own comment for why that's safe.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(
        cache,
        n.id,
        { n, store, onOpenDocumentView, onToggleBranchFocus },
        makeConversationFns,
      );
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "conversation" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          history: n.history,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "web_research") {
      // No onDock here either (same reasoning as the html/image/conversation
      // branches above) - WebResearchNodeView never offers a dock-into-parent
      // action; the generic `if (n.isDocked) continue` guard above still
      // covers it correctly if it were ever docked via a direct WS call.
      // No isBranchFocusActive here either - see the html branch's own
      // comment above. isDimmed IS wired below as of ADR-012 stage 12.5 -
      // see the html branch's own comment for why that's safe.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(
        cache,
        n.id,
        { n, store, onOpenDocumentView, onToggleBranchFocus },
        makeWebResearchFns,
      );
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "web_research" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          query: n.content,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          researchStage: n.researchStage,
          researchCompleted: n.researchCompleted,
          researchTotal: n.researchTotal,
          researchActiveSourceId: n.researchActiveSourceId ?? null,
          researchError: n.researchError,
          researchResult: n.researchResult ?? null,
          researchRetainToKnowledge: n.researchRetainToKnowledge,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "plan") {
      // ADR-008 stage 8.3. No onDock (plan nodes are never docked) and no
      // branch-focus wiring - same reasoning as the web_research branch
      // above. isDimmed IS wired below as of ADR-012 stage 12.5 - see the
      // html branch's own comment for why that's safe. Every OTHER builder
      // field lives on the row itself, so a changed row object IS a cache
      // miss on its own - dimmedVal is folded into extraSig for the same
      // reason every other kind's own extraSig exists.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makePlanFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "plan" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          planGoal: n.planGoal,
          planSteps: n.planSteps,
          builderActivity: n.builderActivity,
          builderStatus: n.builderStatus,
          builderMode: n.builderMode,
          builderRunId: n.builderRunId,
          builderMaxSteps: n.builderMaxSteps,
          builderMaxTokens: n.builderMaxTokens,
          builderMaxWallSeconds: n.builderMaxWallSeconds,
          builderSpentSteps: n.builderSpentSteps,
          builderSpentTokens: n.builderSpentTokens,
          builderSpentWallSeconds: n.builderSpentWallSeconds,
          builderAwaitingToolApproval: n.builderAwaitingToolApproval,
          builderApprovalToolName: n.builderApprovalToolName,
          builderApprovalSummary: n.builderApprovalSummary,
          builderStatusDetail: n.builderStatusDetail,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "harness") {
      // PLAN-2026-08-24 H1. Same shape as the plan branch above: no
      // onDock, no branch-focus wiring, every harness field lives on the
      // row itself so a changed row object IS a cache miss; only
      // dimmedVal rides extraSig.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeHarnessFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "harness" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          harnessGoal: n.harnessGoal,
          harnessReply: n.harnessReply,
          harnessStatus: n.harnessStatus,
          harnessStatusDetail: n.harnessStatusDetail,
          harnessRunId: n.harnessRunId,
          harnessActivity: n.harnessActivity,
          harnessContextTokens: n.harnessContextTokens,
          harnessMaxContextTokens: n.harnessMaxContextTokens,
          harnessCompactions: n.harnessCompactions,
          harnessWorkspacePath: n.harnessWorkspacePath,
          harnessWorkspaceActive: n.harnessWorkspaceActive,
          harnessAwaitingApproval: n.harnessAwaitingApproval,
          harnessApprovalToolName: n.harnessApprovalToolName,
          harnessApprovalSummary: n.harnessApprovalSummary,
          harnessApprovalSessionOffered: n.harnessApprovalSessionOffered,
          harnessPlan: n.harnessPlan,
          harnessAwaitingQuestion: n.harnessAwaitingQuestion,
          harnessQuestion: n.harnessQuestion,
          harnessMaxTurns: n.harnessMaxTurns,
          harnessSpentTurns: n.harnessSpentTurns,
          harnessSpentTokens: n.harnessSpentTokens,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "artifact") {
      // No onDock here either (same reasoning as the html/image/conversation/
      // web_research branches above) - ArtifactNodeView never offers a
      // dock-into-parent action; the generic `if (n.isDocked) continue` guard
      // above still covers it correctly if it were ever docked via a direct
      // WS call. No isBranchFocusActive here either - see the html branch's
      // own comment above. isDimmed IS wired below as of ADR-012 stage 12.5 -
      // see that same comment for why that's safe.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeArtifactFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "artifact" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          artifactContent: n.artifactContent,
          history: n.history,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "gitlink") {
      // No onDock here either (same reasoning as the html/image/conversation/
      // web_research/artifact branches above) - GitlinkNodeView never offers
      // a dock-into-parent action; the generic `if (n.isDocked) continue`
      // guard above still covers it correctly if it were ever docked via a
      // direct WS call. gitlinkContextXml is deliberately absent below - it
      // is never part of the scene wire payload (fetched lazily on demand via
      // fetchGitlinkContext instead - see GitlinkNodeView's own Context tab).
      // No isBranchFocusActive here either. isDimmed IS wired below as of
      // ADR-012 stage 12.5 - see the html branch's own comment for why
      // that's safe.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeGitlinkFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "gitlink" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          gitlinkRepo: n.gitlinkRepo,
          gitlinkBranch: n.gitlinkBranch,
          gitlinkScopeMode: n.gitlinkScopeMode,
          gitlinkLocalRoot: n.gitlinkLocalRoot,
          gitlinkRepoFilePaths: n.gitlinkRepoFilePaths,
          gitlinkSelectedPaths: n.gitlinkSelectedPaths,
          gitlinkTaskPrompt: n.gitlinkTaskPrompt,
          gitlinkContextStats: n.gitlinkContextStats,
          gitlinkContextSummary: n.gitlinkContextSummary,
          gitlinkContextVersion: n.gitlinkContextVersion,
          gitlinkProposalMarkdown: n.gitlinkProposalMarkdown,
          gitlinkPendingChanges: n.gitlinkPendingChanges,
          gitlinkPreviewText: n.gitlinkPreviewText,
          gitlinkChangeFingerprint: n.gitlinkChangeFingerprint ?? null,
          gitlinkChangeState: n.gitlinkChangeState,
          gitlinkError: n.gitlinkError,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "code_sandbox") {
      // No onDock here either (same reasoning as every non-dockable R5
      // plugin-node branch above) - CodeSandboxNodeView never offers a
      // dock-into-parent action; the generic `if (n.isDocked) continue`
      // guard above still covers it correctly if it were ever docked via a
      // direct WS call. code_sandbox_sandbox_id is deliberately absent below
      // - it is pure internal server bookkeeping (a sandbox directory name),
      // never part of the scene wire payload at all (see scene-state.ts) and
      // never read/forwarded anywhere in this mapping. No isBranchFocusActive
      // here either. isDimmed IS wired below as of ADR-012 stage 12.5 - see
      // the html branch's own comment for why that's safe.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(
        cache,
        n.id,
        { n, store, onOpenDocumentView, onToggleBranchFocus },
        makeCodeSandboxFns,
      );
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "code_sandbox" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          codeSandboxRequirements: n.codeSandboxRequirements,
          codeSandboxApprovalRequirements: n.codeSandboxApprovalRequirements,
          codeSandboxApprovalAllowSourceBuilds: n.codeSandboxApprovalAllowSourceBuilds,
          codeSandboxApprovalIsRepair: n.codeSandboxApprovalIsRepair,
          codeSandboxPrompt: n.codeSandboxPrompt,
          codeSandboxCode: n.codeSandboxCode,
          codeSandboxOutput: n.codeSandboxOutput,
          codeSandboxAnalysis: n.codeSandboxAnalysis,
          codeSandboxAwaitingApproval: n.codeSandboxAwaitingApproval,
          codeSandboxError: n.codeSandboxError,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "note") {
      // No onDock here either (same reasoning as every non-dockable kind
      // above) - a note never offers a dock-into-parent action of its own;
      // the generic `if (n.isDocked) continue` guard above still covers it
      // correctly if it were ever docked via a direct WS call. No
      // isBranchFocusActive here either. isDimmed IS wired below as of
      // ADR-012 stage 12.5 - see the html branch's own comment for why
      // that's safe. NodeShell.tsx's own note-migration comment notes this
      // kind hardcodes collapsed=false (no LOD posture of its own) - the
      // filter's own opacity dimming is unaffected by that, since it's
      // driven by this style prop, not the collapsed gate.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeNoteFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "note" as const,
        position: { x: n.x, y: n.y },
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          content: n.content,
          color: n.color ?? null,
          headerColor: n.headerColor ?? null,
          isSystemPrompt: n.isSystemPrompt,
          isSummaryNote: n.isSummaryNote,
          // ADR-002 Workstream 1: itemIds doubles as the source branch ids
          // for a Compare Branches result note - see SceneNode.item_ids's
          // own comment on backend/domain/model.py.
          isBranchComparison: n.isBranchComparison,
          compareSourceNodeIds: n.itemIds,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "frame" || n.kind === "container") {
      // R6.1/R6.1 follow-up: rendered BEHIND every other node (zIndex < 0 -
      // React Flow paints in ascending zIndex order) since members are
      // ordinary top-level flow nodes at their own absolute positions,
      // never React Flow parentId/extent children of this node - see
      // GroupNodeView.tsx's own module doc for why that is the deliberate,
      // simpler equivalent of legacy's "auto-grow to enclose, never clip"
      // behavior. Container sits BEHIND frame (-2 vs -1), matching
      // legacy's own relative z-ordering (Frame=-2, Container=-3 there) -
      // container membership can nest a frame inside it (create_container
      // has no kind restriction, unlike create_frame), so without this
      // distinction a nested frame would have undefined stacking against
      // its own parent container's background.
      //
      // width/height are set on the FLOW NODE OBJECT itself (not just inside
      // `data`) - the documented xyflow mechanism that lets <NodeResizer/>
      // (frame only) drive the node wrapper's own size directly; see
      // GroupNodeView.tsx's own doc for the fuller reasoning. Fallback to
      // GROUP_FALLBACK_WIDTH/HEIGHT covers the (should-be-unreachable, see
      // canvasConstants.ts) case of a null groupWidth/groupHeight.
      //
      // draggable: always true now (R6.1 follow-up restores legacy's own
      // "an unlocked frame can still be dragged independently of its
      // members" behavior - see groupDragKindOf/applyGroupDragDelta below,
      // which gate the MEMBER-cascade on lock state, not draggability
      // itself; backend/canvas.py's move_node pins a manual position
      // anchor so the drag actually sticks instead of snapping back).
      // R6.1 follow-up: a simplified equivalent of legacy's collapsed-
      // container hover "ghost frame" preview - just member kinds, not a
      // rendered miniature of actual content. Looked up from the SAME
      // nodesById map the dockedChildren computation above already builds
      // once per call; a stale/dangling item_ids entry (a member deleted
      // out from under a group) is silently skipped, matching
      // _bbox_of_members' own posture on the backend. Doubles as this
      // kind's own cache-invalidation signature below: a member's kind
      // never changes after creation, so memberKinds only differs when
      // n.itemIds itself differs (already covered by node-reference
      // invalidation) OR a referenced member was deleted out from under the
      // group WITHOUT n.itemIds being pruned server-side (the exact
      // "dangling entry" case this comment already documents) - the one
      // case node-reference-only invalidation would otherwise miss.
      const memberKinds = n.itemIds.map((id) => nodesById.get(id)?.kind).filter((kind): kind is string => !!kind);
      const extraSig = memberKinds.join(",");
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeGroupFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: n.kind as "frame" | "container",
        position: { x: n.x, y: n.y },
        width: n.groupWidth ?? GROUP_FALLBACK_WIDTH,
        height: n.groupHeight ?? GROUP_FALLBACK_HEIGHT,
        zIndex: n.kind === "container" ? -2 : -1,
        draggable: true,
        data: {
          groupKind: n.kind,
          label: n.content,
          color: n.color ?? null,
          headerColor: n.headerColor ?? null,
          isCollapsed: n.isCollapsed,
          isLocked: n.isLocked,
          itemIds: n.itemIds,
          memberKinds,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    if (n.kind === "chart") {
      // No onDock here either (same reasoning as every non-dockable R3/R5
      // content-card branch above) - ChartNodeView never offers a
      // dock-into-parent action; the generic `if (n.isDocked) continue`
      // guard above still covers it correctly if it were ever docked via a
      // direct WS call. width/height are ALSO set on the flow node object
      // itself here (not just inside `data`) - the same NodeResizer
      // controlled-mode technique the frame/container branch above uses
      // (see GroupNodeView.tsx's own doc), needed because ChartNodeView is
      // resizable too. Unlike frame/container though, chartWidth/chartHeight
      // are part of the ordinary 9-field wire contract (scene_payload()
      // exposes them like every other chart field), so they're ALSO
      // duplicated into `data` below rather than living only on the flow
      // node object. No isBranchFocusActive here either. isDimmed IS wired
      // below as of ADR-012 stage 12.5 - see the html branch's own comment
      // for why that's safe; this flow-node-level `style` is React Flow's
      // own per-node styling (opacity only) and is unrelated to
      // ChartNodeView.tsx's own internal 100%/100% fill style on its inner
      // wrapper div, so the two never conflict.
      const dimmedVal = isDimmed(n.id);
      const extraSig = dimmedVal ? "1" : "0";
      const cached = cache.flowNodes.get(n);
      const fns = getDispatcher(cache, n.id, { n, store, onOpenDocumentView, onToggleBranchFocus }, makeChartFns);
      if (cached && cached.extraSig === extraSig) {
        flowNodes.push(cached.flowNode);
        continue;
      }
      const flowNode: SceneFlowNode = {
        id: n.id,
        type: "chart" as const,
        position: { x: n.x, y: n.y },
        width: n.chartWidth,
        height: n.chartHeight,
        style: dimmedVal ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          chartType: n.chartType,
          chartData: n.chartData,
          chartError: n.chartError,
          chartWidth: n.chartWidth,
          chartHeight: n.chartHeight,
          chartAspectLocked: n.chartAspectLocked,
          chartSourceNodeId: n.chartSourceNodeId,
          ...fns,
        },
      };
      cache.flowNodes.set(n, { extraSig, flowNode });
      flowNodes.push(flowNode);
      continue;
    }
    // Fallback placeholder - unreachable in practice given the exhaustive
    // kind list above, kept for forward-compat with a future kind this
    // switch hasn't been taught yet. No callbacks at all, so no dispatcher
    // is needed; node-reference-only invalidation is exact (title is n's
    // own field).
    const cachedPlaceholder = cache.flowNodes.get(n);
    if (cachedPlaceholder) {
      flowNodes.push(cachedPlaceholder.flowNode);
      continue;
    }
    const placeholderFlowNode: SceneFlowNode = {
      id: n.id,
      type: "placeholder" as const,
      position: { x: n.x, y: n.y },
      data: { title: n.title },
    };
    cache.flowNodes.set(n, { extraSig: "", flowNode: placeholderFlowNode });
    flowNodes.push(placeholderFlowNode);
  }

  pruneDispatcherCache(cache, nodesById);
  return flowNodes;
}

// R5.1: the onSelectionChange callback's actual logic, pulled out standalone
// for direct unit testing (same posture as toFlowNodes
// above) - a full <ReactFlow> mount's own drag-select interaction isn't
// something this codebase drives in tests anywhere else, so this is what
// gets covered instead of the mount.
export function handleSelectionChange(store: SceneStore, nodes: { id: string }[]): void {
  store.setSelectedNodeId(nodes[0]?.id ?? null);
}

// R6.1: whether `node`'s own drag should carry its itemIds members along -
// true for any container (no lock concept, always drags as a group) or a
// LOCKED frame; false for everything else (including an unlocked frame,
// which is non-draggable anyway - see toFlowNodes' draggable: setting
// above). Exported standalone, same testability convention as
// toFlowNodes/handleSelectionChange above.
export function groupDragKindOf(node: SceneFlowNode | undefined): "frame" | "container" | null {
  if (!node) return null;
  if (node.type === "container") return "container";
  if (node.type === "frame" && (node.data as { isLocked?: boolean }).isLocked) return "frame";
  return null;
}

// R6.1: the group-drag delta-application core, pulled out of onNodesChange's
// closure for the same reason - a direct unit test can call this without a
// full <ReactFlow> mount + synthetic drag simulation. `nodes` is the PRE-this-
// change local flow-node array; `scaledPosition` is the dragged node's own
// new (already drag-speed-scaled) target position for this tick. Returns one
// synthetic "position" NodeChange per live member, carrying it by the exact
// same delta the dragged group node itself is about to move by.
export function applyGroupDragDelta(
  nodes: SceneFlowNode[],
  draggedId: string,
  scaledPosition: { x: number; y: number },
): NodeChange<SceneFlowNode>[] {
  const draggedNode = nodes.find((n) => n.id === draggedId);
  if (!groupDragKindOf(draggedNode) || !draggedNode) return [];
  const deltaX = scaledPosition.x - draggedNode.position.x;
  const deltaY = scaledPosition.y - draggedNode.position.y;
  return collectGroupMemberDeltaChanges(nodes, draggedNode, deltaX, deltaY, new Set([draggedId]));
}

// R6.1 follow-up: recurses into any member that is itself a group (a
// container nesting a frame or another container - create_container has
// no kind restriction, unlike create_frame - see backend/canvas.py's own
// docstrings), so dragging the OUTER group carries the FULL nested tree,
// not just its direct itemIds. Without this, an inner group's own
// members stayed put while only the inner group's single bounding node
// moved with the outer drag, visibly desyncing it from its own contents -
// a real, reachable bug given nesting is legitimately possible today.
// `visited` is a defensive cycle guard (creation-time validation should
// make a cycle unreachable, but this must never infinite-loop even if one
// existed) and also prevents re-visiting the node being dragged itself.
function collectGroupMemberDeltaChanges(
  nodes: SceneFlowNode[],
  groupNode: SceneFlowNode,
  deltaX: number,
  deltaY: number,
  visited: Set<string>,
): NodeChange<SceneFlowNode>[] {
  const itemIds = (groupNode.data as { itemIds?: string[] }).itemIds ?? [];
  const changes: NodeChange<SceneFlowNode>[] = [];
  for (const memberId of itemIds) {
    if (visited.has(memberId)) continue;
    const member = nodes.find((n) => n.id === memberId);
    if (!member) continue;
    visited.add(memberId);
    changes.push({
      id: memberId,
      type: "position",
      dragging: true,
      position: { x: member.position.x + deltaX, y: member.position.y + deltaY },
    });
    if (member.type === "frame" || member.type === "container") {
      changes.push(...collectGroupMemberDeltaChanges(nodes, member, deltaX, deltaY, visited));
    }
  }
  return changes;
}

// R6.1 follow-up: the id-only counterpart to collectGroupMemberDeltaChanges
// above, for the two call sites that need "every node this group drag
// carries along, transitively" without also computing a position delta -
// the smart-guide exclusion set (a carried member must never be an
// alignment candidate for the group dragging it) and the drag-end commit
// loop (every carried member's SETTLED position must be persisted via
// moveNode, not just the outer group's directly-listed itemIds - without
// this, a nested group's own members would visually follow the drag but
// snap back the moment the scene re-syncs, since their moved position was
// never actually committed).
// Exported (Qt-removal decomposition, hook extraction): read directly by
// useNodeDragAndSizeSync.ts's onNodesChange, which used to be a closure in
// this same module - see that hook's own doc.
export function collectTransitiveMemberIds(
  nodes: SceneFlowNode[],
  groupNode: SceneFlowNode,
  visited: Set<string> = new Set(),
): Set<string> {
  const itemIds = (groupNode.data as { itemIds?: string[] }).itemIds ?? [];
  for (const memberId of itemIds) {
    if (visited.has(memberId)) continue;
    visited.add(memberId);
    const member = nodes.find((n) => n.id === memberId);
    if (member && (member.type === "frame" || member.type === "container")) {
      collectTransitiveMemberIds(nodes, member, visited);
    }
  }
  return visited;
}

// R7.5b-1: legacy's exact faded-connections opacity (graphlink_connections.py's
// _sync_connection_visibility_mode) - every connection sits at this opacity
// except the one under the mouse, once the feature is toggled on. Legacy also
// had a `is_selected` stay-bright branch, but the recon confirmed it's dead
// code (never set True anywhere in the Qt codebase) - deliberately not ported.
const FADED_CONNECTION_OPACITY = 0.08;

// R7.5b-2: which node-kind pairs get the orthogonal step path when the
// toggle is on - derived by mapping each legacy *ConnectionItem subclass onto
// this app's node-kind vocabulary (see OrthogonalEdge.tsx's own module doc
// for the path-shape translation). Exported standalone for direct unit
// testing, same posture as toFlowEdges/toFlowNodes.
//
// - source kind "note" -> never eligible (legacy SystemPromptConnectionItem:
//   always a fixed Bezier, regardless of the toggle).
// - target kind in {code, document, image, thinking} -> never eligible
//   (legacy ContentConnectionItem/DocumentConnectionItem/ImageConnectionItem/
//   ThinkingConnectionItem: always straight lines).
// - target kind in {chat, conversation, html} -> eligible (legacy
//   ConnectionItem/ConversationConnectionItem/HtmlConnectionItem, which all
//   share the ortho-gated update_path).
// - anything else (web_research/artifact/gitlink/code_sandbox/harness/frame/
//   container/chart/note-as-target) -> defaulted NOT eligible - these node
//   kinds didn't exist as distinct connection types in the legacy app, so
//   there is no research-backed mapping for them (an explicit, documented
//   default, not a silent omission).
const ORTHO_INELIGIBLE_TARGET_KINDS = new Set(["code", "document", "image", "thinking"]);
const ORTHO_ELIGIBLE_TARGET_KINDS = new Set(["chat", "conversation", "html"]);

export function isOrthogonalEligible(sourceKind: string | undefined, targetKind: string | undefined): boolean {
  if (sourceKind === "note") return false;
  if (targetKind === undefined) return false;
  if (ORTHO_INELIGIBLE_TARGET_KINDS.has(targetKind)) return false;
  return ORTHO_ELIGIBLE_TARGET_KINDS.has(targetKind);
}

// R7.5b-3: one node's current rendered size in flow units, for smart-guide
// rects - see the comment on CanvasInner's reactFlow hoist for why the
// internal store alone is not enough and the DOM is the reliable fallback.
// Exported since R7.5c: App.tsx's Ctrl+Arrow navigation has to center on a
// node's MIDDLE, and hit the exact same empty-`measured` trap this solves.
export type MeasuredSizeSource = { getInternalNode: (id: string) => { measured?: { width?: number; height?: number } } | undefined };
export function measuredNodeSize(reactFlow: MeasuredSizeSource, id: string): { width: number; height: number } | null {
  const measured = reactFlow.getInternalNode(id)?.measured;
  if (measured?.width !== undefined && measured?.height !== undefined) {
    return { width: measured.width, height: measured.height };
  }
  const el = document.querySelector(`.react-flow__node[data-id="${CSS.escape(id)}"]`);
  if (!(el instanceof HTMLElement) || el.offsetWidth === 0) return null;
  return { width: el.offsetWidth, height: el.offsetHeight };
}

/**
 * ADR-011 stage 11.2: the virtualization-audit fallback for a smart-guide
 * alignment CANDIDATE that `onlyRenderVisibleElements` (see CanvasInner's
 * own `<ReactFlow>` below) has left unmounted - measuredNodeSize's own
 * getInternalNode/DOM reads both come up empty for a node that was never
 * rendered in the first place, since there is nothing to measure.
 *
 * Three of SceneFlowNode's kinds are the one exception: toFlowNodes above
 * sets `width`/`height` DIRECTLY ON THE FLOW NODE OBJECT for frame/
 * container (`n.groupWidth`/`n.groupHeight`) and chart (`n.chartWidth`/
 * `n.chartHeight`) - the documented xyflow controlled-size mechanism their
 * own <NodeResizer/> needs (see GroupNodeView.tsx's/ChartNodeView.tsx's own
 * module docs on those branches). That is DATA carried on the node object
 * itself, not a DOM measurement - present and correct regardless of mount
 * state. Confirmed against backend/domain/graph.py's scene_payload(), which
 * is the server-side source those four fields come from (groupWidth/
 * groupHeight from FrameState/ContainerState, chartWidth/chartHeight from
 * ChartState) - the one place in this app's domain model that tracks a
 * node's size server-side at all.
 *
 * Every other kind's size is purely content-driven (CSS auto-sized, no
 * backend field for it - the exact reason measuredNodeSize's DOM fallback
 * existed in the first place), so this returns null for them and the
 * caller (buildDragSizeCache below) simply omits that node from the cache -
 * the sane fallback the audit called for: an off-viewport, unmeasurable
 * candidate is excluded from alignment for that drag, never force-mounted
 * or estimated.
 */
export function flowNodeOwnSize(node: SceneFlowNode): { width: number; height: number } | null {
  if (typeof node.width === "number" && typeof node.height === "number") {
    return { width: node.width, height: node.height };
  }
  return null;
}

/**
 * ADR-011 stage 11.3: the drag-GESTURE-lifetime smart-guide size cache -
 * built exactly ONCE, at the first frame of a NEW drag gesture (see
 * CanvasInner's own dragSizeCacheRef and the `startingNewDrag` check in
 * onNodesChange), never per frame. This is the fix for P3 in ADR-011's own
 * audit: measuredNodeSize's DOM fallback (`document.querySelector` +
 * `offsetWidth`/`offsetHeight`, a forced reflow) used to run for the
 * dragged node AND every candidate node INSIDE onNodesChange's per-frame
 * loop - up to N reflows PER DRAG FRAME, ~6,000/s at 100 nodes and 60fps.
 * Now it runs at most once per node for the WHOLE gesture; every later
 * frame of that same gesture reads back out of the Map this returns
 * (computeSmartGuideFrame below) instead of touching the DOM again.
 *
 * flowNodeOwnSize is tried first (free, DOM-independent); measuredNodeSize
 * is the fallback for every other kind. A node with neither (off-viewport
 * and unmounted under onlyRenderVisibleElements, no server-tracked size) is
 * simply absent from the returned map - see computeSmartGuideFrame's own
 * `if (!size) continue`.
 */
export function buildDragSizeCache(
  reactFlow: MeasuredSizeSource,
  nodes: SceneFlowNode[],
): Map<string, { width: number; height: number }> {
  const cache = new Map<string, { width: number; height: number }>();
  for (const n of nodes) {
    const size = flowNodeOwnSize(n) ?? measuredNodeSize(reactFlow, n.id);
    if (size) cache.set(n.id, size);
  }
  return cache;
}

/**
 * R7.5b-3/ADR-011 stage 11.3: one drag frame's smart-guide snap
 * computation, pulled out of onNodesChange's closure - callable directly
 * (same standalone-for-direct-testing posture as applyGroupDragDelta/
 * groupDragKindOf above, whose own doc explains why: driving a full
 * <ReactFlow> mount + synthetic pointer drag isn't something this codebase
 * exercises in tests anywhere else) so a test can drive N simulated frames
 * without one.
 *
 * `sizeCache` is read-only here - see buildDragSizeCache above for where it
 * gets populated (once, at drag start) and CanvasInner's dragSizeCacheRef
 * for the ref it lives in across a gesture's many onNodesChange calls. A
 * moving node absent from the cache (no resolvable size) is a no-op frame:
 * the ORIGINAL (unsnapped) position passes through unchanged and no guides
 * are produced, same as measuredNodeSize returning null did before this
 * stage.
 */
export function computeSmartGuideFrame(
  nodes: SceneFlowNode[],
  changeId: string,
  finalPosition: { x: number; y: number },
  sizeCache: Map<string, { width: number; height: number }>,
): { position: { x: number; y: number }; guides: GuideLine[] } {
  const moving = nodes.find((n) => n.id === changeId);
  const movingSize = sizeCache.get(changeId);
  if (!moving || !movingSize) return { position: finalPosition, guides: [] };
  const memberIds = groupDragKindOf(moving) ? collectTransitiveMemberIds(nodes, moving) : new Set<string>();
  const candidates: Rect[] = [];
  for (const n of nodes) {
    if (n.id === changeId || n.selected || memberIds.has(n.id)) continue;
    const size = sizeCache.get(n.id);
    if (!size) continue;
    candidates.push({ x: n.position.x, y: n.position.y, width: size.width, height: size.height });
  }
  const snap = computeSmartGuideSnap(
    { x: finalPosition.x, y: finalPosition.y, width: movingSize.width, height: movingSize.height },
    candidates,
  );
  return { position: { x: snap.x, y: snap.y }, guides: snap.guides };
}

/**
 * R7.5c (extended): carry React Flow's own per-node runtime state - the
 * selection flag AND the measured node size - across a snapshot rebuild.
 *
 * toFlowNodes mints brand-new node objects from every scene snapshot, so
 * anything React Flow keeps on the node object rather than in the scene is
 * dropped unless copied over explicitly. Two fields matter:
 *
 * SELECTION (the original R7.5c fix): found live, not by a test - Ctrl+Arrow
 * calls setCenter, setCenter fires onMove, onMove reports the viewport to
 * the backend, the backend echoes a fresh scene, and the selection the
 * keystroke had just made vanished about 300ms later. That left the NEXT
 * Ctrl+Arrow with no single selected node, so branch-walking died after
 * exactly one hop.
 *
 * MEASURED SIZE (the node/edge blink fix): React Flow writes each node's
 * measured dimensions back onto OUR node objects through ordinary
 * `dimensions` changes (applyNodeChanges' own 'dimensions' case sets
 * `node.measured`), and its adoptUserNodes preserves a node's internal
 * measurement/handle geometry across a nodes-prop change ONLY IF the
 * incoming node object either is reference-identical to the last one or
 * still carries that `measured` field (verified against the installed
 * @xyflow/system source: adoptUserNodes rebuilds internals for any
 * new-reference node, taking `measured` from the user object - undefined
 * for a fresh toFlowNodes product - and parseHandles keeps the previous
 * handleBounds only when `userNode.measured` is set). A node whose
 * measurement gets wiped this way is re-rendered with `visibility: hidden`
 * (NodeWrapper's own `hasDimensions` gate) and every edge touching it
 * unmounts entirely (getEdgePosition returns null for an uninitialized
 * node) until the resize-observer cycle re-measures it a frame or more
 * later. Since every scene publish rebuilds the changed rows' node objects
 * (and this function's own selection clone used to mint a fresh object for
 * every SELECTED node on every publish), the user-visible result was nodes
 * and their connections blinking for a frame after every drag drop,
 * viewport echo, and streaming patch. Carrying `measured` over closes the
 * whole chain: dimensions survive, handleBounds survive, nothing unmounts.
 *
 * Reference-identical nodes (a toFlowNodes cache hit that is literally the
 * same object React Flow already adopted) pass through untouched - cloning
 * them would defeat adoptUserNodes' reference-equality fast path for no
 * benefit. A node the backend actually removed is simply absent from
 * `rebuilt`, so a stale id can never resurrect one; a genuinely NEW node
 * has no prior state to carry and measures normally on first mount.
 */
export function withPreservedFlowState(
  rebuilt: SceneFlowNode[],
  current: SceneFlowNode[],
): SceneFlowNode[] {
  if (current.length === 0) return rebuilt;
  const currentById = new Map(current.map((n) => [n.id, n]));
  let changed = false;
  const merged = rebuilt.map((n) => {
    const prev = currentById.get(n.id);
    if (!prev || prev === n) return n;
    const selected = prev.selected === true;
    const measured = n.measured === undefined ? prev.measured : undefined;
    if (!selected && measured === undefined) return n;
    changed = true;
    const clone: SceneFlowNode = { ...n };
    if (selected) clone.selected = true;
    if (measured !== undefined) clone.measured = measured;
    return clone;
  });
  return changed ? merged : rebuilt;
}

// Exported standalone for direct unit testing, same posture as toFlowNodes
// above - R7.5b-1's fade-connections opacity logic doesn't need a mounted
// <ReactFlow> to verify.
export function toFlowEdges(scene: SceneState, hoveredEdgeId: string | null): Edge[] {
  // ADR-011 stage 11.1: dockedNodeIds now comes from the SAME
  // buildSceneIndexes pass toFlowNodes above uses (was its own standalone
  // `scene.nodes.filter(...)` here) - kindOf is looked up straight off
  // nodesById's own node rows instead of a second separate `Map(scene.nodes
  // .map((n) => [n.id, n.kind]))`, one less O(N) map built per call.
  const { nodesById, dockedNodeIds } = buildSceneIndexes(scene);
  // An edge pointing at a docked node must not render either - mirrors the
  // legacy connection-item self-suppression when its end node is docked.
  return scene.edges
    .filter((e) => !dockedNodeIds.has(e.target))
    .map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type:
        scene.orthogonalRouting &&
        isOrthogonalEligible(nodesById.get(e.source)?.kind, nodesById.get(e.target)?.kind)
          ? "orthogonal"
          : undefined,
      ...(scene.fadeConnectionsEnabled && e.id !== hoveredEdgeId
        ? { style: { opacity: FADED_CONNECTION_OPACITY } }
        : {}),
    }));
}

// R6.3: the debounce wrapper for viewport (pan/zoom) reporting - same posture
// as ChartNodeView.tsx's makeDebouncedChartResize (a plain clearTimeout/
// setTimeout box keyed off the caller's own timerRef, so debounce state
// survives across repeated calls without this function owning any React
// state itself), exported standalone for direct unit testing without
// mounting a real <ReactFlow> pan/zoom gesture - the same testability
// posture as toFlowNodes/handleSelectionChange above.
export function makeDebouncedViewportReport(
  timerRef: { current: ReturnType<typeof setTimeout> | null },
  onReport: (zoomFactor: number, scrollX: number, scrollY: number) => void,
  debounceMs: number = VIEWPORT_REPORT_DEBOUNCE_MS,
): (zoomFactor: number, scrollX: number, scrollY: number) => void {
  return (zoomFactor, scrollX, scrollY) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onReport(zoomFactor, scrollX, scrollY);
    }, debounceMs);
  };
}

/**
 * The diff half of node-size reporting: given the ids currently on canvas,
 * return only those whose rendered size differs from what was last sent,
 * updating `lastReported` in place as it goes.
 *
 * Diffing is what makes this cheap enough to run on every re-measure. A
 * streaming reply re-measures its node constantly but its WIDTH never
 * moves and its height stops changing the moment the text settles, so the
 * steady state is an empty array and no intent at all.
 *
 * Nodes that cannot be measured are SKIPPED, never reported as zero: with
 * `onlyRenderVisibleElements` on, an off-viewport node is genuinely
 * unmounted and unmeasurable, and the backend's last known size for it is
 * better than a zero that would collapse its group's box. For the same
 * reason this never removes ids from `lastReported` - a node that scrolls
 * away and back has not changed size, so it should not re-report.
 *
 * Exported for direct unit testing, same posture as makeDebouncedViewport-
 * Report/toFlowNodes above.
 */
export function collectChangedNodeSizes(
  ids: string[],
  measure: (id: string) => { width: number; height: number } | null,
  lastReported: Map<string, string>,
): Array<[string, number, number]> {
  const changed: Array<[string, number, number]> = [];
  for (const id of ids) {
    const size = measure(id);
    if (size === null || size.width <= 0 || size.height <= 0) continue;
    const key = `${size.width}x${size.height}`;
    if (lastReported.get(id) === key) continue;
    lastReported.set(id, key);
    changed.push([id, size.width, size.height]);
  }
  return changed;
}

// R8a: reads a real design-token value at render time rather than
// hardcoding a hex literal - needed anywhere a color has to be a plain JS
// string (an SVG-attribute-producing prop like MiniMap's nodeColor/
// nodeStrokeColor), not a CSS declaration value or a style={{}} block, so
// it falls outside what the no-raw-colors lint gate can enforce for us.
function useCssVar(name: string, fallback: string): string {
  // A lazy initializer, not an effect: the token's value is static for the
  // component's lifetime (no live theme-switching exists yet), so reading
  // it once during the initial render avoids both a spurious extra render
  // AND a one-frame flash of `fallback` before the real value lands.
  const [value] = useState(() => {
    const computed = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return computed || fallback;
  });
  return value;
}

function CanvasInner({
  store,
  onOpenDocumentView,
  getComposerRoute,
}: {
  store: SceneStore;
  onOpenDocumentView: (markdown: string, sourceLabel: string) => void;
  // ADR-018 stage 18.3: passed straight through to toFlowNodes - see that
  // function's own comment on why a getter, not a subscribed value.
  getComposerRoute: () => { provider: string; modelId: string };
}) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  // Live mirror for event handlers that must read current scene values
  // (the pan handler's drag factor) without re-registering per publish.
  const sceneRef = useRef(scene);
  useEffect(() => {
    sceneRef.current = scene;
  }, [scene]);
  const grid = useSyncExternalStore(store.subscribe, store.getGrid);
  // ADR-002 Workstream 1 ("Branch status and lifecycle") - "Focus Accepted
  // Paths", toggled from ViewPopover.tsx's own checkbox (a sibling
  // component, hence living on sceneStore rather than as useState here -
  // see that field's own comment).
  const focusAcceptedPaths = useSyncExternalStore(store.subscribe, store.getFocusAcceptedPaths);
  // ADR-012 stage 12.5: the FILTER section's own two toggle groups, same
  // "sibling component, hence living on sceneStore" reasoning as
  // focusAcceptedPaths just above.
  const filterKinds = useSyncExternalStore(store.subscribe, store.getFilterKinds);
  const filterStatuses = useSyncExternalStore(store.subscribe, store.getFilterStatuses);
  // ADR-011 stage 11.2: true for exactly the duration of one
  // exportCanvasAsPng capture (AppBar.tsx's exportPng/commands.ts's
  // export-canvas-png, both routed through SceneStore.setExportInProgress) -
  // see exportCanvasPng.ts's own doc for why a full-canvas PNG export needs
  // onlyRenderVisibleElements suspended: the export computes a viewport that
  // fits every node into a 1920x1080 FRAME, but that framing has no way to
  // know the LIVE on-screen canvas container might be smaller than that, and
  // virtualization filters against the container's REAL client size - a node
  // that fits inside the export frame can still fall outside the live
  // container's actual bounds and never mount, silently missing from the
  // captured DOM regardless of correct viewport math.
  const exportInProgress = useSyncExternalStore(store.subscribe, store.getExportInProgress);
  // Hoisted above onNodesChange: smart guides (R7.5b-3) need node
  // dimensions. Neither the local `nodes` array NOR React Flow's internal
  // store reliably has them here: toFlowNodes rebuilds the array from every
  // scene snapshot with fresh objects (no `measured`), and RF's own
  // adoptUserNodes then rebuilds its internal `measured` FROM those user
  // objects (verified against the installed @xyflow/system source, and
  // confirmed live: `getInternalNode(...).measured` was {} at drag time), so
  // both copies get wiped on every publish. measuredNodeSize below prefers
  // the internal store when populated and falls back to the live DOM
  // element's layout size - offsetWidth/Height are pre-transform layout px
  // (flow units at any zoom), and reading the live rendered rect is also the
  // most faithful translation of legacy's own sceneBoundingRect() reads.
  const reactFlow = useReactFlow();

  // Local node state exists so dragging is fluid; backend snapshots are the
  // truth and reconcile in whenever nothing is being dragged. React Flow
  // owns the rendered node collection (see the <ReactFlow> element's own
  // defaultNodes comment). This component keeps only a mirror ref for its
  // own logic - drag corrections, delete routing, scene merge - so a drag
  // frame never has to travel through React state to reach the renderer.
  const storeApi = useStoreApi();

  // ADR-011 stage 11.1: ONE ToFlowNodesCache for this canvas's whole
  // lifetime, threaded into every toFlowNodes call below - this is what
  // actually makes the per-node dispatcher/whole-flow-node memoization in
  // toFlowNodes effective across snapshots (a cache recreated every call
  // would never see a previous call's entries to hit against). Lazy-
  // initialized via the guarded-null pattern rather than
  // `useRef(createToFlowNodesCache())` so a fresh cache object isn't
  // allocated (and immediately discarded) on every re-render.
  const toFlowNodesCacheRef = useRef<ToFlowNodesCache | null>(null);
  if (toFlowNodesCacheRef.current === null) toFlowNodesCacheRef.current = createToFlowNodesCache();

  // R7.5b-1: which edge (if any) is under the mouse right now - the one
  // exemption from faded-connections' blanket low-opacity effect. Local-only
  // (never scene state), same posture as legacy's purely presentational hover
  // flag.
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);

  // DRAG-SYNC REBUILD: node-drag position correction (smart guides + the
  // group-member cascade), drag/active-gesture tracking, and node-size
  // reporting back to the backend - one cohesive, already-interdependent
  // subsystem, and the most safety-critical piece of this canvas. See the
  // hook's own module doc for why it moved as a single unit.
  const { nodesRef, draggingRef, dragActive, smartGuideLines, onNodesChange, onDelete } = useNodeDragAndSizeSync(
    scene,
    reactFlow,
    store,
  );
  const visibleGuideLines = scene.smartGuides ? smartGuideLines : [];

  // R8a (UI/UX issue list finding #11): the View popover's FONT section
  // (family/size/color), round-tripped into CSS custom properties on the
  // canvas wrapper - see the hook's own doc for why.
  const canvasWrapperRef = useRef<HTMLDivElement | null>(null);
  useCanvasFontVars(canvasWrapperRef, scene.fontFamily, scene.fontSizePt, scene.fontColor);

  // R8a: "Hide Other Branches" - which node id branch focus is currently
  // anchored to, or null when off. Local-only state (never scene state),
  // same posture as App.tsx's documentViewContent - a pure display concern.
  const { effectiveBranchFocusOriginId, onToggleBranchFocus } = useBranchFocus(scene.nodes);

  // R6.3: viewport (pan/zoom) reporting - see the hook's own doc and
  // makeDebouncedViewportReport's for why the debounce is load-bearing.
  const { onMove, viewportTimerRef } = useViewportReporting(store);

  useEffect(() => {
    if (draggingRef.current) return;
    const next = withPreservedFlowState(
      toFlowNodes(
        scene,
        store,
        onOpenDocumentView,
        effectiveBranchFocusOriginId,
        onToggleBranchFocus,
        focusAcceptedPaths,
        // Non-null: the guarded-null lazy-init above runs synchronously on
        // every render before this effect can fire, so `.current` is
        // always populated by the time this closure executes - TS just
        // can't prove that across the mutable ref indirection.
        toFlowNodesCacheRef.current!,
        getComposerRoute,
        filterKinds,
        filterStatuses,
      ),
      nodesRef.current,
    );
    // Mirror advances with the state it describes - see nodesRef's comment.
    nodesRef.current = next;
    // Straight into React Flow's own store rather than through React state:
    // this is the same setter React Flow's internal prop-sync would call, so
    // the renderer is updated in this effect instead of one commit later.
    storeApi.getState().setNodes(next);
  }, [
    scene, store, onOpenDocumentView, effectiveBranchFocusOriginId, onToggleBranchFocus, focusAcceptedPaths,
    getComposerRoute, filterKinds, filterStatuses,
    // nodesRef/draggingRef/storeApi are all stable ref/store-api identities
    // (useNodeDragAndSizeSync's/useStoreApi's own refs never change across
    // renders) - listed here only because they now cross a hook boundary,
    // which stops react-hooks/exhaustive-deps from inferring that stability
    // on its own; behaviorally a no-op, same as before this extraction.
    nodesRef, draggingRef, storeApi,
    // REVIEW-FIX: a scene publish that lands while draggingRef.current is
    // true bails out of this effect ABOVE and is discarded entirely, not
    // queued - and draggingRef is a plain ref, so nothing here re-ran when
    // dragging actually ended, permanently losing that snapshot unless some
    // LATER, unrelated publish happened to change `scene` again. dragActive
    // is reactive state that flips false in the exact same onNodesChange
    // branch that clears draggingRef.current (see its own doc above), so
    // listing it here is what makes this effect reconsider "should I sync
    // now" the instant a drag gesture ends, using whichever `scene` this
    // render already holds - not a special replay of whatever arrived
    // mid-drag, just the ordinary reconciliation this effect always does,
    // finally allowed to run once dragging stops.
    dragActive,
  ]);

  // ADR-012 stage 12.3: Shift+F10 / the ContextMenu key opens a node's menu
  // via the keyboard - see keyboardContextMenu.ts's own doc for why this
  // needs a document-level listener rather than anything attached to
  // .scene-node itself.
  useEffect(() => {
    document.addEventListener("keydown", handleKeyboardContextMenu);
    return () => document.removeEventListener("keydown", handleKeyboardContextMenu);
  }, []);

  // Hover no longer feeds the edge model at all: ConnectionCanvas applies
  // the faded-connections lens while drawing, from its own hoveredId prop.
  // That leaves this derivation dependent only on the scene, so hovering a
  // connection cannot rebuild it.
  const edges = useMemo(() => toFlowEdges(scene, null), [scene]);
  // Mirror of the rendered edges, read by the drag pipeline when it resolves
  // which connections a gesture must keep in step. A ref rather than a
  // dependency so the middleware is not re-registered whenever edges change.
  // What ConnectionCanvas draws. Derived from the same edge model as before,
  // reduced to what drawing needs; positions are read live from the flow
  // store each frame rather than carried on these objects, which is the
  // whole point of the canvas approach.
  const connections = useMemo<ConnectionSpec[]>(
    () => edges.map((e) => ({ id: e.id, source: e.source, target: e.target, orthogonal: e.type === "orthogonal" })),
    [edges],
  );
  // The geometry the canvas last drew, reported back so hit-testing runs
  // against exactly what is on screen instead of a second computation that
  // could disagree with it.
  const connectionGeometryRef = useRef<Map<string, ConnectionPath>>(new Map());
  const onConnectionGeometry = useCallback((paths: Map<string, ConnectionPath>) => {
    connectionGeometryRef.current = paths;
  }, []);
  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null);
  const connectionStroke = useCssVar("--gl-surface-border-strong", "#505050");
  const connectionSelectedStroke = useCssVar("--gl-surface-text-primary", "#E0E0E0");

  // Pointer interaction for connections. The canvas is presentational and
  // never receives events itself, so hover and selection are resolved here
  // by testing the pointer against the geometry the canvas reported.
  const connectionAt = useCallback(
    (clientX: number, clientY: number): string | null => {
      const point = reactFlow.screenToFlowPosition({ x: clientX, y: clientY });
      const zoom = storeApi.getState().transform[2] || 1;
      // A constant on-screen grab distance, expressed in flow units.
      const tolerance = 8 / zoom;
      for (const [id, path] of connectionGeometryRef.current) {
        if (isPointOnConnection(path, point, tolerance)) return id;
      }
      return null;
    },
    [reactFlow, storeApi],
  );

  // R8a: the minimap used to render every node as React Flow's own default
  // plain rectangle (no nodeColor/nodeStrokeColor was ever passed), which
  // reads as flat, undifferentiated "white boxes" against this app's dark
  // theme. note/frame/container are the only kinds with a real, user-
  // assigned color (the same palette GroupColorPicker writes) - reflect it
  // here so a colored group/note actually stands out on the minimap the
  // way it does on the canvas. Every other kind (and any uncolored group/
  // note) gets one deliberate, visible neutral instead of RF's default;
  // the currently selected node gets the brightest tone so selection state
  // reads on the minimap too. Colors are read from the real design tokens
  // (not hardcoded hex) so this stays theme-driven.
  const minimapNodeColor = useCssVar("--gl-surface-handle-hover", "#6A6A6A");
  const minimapStrokeColor = useCssVar("--gl-surface-border-strong", "#505050");
  const minimapSelectedColor = useCssVar("--gl-surface-text-bright", "#FFFFFF");
  const getMinimapNodeColor = useCallback(
    (node: SceneFlowNode) => {
      if (node.selected) return minimapSelectedColor;
      if ((node.type === "note" || node.type === "frame" || node.type === "container") && node.data.color) {
        return node.data.color;
      }
      return minimapNodeColor;
    },
    [minimapNodeColor, minimapSelectedColor],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        store.connectNodes(connection.source, connection.target);
      }
    },
    [store],
  );

  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: { id: string }[] }) => handleSelectionChange(store, sel),
    [store],
  );
  const snapGrid = useMemo<[number, number]>(() => [grid.gridSize, grid.gridSize], [grid.gridSize]);

  const { screenToFlowPosition, setCenter } = reactFlow;

  // Navigation pins (R2.4/R6.3) render only in PinOverlay.tsx's own list -
  // jump-to and rename/note editing, never a marker on the canvas itself.
  // The legacy Qt app's pins WERE real QGraphicsItems living in the same
  // scene as the nodes (NavigationPinsController.focus() called
  // pin.setSelected(True) on one) - that half of the port was never
  // carried forward. Same ViewportPortal technique the smart-guide lines
  // below already use for exactly the same reason (children render inside
  // the same CSS-transformed layer, in flow coordinates, panning/zooming
  // for free). Click jumps to the pin, matching PinOverlay's own "Pin
  // title" jump button (setCenter(pin.x, pin.y, ...)) exactly, so the two
  // affordances behave identically.
  const onJumpToPin = useCallback(
    (pin: SceneState["pins"][number]) => {
      setCenter(pin.x, pin.y, { zoom: 1, duration: motionDuration(300) });
    },
    [setCenter],
  );

  // Factor-scaled canvas panning (the drag-speed setting's REAL job) plus
  // fade-connections hover - see the hook's own doc for why these two
  // pointer-driven concerns live together (both resolve against the same
  // connectionAt hit test and the same canvasWrapperRef target).
  useCanvasPan(
    canvasWrapperRef,
    sceneRef,
    storeApi,
    reactFlow,
    store,
    connectionAt,
    scene.fadeConnectionsEnabled,
    viewportTimerRef,
    draggingRef,
    setHoveredEdgeId,
    setSelectedConnectionId,
  );

  // Delete removes the selected connection. React Flow used to report edge
  // deletions through onDelete; it no longer renders connections, so this
  // owns that gesture now.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      if (selectedConnectionId === null) return;
      const target = event.target as HTMLElement | null;
      // Never while typing.
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      store.removeEdges([selectedConnectionId]);
      setSelectedConnectionId(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [selectedConnectionId, store]);

  const onDoubleClick = useCallback(
    (event: React.MouseEvent) => {
      // Double-click on empty canvas creates a node there - the R1 stand-in
      // for the plugin picker / context menu creation paths (R2/R8).
      const target = event.target as HTMLElement;
      if (!target.closest(".react-flow__node")) {
        const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
        store.addNode(position.x, position.y);
      }
    },
    [screenToFlowPosition, store],
  );

  return (
    <div
      className="scene-canvas"
      ref={canvasWrapperRef}
      onDoubleClick={onDoubleClick}
      // ADR-015 stage 15.6: the one stable, content-independent hook the
      // Playwright boot-smoke suite (web_ui/e2e/boot.spec.ts) needs to
      // assert the real canvas surface rendered - every other candidate
      // here (text content, node count) is either empty on a fresh
      // session or churns as node types gain feature work.
      data-testid="scene-canvas"
    >
      <ReactFlow
        /* Uncontrolled node state, deliberately. With a controlled `nodes`
           prop, every drag frame travels app state -> re-render -> React
           Flow's prop-sync effect (a PASSIVE effect, so it lands after the
           browser has already had a chance to paint) before the renderer
           learns the new position. Working node editors do the opposite:
           Drawflow, for one, writes the node's position and its connection
           paths synchronously inside the same mousemove handler. Handing
           node state to React Flow reproduces that shape here - a drag
           frame is applied to its store inside the pointer event, and the
           node card and its connections re-render together from that one
           store write. Scene snapshots from the backend are pushed in
           explicitly via the store (see the scene-sync effect above). */
        defaultNodes={EMPTY_NODES}
        edges={EMPTY_EDGES}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        onDelete={onDelete}
        onMove={onMove}
        // R5.1: mirrors React Flow's own selection state into the store so
        // PluginPicker can attach "which node was selected" to executePlugin
        // without either component reaching into the other's internals.
        onSelectionChange={onSelectionChange}
        /* Pan is owned by the wrapper's factor-scaled handler (see
           onCanvasMouseDown) - React Flow's own panOnDrag has no speed
           input, which is how the drag-speed setting lost its meaning in
           the straight port. */
        panOnDrag={false}
        snapToGrid={scene.snapToGrid}
        snapGrid={snapGrid}
        // Double-click is the R1 create-node gesture (wrapper onDoubleClick);
        // RF's default dblclick-zoom would consume it before it ever bubbles.
        zoomOnDoubleClick={false}
        fitView
        // The initial fitView's OWN zoom ceiling, independent of maxZoom
        // below (which only bounds interactive/manual zoom) - see
        // FIT_VIEW_MAX_ZOOM's own doc (canvasConstants.ts) for the bug this
        // prevents and why every OTHER "fit to content" trigger (AppBar's
        // Fit All button/overflow duplicate, the command-palette twin)
        // must share this exact constant, not just this one call site.
        fitViewOptions={{ maxZoom: FIT_VIEW_MAX_ZOOM }}
        minZoom={0.1}
        maxZoom={2.5}
        deleteKeyCode={DELETE_KEY_CODES}
        proOptions={PRO_OPTIONS}
        defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
        /*
         * ADR-011 stage 11.2: off-viewport nodes no longer mount at all (nor
         * re-render, nor pay their markdown/highlight/KaTeX parse cost) -
         * the single biggest lever this ADR has on a large canvas. Suspended
         * for exactly the duration of one PNG export via exportInProgress
         * (see that field's own doc above and exportCanvasPng.ts's).
         *
         * Every other "assumes all nodes are mounted" site the ADR called
         * out for audit, and what each needed:
         * - Group/collapse (GroupNodeView.tsx, applyGroupDragDelta/
         *   collectTransitiveMemberIds above): reads the LOCAL `nodes` data
         *   array and `data.itemIds` only - never the DOM or React Flow's
         *   internal `measured` cache - so an unmounted member is exactly as
         *   reachable as a mounted one. No fix needed.
         * - LOD (useLodVisibility.ts): a hook called FROM WITHIN each
         *   mounted node's own component - an unmounted node's hook simply
         *   never runs, which is correct (no work to skip, nothing to get
         *   wrong). No fix needed.
         * - Smart guides: DID need a fix - see buildDragSizeCache/
         *   computeSmartGuideFrame above (stage 11.3) for the drag-start
         *   cache, and flowNodeOwnSize's own doc for the frame/container/
         *   chart data-level fallback.
         * - Search/pin-highlight (SearchOverlay.tsx/PinOverlay.tsx): both
         *   already call React Flow's setCenter with the target's own
         *   SCENE x/y (never a DOM-measured position) UNCONDITIONALLY, i.e.
         *   before the target could possibly need to be mounted/
         *   interactable - panning the viewport is what MAKES it mount
         *   under virtualization, not something that requires it already
         *   being mounted. Verified, not fixed; see the regression test
         *   covering this in SceneCanvas.pinSearchJump.test.tsx.
         * - Export-to-PNG (exportCanvasPng.ts): DID need a fix - the export
         *   fits every node into a 1920x1080 FRAME, which the live
         *   on-screen container can be smaller than, so virtualization
         *   could leave a node that fits the export frame outside the
         *   live container's real bounds and never mount for capture. Fixed
         *   via exportInProgress above, which that module now sets for the
         *   capture's duration.
         */
        onlyRenderVisibleElements={!exportInProgress && !dragActive}
      >
        {/* Every connection on the scene is drawn here, redrawn each frame
            from live node positions - see ConnectionCanvas's module doc. */}
        <ConnectionCanvas
          connections={connections}
          hoveredId={hoveredEdgeId}
          selectedId={selectedConnectionId}
          fadeEnabled={scene.fadeConnectionsEnabled}
          stroke={connectionStroke}
          selectedStroke={connectionSelectedStroke}
          fadedOpacity={FADED_CONNECTION_OPACITY}
          onGeometry={onConnectionGeometry}
        />
        <Background
          variant={GRID_VARIANTS[grid.gridStyle] ?? BackgroundVariant.Dots}
          gap={grid.gridSize}
          color={grid.gridColor}
          style={{ opacity: grid.gridOpacityPercent / 100 }}
        />
        <MiniMap
          pannable
          zoomable
          className="scene-minimap"
          nodeColor={getMinimapNodeColor}
          nodeStrokeColor={minimapStrokeColor}
          nodeStrokeWidth={2}
        />
        {/* R7.5b-3: smart-guide lines. Legacy's guides were QGraphicsLineItems
            in the same unified scene as the nodes, panning/zooming for free -
            ViewportPortal is the direct React Flow analog (children render
            inside the same CSS-transformed layer, in flow coordinates). The
            dash color is Qt's QColor(128, 128, 128, 200) ported exactly
            (200/255 = 0.784 alpha). pointerEvents none so a guide can never
            swallow the drag it is annotating. */}
        {visibleGuideLines.length > 0 && (
          <ViewportPortal>
            {visibleGuideLines.map((guide, index) => (
              <div
                key={index}
                className="smart-guide-line"
                style={
                  guide.orientation === "vertical"
                    ? {
                        position: "absolute",
                        left: guide.position,
                        top: guide.start,
                        width: 0,
                        height: guide.end - guide.start,
                        borderLeft: "1px dashed rgba(128, 128, 128, 0.784)",
                        pointerEvents: "none",
                      }
                    : {
                        position: "absolute",
                        left: guide.start,
                        top: guide.position,
                        width: guide.end - guide.start,
                        height: 0,
                        borderTop: "1px dashed rgba(128, 128, 128, 0.784)",
                        pointerEvents: "none",
                      }
                }
              />
            ))}
          </ViewportPortal>
        )}
        {/* R2.4/R6.3 fix: navigation pins had no on-canvas presence at all -
            see this component's own doc comment on onJumpToPin above. One
            marker per pin, anchored at its real scene (x, y) - the SAME
            ViewportPortal technique as the smart-guide lines just above,
            for the same "pan/zoom for free, flow coordinates" reason. The
            marker's own tip (not its center) sits at (x, y), matching how
            a map pin visually points AT a location rather than covering
            it. pointerEvents stays enabled here (unlike the guide lines)
            since a pin must be clickable to jump, mirroring PinOverlay's
            own jump button. */}
        {scene.pins.length > 0 && (
          <ViewportPortal>
            {scene.pins.map((pin) => (
              <button
                key={pin.id}
                type="button"
                className="scene-pin-marker"
                style={{ position: "absolute", left: pin.x, top: pin.y }}
                title={pin.note || pin.title}
                aria-label={`Jump to pin ${pin.title}`}
                onClick={() => onJumpToPin(pin)}
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" className="scene-pin-marker-icon">
                  <path d="M9 4h6l-1 6 3 3H7l3-3Z" />
                  <path d="M12 13v7" />
                </svg>
              </button>
            ))}
          </ViewportPortal>
        )}
      </ReactFlow>
      {/* ADR-012 stage 12.6: a non-blank empty-canvas hint - a fresh
          session's scene is otherwise indistinguishable from a broken one.
          pointer-events:none on the wrapper (styles.css) so it never
          intercepts panning/the double-click-to-create-node gesture on
          .scene-canvas beneath it; the button itself opts back into
          pointer-events so it stays clickable. */}
      {scene.nodes.length === 0 && (
        <div className="scene-empty-hint" role="status">
          <p className="scene-empty-hint-text">Type a message to start, or load the sample workspace.</p>
          <button type="button" className="scene-empty-hint-button" onClick={() => store.loadSampleWorkspace()}>
            Load Sample Workspace
          </button>
        </div>
      )}
    </div>
  );
}

// The ReactFlowProvider lives in App (R2): the app bar's zoom/fit buttons and
// the R2.4 PinOverlay (jump-to-pin via setCenter) need the same React Flow
// instance the canvas renders into - pins moved out to their own overlay,
// ported with real search + rename/note editing (R2.4).
export function SceneCanvas({
  store,
  onOpenDocumentView,
  // ADR-018 stage 18.3: optional (default no-op) so every existing direct
  // render of <SceneCanvas> - App.tsx aside - keeps working unchanged; only
  // App.tsx's real render supplies the composer's live route. See
  // toFlowNodes' own comment for why this is a getter, not a value.
  getComposerRoute = () => ({ provider: "", modelId: "" }),
}: {
  store: SceneStore;
  onOpenDocumentView: (markdown: string, sourceLabel: string) => void;
  getComposerRoute?: () => { provider: string; modelId: string };
}) {
  // ADR-003 stage 3.5: renders BridgeErrorState INSTEAD of the canvas - not
  // alongside it - the moment the scene topic's schema version is rejected.
  // CanvasInner is not mounted at all in that case: its hooks (toFlowNodes,
  // the drag/selection state, React Flow itself) all assume `scene` is
  // current, and per BridgeErrorState's own doc, a rejected payload makes
  // whatever the store is still holding stale by definition the instant this
  // fires (WsTransport withholds the frame; the store simply stops updating
  // - see SceneStore.getSceneVersionRejection's own comment).
  // Review-fix: getSceneBlockingRejection, not getSceneVersionRejection -
  // the latter clears the instant a compatible frame arrives at the wire
  // level, which for a patch (the normal steady-state path) is BEFORE this
  // store has confirmed the patch actually applies. Gating on the raw
  // signal let CanvasInner flash the stale pre-outage scene for one tick
  // during recovery - see sceneStore.ts's own comment on
  // sceneVersionRecovering for the full mechanism.
  const versionRejection = useSyncExternalStore(store.subscribe, store.getSceneBlockingRejection);
  if (versionRejection) {
    return (
      <BridgeErrorState title="Canvas unavailable" rejection={versionRejection} className="scene-bridge-error" />
    );
  }
  return <CanvasInner store={store} onOpenDocumentView={onOpenDocumentView} getComposerRoute={getComposerRoute} />;
}
