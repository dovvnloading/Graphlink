import {
  Background,
  BackgroundVariant,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ViewportPortal,
  useReactFlow,
  useStore,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  type OnMove,
  applyNodeChanges,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";
import type { StreamListener } from "../../lib/ws/transport";
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
import { PyCoderNodeView, type PyCoderFlowNode } from "./PyCoderNodeView";
import { ThinkingNodeView, type ThinkingFlowNode } from "./ThinkingNodeView";
import { WebResearchNodeView, type WebResearchFlowNode } from "./WebResearchNodeView";
import {
  GROUP_FALLBACK_HEIGHT,
  GROUP_FALLBACK_WIDTH,
  LOD_ZOOM_THRESHOLD,
  VIEWPORT_REPORT_DEBOUNCE_MS,
} from "./canvasConstants";
import { SceneStore, scaleDragPosition } from "./sceneStore";
import { computeSmartGuideSnap, type GuideLine, type Rect } from "./smartGuides";

// R6.1: Notes/Frames/Containers - the generated SceneNodeRow type (codegen
// source: backend/canvas.py's scene_payload()) has not been regenerated yet
// to include these fields (see backend/canvas.py's own R6.1 section for the
// exact contract this documents - color/headerColor/isSystemPrompt/
// isSummaryNote/itemIds/isLocked/groupWidth/groupHeight). This local type
// carries the contract precisely so toFlowNodes below reads real,
// type-checked field names instead of scattering `as any` - once codegen
// runs, SceneNodeRow will carry these natively and this cast (and this
// comment) can be deleted.
interface SceneNodeGroupFields {
  color: string | null;
  headerColor: string | null;
  isSystemPrompt: boolean;
  isSummaryNote: boolean;
  // ADR-002 Workstream 1 ("Compare Branches") - note kind only, same
  // "generated type doesn't carry this yet" situation as every other
  // field on this interface. itemIds (already here, for frame/container
  // membership) doubles as the source branch node ids for a comparison
  // note - see backend/canvas.py's SceneNode.item_ids comment.
  isBranchComparison: boolean;
  itemIds: string[];
  isLocked: boolean;
  groupWidth: number | null;
  groupHeight: number | null;
}
type SceneNodeRowWithGroups = SceneNodeRow & SceneNodeGroupFields;

// R6.2: Chart node. Same situation as SceneNodeGroupFields above - the
// generated SceneNodeRow type hasn't been regenerated yet to carry
// chartType/chartData/chartError/chartAssetId/chartAssetVersion/chartWidth/
// chartHeight/chartAspectLocked/chartSourceNodeId (backend/canvas.py's
// scene_payload() contract for this increment). chartData is left as
// Record<string, unknown> rather than a fully-typed union - it is already
// canonicalized server-side (graphlink_chart_data.py's
// canonicalize_chart_data), and this view only ever reads its own
// well-known "title" key defensively (see ChartNodeView.tsx).
interface SceneNodeChartFields {
  chartType: string;
  chartData: Record<string, unknown>;
  chartError: string;
  chartAssetId: string;
  chartAssetVersion: number;
  chartWidth: number;
  chartHeight: number;
  chartAspectLocked: boolean;
  chartSourceNodeId: string;
}
type SceneNodeRowWithChart = SceneNodeRow & SceneNodeChartFields;

// R6.3: Scene-level serialization gaps. Same situation as
// SceneNodeGroupFields/SceneNodeChartFields above - the generated
// SceneNodeRow type hasn't been regenerated yet to carry
// htmlSplitterState/chatScrollValue (backend/canvas.py's scene_payload()
// contract for this increment). contentParts is deliberately absent here -
// nothing in this increment's frontend scope reads it (see this increment's
// own report for why: it's a backend-only round-trip capability for OLD
// multimodal sessions R6.4 may load, not a new frontend feature).
interface SceneNodeR63Fields {
  htmlSplitterState: number | null;
  chatScrollValue: number;
}
type SceneNodeRowWithR63 = SceneNodeRow & SceneNodeR63Fields;

// ADR-002 Workstream 1 ("Synthesize Branches"). Same situation as
// SceneNodeGroupFields/SceneNodeChartFields/SceneNodeR63Fields above - the
// generated SceneNodeRow type hasn't been regenerated yet to carry
// provider/model/isBranchSynthesis/synthesisInstructions (backend/canvas.py's
// scene_payload() contract for this increment). itemIds is declared again
// here (already present on SceneNodeGroupFields, for the UNRELATED note-kind
// Compare Branches reuse) rather than shared between the two interfaces -
// this one is intersected with SceneNodeRow independently, in the chat-kind
// branch below, so there is no actual overlap at runtime; see
// SceneNode.item_ids's own comment on backend/canvas.py for the two,
// deliberately distinct uses of that one generic field.
interface SceneNodeSynthesisFields {
  provider: string | null;
  model: string | null;
  isBranchSynthesis: boolean;
  synthesisInstructions: string;
  itemIds: string[];
}
type SceneNodeRowWithSynthesis = SceneNodeRow & SceneNodeSynthesisFields;

// ADR-002 Workstream 1 ("Branch status and lifecycle"). Same situation as
// SceneNodeSynthesisFields above - the generated SceneNodeRow type hasn't
// been regenerated yet to carry branchStatus/isFinalDeliverable
// (backend/canvas.py's scene_payload() contract for this increment).
interface SceneNodeBranchLifecycleFields {
  branchStatus: string;
  isFinalDeliverable: boolean;
}
type SceneNodeRowWithBranchLifecycle = SceneNodeRow & SceneNodeBranchLifecycleFields;

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
  | PyCoderFlowNode
  | CodeSandboxFlowNode
  | NoteFlowNode
  | GroupFlowNode
  | ChartFlowNode;

function PlaceholderNodeView({ data, selected }: NodeProps<PlaceholderNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const collapsed = zoom < LOD_ZOOM_THRESHOLD;
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
  pycoder: PyCoderNodeView,
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
};

// R7.5b-2: the first custom edge type registered in this codebase - see
// OrthogonalEdge.tsx's own module doc. Edges not classified as "orthogonal"
// by isOrthogonalEligible below fall through to defaultEdgeOptions's stock
// bezier via an `undefined` type, same as before this feature existed.
const EDGE_TYPES = {
  orthogonal: OrthogonalEdge,
};

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
  // branchStatus isn't on the generated SceneNodeRow type yet (see
  // SceneNodeBranchLifecycleFields' own comment) - cast once here, rather
  // than at every read site below.
  const nodesById = new Map(scene.nodes.map((n) => [n.id, n as SceneNodeRowWithBranchLifecycle]));
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

// Exported standalone for direct unit testing (same posture as
// scaleDragPosition in sceneStore.ts) - covers the parentChatNodeId
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
): SceneFlowNode[] {
  // Looked up per-chat-node below to build dockedChildren - a docked node is
  // omitted from the returned array entirely (see the "thinking" branch), so
  // this is the only remaining way a chat node's dock badge/menu can find it.
  const nodesById = new Map(scene.nodes.map((n) => [n.id, n]));
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
  const isDimmed = (id: string) => dimmedIds.has(id) || nonAcceptedIds.has(id);
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
      const dockedChildren: { id: string; label: string }[] = [];
      for (const e of scene.edges) {
        if (e.source !== n.id) continue;
        const target = nodesById.get(e.target);
        if (target?.isDocked) dockedChildren.push({ id: target.id, label: target.title });
      }
      const chatR63 = n as SceneNodeRowWithR63;
      const chatSynthesis = n as SceneNodeRowWithSynthesis;
      const chatLifecycle = n as SceneNodeRowWithBranchLifecycle;
      flowNodes.push({
        id: n.id,
        type: "chat" as const,
        position: { x: n.x, y: n.y },
        // R8a: "Hide Other Branches" dimming - see computeDimmedNodeIds' own
        // doc above. undefined (not an explicit opacity: 1) when not dimmed,
        // so this never overrides anything else that might set style later.
        style: isDimmed(n.id) ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          content: n.content,
          isUser: n.isUser,
          isCollapsed: n.isCollapsed,
          dockedChildren,
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.deleteChatNode(n.id),
          onUndockChild: (childId: string) => store.setNodeDocked(childId, false),
          onRegenerate: () => store.regenerateResponse(n.id),
          onGenerateImage: () => store.generateImage(n.id),
          // R6.2: real chart generation - fires the new generateChart intent
          // with this chat node as the parent (see ChatNodeView.tsx's own
          // Generate Chart submenu). Fire-and-forget, same posture as
          // onGenerateImage above - the new chart node arrives through the
          // next scene snapshot.
          onGenerateChart: (chartType: string) => store.generateChart(n.id, chartType),
          // R8a: the two note agents, restored from the deleted Qt app. Same
          // fire-and-forget posture as onGenerateImage above - the new note
          // arrives through the next scene snapshot.
          onGenerateKeyTakeaway: () => store.generateKeyTakeaway(n.id),
          onGenerateExplainerNote: () => store.generateExplainerNote(n.id),
          // R8a: Open Document View - shows this node's own message text
          // verbatim in the read-only document panel. Guards on non-blank
          // content, matching legacy's own document-view action exactly:
          // a notification on nothing, not a silent no-op (see
          // conversationHistoryToDocumentMarkdown's own doc above for the
          // sibling conversation-node guard).
          onOpenDocumentView: () => {
            if (n.content.trim()) onOpenDocumentView(n.content, n.isUser ? "Your message" : "Assistant message");
            else store.showInfoNotification(NO_DOCUMENT_VIEW_CONTENT_MESSAGE);
          },
          // R8a: "Hide Other Branches" - isBranchFocusActive is scene-wide
          // (not per-node), purely so the menu button can flip its own label
          // to "Show All Branches" once ANY branch focus is active, matching
          // legacy's own `"Show All Branches" if is_branch_hidden else
          // "Hide Other Branches"` regardless of which node's menu is open.
          isBranchFocusActive: branchFocusOriginId !== null,
          onToggleBranchFocus: () => onToggleBranchFocus(n.id),
          // ADR-002 Workstream 1: stages this node as sceneStore's
          // replyTargetNodeId - the composer's next Send then reads and
          // consumes it (see sceneStore.ts's sendMessage). `store` is
          // already threaded into toFlowNodes, so no new parameter is
          // needed here (unlike onToggleBranchFocus, which lives as local
          // state on SceneCanvas itself, not on the store).
          onBranchFromHere: () => store.setReplyTargetNodeId(n.id),
          // ADR-002 Workstream 1 ("Synthesize Branches"): provenance carried
          // by the result node - see SceneNodeSynthesisFields' own comment.
          // provider/model are None/absent for every ordinary chat node
          // (the vast majority), rendered as no badge at all - see
          // ChatNodeView.tsx's own guard.
          provider: chatSynthesis.provider,
          model: chatSynthesis.model,
          isBranchSynthesis: chatSynthesis.isBranchSynthesis,
          synthesisInstructions: chatSynthesis.synthesisInstructions,
          synthesisSourceNodeIds: chatSynthesis.itemIds,
          // ADR-002 Workstream 1 ("Branch status and lifecycle") - see
          // SceneNodeBranchLifecycleFields' own comment. Fire-and-forget,
          // same posture as onBranchFromHere/onGenerateKeyTakeaway above -
          // the new value arrives through the next scene snapshot.
          branchStatus: chatLifecycle.branchStatus,
          isFinalDeliverable: chatLifecycle.isFinalDeliverable,
          onSetBranchStatus: (status: string) => store.setBranchStatus(n.id, status),
          onSetFinalDeliverable: (isFinal: boolean) => store.setFinalDeliverable(n.id, isFinal),
          onCollapseBranch: (collapsed: boolean) => store.collapseBranch(n.id, collapsed),
          // R6.3: the node's own scroll position within its content area -
          // read on mount by ChatNodeView (restore) and reported (debounced)
          // via the new setChatScrollValue intent on every scroll. Defaults
          // to 0 the same way every other numeric ?? fallback in this file
          // does, ahead of codegen regenerating SceneNodeRow to carry it.
          chatScrollValue: chatR63.chatScrollValue ?? 0,
          onScrollChange: (value: number) => store.setChatScrollValue(n.id, value),
        },
      });
      continue;
    }
    if (n.kind === "code") {
      // parentChatNodeId: this code node's own one-hop parent lookup - the
      // new stack's equivalent of legacy's CodeNode.parent_content_node,
      // resolved client-side (same one-hop-via-edges style as
      // dockedChildren above) since the backend never kind-sniffs a code
      // node id back to its parent chat node (see regenerateResponse's own
      // comment above and SceneCanvas's regenerate-response design notes).
      const parentEdge = scene.edges.find((e) => e.target === n.id);
      const parentChatNodeId = parentEdge ? parentEdge.source : null;
      flowNodes.push({
        id: n.id,
        type: "code" as const,
        position: { x: n.x, y: n.y },
        style: isDimmed(n.id) ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          code: n.code,
          language: n.language,
          parentChatNodeId,
          onRegenerate: () => {
            if (parentChatNodeId) store.regenerateResponse(parentChatNodeId);
          },
          onDelete: () => store.removeNodes([n.id]),
          // R8a: "Hide Other Branches" - see the chat branch above for why
          // isBranchFocusActive is scene-wide rather than per-node.
          isBranchFocusActive: branchFocusOriginId !== null,
          onToggleBranchFocus: () => onToggleBranchFocus(n.id),
        },
      });
      continue;
    }
    if (n.kind === "document") {
      flowNodes.push({
        id: n.id,
        type: "document" as const,
        position: { x: n.x, y: n.y },
        style: isDimmed(n.id) ? { opacity: BRANCH_DIM_OPACITY } : undefined,
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
          // setChatCollapsed's backend handler (backend/canvas.py) looks up
          // ANY node by id and sets is_collapsed - it does not special-case
          // "chat" kind despite the intent's name - so it is reused here
          // as-is rather than inventing a setDocumentCollapsed intent the
          // backend doesn't register. See this increment's report for the
          // full reasoning.
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDock: () => store.setNodeDocked(n.id, true),
          onDelete: () => store.removeNodes([n.id]),
          // R8a: "Hide Other Branches" - see the chat branch above.
          isBranchFocusActive: branchFocusOriginId !== null,
          onToggleBranchFocus: () => onToggleBranchFocus(n.id),
        },
      });
      continue;
    }
    if (n.kind === "thinking") {
      // Docked-hiding is handled by the generic check above; once undocked,
      // it resurfaces as a badge + "Reveal Docked Items" entry on its parent
      // chat node (dockedChildren above).
      flowNodes.push({
        id: n.id,
        type: "thinking" as const,
        position: { x: n.x, y: n.y },
        style: isDimmed(n.id) ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          thinkingText: n.content,
          onDock: () => store.setNodeDocked(n.id, true),
          onDelete: () => store.removeNodes([n.id]),
          // R8a: "Hide Other Branches" - see the chat branch above.
          isBranchFocusActive: branchFocusOriginId !== null,
          onToggleBranchFocus: () => onToggleBranchFocus(n.id),
        },
      });
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
      const htmlR63 = n as SceneNodeRowWithR63;
      flowNodes.push({
        id: n.id,
        type: "html" as const,
        position: { x: n.x, y: n.y },
        data: {
          htmlContent: n.content,
          isCollapsed: n.isCollapsed,
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          // R6.3: the Source/Preview split position - read on mount by
          // HtmlNodeView (restore; null means "no saved value, use the
          // component's own 50/50 default") and reported (debounced) via the
          // new setHtmlSplitterState intent once a drag settles. See
          // canvasConstants.ts's own HTML_SPLIT_* doc for why this exists
          // now despite being scoped OUT back in R3.17/R3.18.
          htmlSplitterState: htmlR63.htmlSplitterState ?? null,
          onSplitterChange: (value: number) => store.setHtmlSplitterState(n.id, value),
        },
      });
      continue;
    }
    if (n.kind === "image") {
      // No onDock here either (same reasoning as the html branch above) -
      // ImageNodeView never offers a "dock into parent" action, so this kind
      // never sets isDocked=true through any UI path of its own. The generic
      // `if (n.isDocked) continue` guard above still covers it correctly if
      // it were ever docked via a direct WS call, same as html.
      flowNodes.push({
        id: n.id,
        type: "image" as const,
        position: { x: n.x, y: n.y },
        style: isDimmed(n.id) ? { opacity: BRANCH_DIM_OPACITY } : undefined,
        data: {
          imageAssetId: n.imageAssetId,
          prompt: n.content,
          onDelete: () => store.removeNodes([n.id]),
          // R4.4a: unlike CodeNodeView's onRegenerate, no client-side parent
          // lookup/null-guard is needed here - the backend resolves the
          // image's parent chat node internally (see sceneStore.ts's
          // regenerateImage / backend/canvas.py's resolve_regenerate_image).
          onRegenerate: () => store.regenerateImage(n.id),
          // R8a: "Hide Other Branches" - see the chat branch above.
          isBranchFocusActive: branchFocusOriginId !== null,
          onToggleBranchFocus: () => onToggleBranchFocus(n.id),
        },
      });
      continue;
    }
    if (n.kind === "conversation") {
      // No onDock here either (same reasoning as the html/image branches
      // above) - ConversationNodeView never offers a dock-into-parent
      // action, so this kind never sets isDocked=true through any UI path
      // of its own; the generic `if (n.isDocked) continue` guard above still
      // covers it correctly if it were ever docked via a direct WS call.
      flowNodes.push({
        id: n.id,
        type: "conversation" as const,
        position: { x: n.x, y: n.y },
        data: {
          history: n.history,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          // Reuses the existing generic setChatCollapsed intent - same
          // reasoning as every other non-chat node kind's onToggleCollapse
          // above (the backend handler looks up ANY node by id).
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          onSend: (text: string) => store.sendConversationMessage(n.id, text),
          onDeleteMessage: (index: number) => store.deleteConversationMessage(n.id, index),
          // Same null-guard pattern as Composer.tsx's own analogous cancel
          // call site - only fire the intent if there is genuinely a
          // non-null request id to target.
          onCancel: () => {
            if (n.pendingRequestId) store.cancelConversationRequest(n.pendingRequestId);
          },
          // R8a: Open Document View - the node's ENTIRE message history,
          // formatted as a numbered transcript (see
          // conversationHistoryToDocumentMarkdown's own doc above). Guards
          // on a non-empty formatted result (empty history, or every
          // message blank, both format to "") the same way the chat branch
          // above guards on non-blank content.
          onOpenDocumentView: () => {
            const markdown = conversationHistoryToDocumentMarkdown(n.history);
            if (markdown) onOpenDocumentView(markdown, "Conversation transcript");
            else store.showInfoNotification(NO_DOCUMENT_VIEW_CONTENT_MESSAGE);
          },
        },
      });
      continue;
    }
    if (n.kind === "web_research") {
      // No onDock here either (same reasoning as the html/image/conversation
      // branches above) - WebResearchNodeView never offers a dock-into-parent
      // action; the generic `if (n.isDocked) continue` guard above still
      // covers it correctly if it were ever docked via a direct WS call.
      flowNodes.push({
        id: n.id,
        type: "web_research" as const,
        position: { x: n.x, y: n.y },
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
          // Reuses the existing generic setChatCollapsed intent - same
          // reasoning as every other non-chat node kind's onToggleCollapse
          // above (the backend handler looks up ANY node by id).
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          onRun: (query: string) => store.runWebResearch(n.id, query),
          // Same null-guard pattern as the conversation node's own analogous
          // cancel call site above - only fire the intent if there is
          // genuinely a non-null request id to target.
          onCancel: () => {
            if (n.pendingRequestId) store.cancelWebResearchRequest(n.pendingRequestId);
          },
        },
      });
      continue;
    }
    if (n.kind === "artifact") {
      // No onDock here either (same reasoning as the html/image/conversation/
      // web_research branches above) - ArtifactNodeView never offers a
      // dock-into-parent action; the generic `if (n.isDocked) continue` guard
      // above still covers it correctly if it were ever docked via a direct
      // WS call.
      flowNodes.push({
        id: n.id,
        type: "artifact" as const,
        position: { x: n.x, y: n.y },
        data: {
          artifactContent: n.artifactContent,
          history: n.history,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          // Reuses the existing generic setChatCollapsed intent - same
          // reasoning as every other non-chat node kind's onToggleCollapse
          // above (the backend handler looks up ANY node by id).
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          onSubmit: (text: string) => store.sendArtifactMessage(n.id, text),
          // Same null-guard pattern as the conversation/web_research nodes'
          // own analogous cancel call sites above - only fire the intent if
          // there is genuinely a non-null request id to target.
          onCancel: () => {
            if (n.pendingRequestId) store.cancelArtifactRequest(n.pendingRequestId);
          },
        },
      });
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
      flowNodes.push({
        id: n.id,
        type: "gitlink" as const,
        position: { x: n.x, y: n.y },
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
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          onFetchRepositories: () => store.fetchGitlinkRepositories(n.id),
          onLoadTree: (repo: string, branch: string) => store.loadGitlinkRepoTree(n.id, repo, branch),
          onSetLocalRoot: (localRoot: string) => store.setGitlinkLocalRoot(n.id, localRoot),
          onBrowseLocalRoot: () => store.pickGitlinkLocalRoot(n.id),
          onImportSnapshot: (repo: string, branch: string) => store.importGitlinkSnapshot(n.id, repo, branch),
          onBuildContext: (scopeMode: string, selectedPaths: string[]) =>
            store.buildGitlinkContext(n.id, scopeMode, selectedPaths),
          onFetchContext: () => store.fetchGitlinkContext(n.id),
          onRun: (taskPrompt: string) => store.runGitlinkChangeSet(n.id, taskPrompt),
          // Same null-guard pattern as the conversation/web_research/artifact
          // nodes' own analogous cancel call sites above - only fire the
          // intent if there is genuinely a non-null request id to target.
          onCancel: () => {
            if (n.pendingRequestId) store.cancelGitlinkRequest(n.pendingRequestId);
          },
          onApply: (fingerprint: string) => store.applyGitlinkChanges(n.id, fingerprint),
        },
      });
      continue;
    }
    if (n.kind === "pycoder") {
      // No onDock here either (same reasoning as every non-dockable R5
      // plugin-node branch above) - PyCoderNodeView never offers a
      // dock-into-parent action; the generic `if (n.isDocked) continue`
      // guard above still covers it correctly if it were ever docked via a
      // direct WS call.
      flowNodes.push({
        id: n.id,
        type: "pycoder" as const,
        position: { x: n.x, y: n.y },
        data: {
          pycoderMode: n.pycoderMode,
          pycoderPrompt: n.pycoderPrompt,
          pycoderCode: n.pycoderCode,
          pycoderOutput: n.pycoderOutput,
          pycoderAnalysis: n.pycoderAnalysis,
          pycoderLastRunFailed: n.pycoderLastRunFailed,
          pycoderAwaitingApproval: n.pycoderAwaitingApproval,
          pycoderError: n.pycoderError,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          onSetMode: (mode: string) => store.setPyCoderMode(n.id, mode),
          onRun: (inputText: string) => store.runPyCoder(n.id, inputText),
          // Same null-guard pattern as every other plugin node's own
          // analogous cancel call site above - only fire the intent if there
          // is genuinely a non-null request id to target.
          onCancel: () => {
            if (n.pendingRequestId) store.cancelPyCoderRequest(n.pendingRequestId);
          },
          // CRITICAL (see CodeExecutionApprovalPanel.tsx's own module doc):
          // these read n.pendingRequestId - the CURRENT scene snapshot's own
          // value for THIS node - never anything the UI layer could supply
          // as a distinct argument. Same null-guard posture as onCancel
          // above; approveCodeExecution/denyCodeExecution both require a
          // non-null string.
          onApprove: () => {
            if (n.pendingRequestId) store.approveCodeExecution(n.pendingRequestId);
          },
          onDeny: () => {
            if (n.pendingRequestId) store.denyCodeExecution(n.pendingRequestId);
          },
        },
      });
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
      // never read/forwarded anywhere in this mapping.
      flowNodes.push({
        id: n.id,
        type: "code_sandbox" as const,
        position: { x: n.x, y: n.y },
        data: {
          codeSandboxRequirements: n.codeSandboxRequirements,
          codeSandboxApprovalRequirements: n.codeSandboxApprovalRequirements,
          codeSandboxPrompt: n.codeSandboxPrompt,
          codeSandboxCode: n.codeSandboxCode,
          codeSandboxOutput: n.codeSandboxOutput,
          codeSandboxAnalysis: n.codeSandboxAnalysis,
          codeSandboxAwaitingApproval: n.codeSandboxAwaitingApproval,
          codeSandboxError: n.codeSandboxError,
          isCollapsed: n.isCollapsed,
          pendingRequestId: n.pendingRequestId ?? null,
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
          onSetRequirements: (requirementsText: string) =>
            store.setCodeSandboxRequirements(n.id, requirementsText),
          onRun: (inputText: string) => store.runCodeSandbox(n.id, inputText),
          onCancel: () => {
            if (n.pendingRequestId) store.cancelCodeSandboxRequest(n.pendingRequestId);
          },
          // CRITICAL - same posture as the pycoder branch's own
          // onApprove/onDeny above: always n.pendingRequestId, never a
          // UI-supplied argument.
          onApprove: () => {
            if (n.pendingRequestId) store.approveCodeExecution(n.pendingRequestId);
          },
          onDeny: () => {
            if (n.pendingRequestId) store.denyCodeExecution(n.pendingRequestId);
          },
          // Generic passthrough to the transport's own stream fan-out (see
          // sceneStore.ts's own subscribeStream doc) - the live terminal
          // pane keys its subscription off data.pendingRequestId itself,
          // this closure only needs to exist so the component never touches
          // the store/transport directly.
          subscribeStream: (requestId: string, listener: StreamListener) =>
            store.subscribeStream(requestId, listener),
        },
      });
      continue;
    }
    if (n.kind === "note") {
      // No onDock here either (same reasoning as every non-dockable kind
      // above) - a note never offers a dock-into-parent action of its own;
      // the generic `if (n.isDocked) continue` guard above still covers it
      // correctly if it were ever docked via a direct WS call.
      const note = n as SceneNodeRowWithGroups;
      flowNodes.push({
        id: n.id,
        type: "note" as const,
        position: { x: n.x, y: n.y },
        data: {
          content: n.content,
          color: note.color,
          headerColor: note.headerColor,
          isSystemPrompt: note.isSystemPrompt,
          isSummaryNote: note.isSummaryNote,
          // ADR-002 Workstream 1: itemIds doubles as the source branch ids
          // for a Compare Branches result note - see SceneNodeGroupFields'
          // own comment.
          isBranchComparison: note.isBranchComparison,
          compareSourceNodeIds: note.itemIds,
          onSetContent: (content: string) => store.setNoteContent(n.id, content),
          onSetColor: (color: string | null, headerColor: string | null) =>
            store.setGroupColor(n.id, color, headerColor),
          onDelete: () => store.removeNodes([n.id]),
        },
      });
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
      const group = n as SceneNodeRowWithGroups;
      flowNodes.push({
        id: n.id,
        type: n.kind as "frame" | "container",
        position: { x: n.x, y: n.y },
        width: group.groupWidth ?? GROUP_FALLBACK_WIDTH,
        height: group.groupHeight ?? GROUP_FALLBACK_HEIGHT,
        zIndex: n.kind === "container" ? -2 : -1,
        draggable: true,
        data: {
          groupKind: n.kind,
          label: n.content,
          color: group.color,
          headerColor: group.headerColor,
          isCollapsed: n.isCollapsed,
          isLocked: group.isLocked,
          itemIds: group.itemIds,
          // R6.1 follow-up: a simplified equivalent of legacy's collapsed-
          // container hover "ghost frame" preview - just member kinds, not
          // a rendered miniature of actual content. Looked up from the
          // SAME nodesById map the dockedChildren computation above
          // already builds once per call; a stale/dangling item_ids entry
          // (a member deleted out from under a group) is silently skipped,
          // matching _bbox_of_members' own posture on the backend.
          memberKinds: group.itemIds.map((id) => nodesById.get(id)?.kind).filter((kind): kind is string => !!kind),
          onSetLabel: (text: string) => store.setGroupLabel(n.id, text),
          onToggleCollapsed: () => store.toggleGroupCollapsed(n.id),
          onToggleLock: () => store.toggleFrameLock(n.id),
          onSetColor: (color: string | null, headerColor: string | null) =>
            store.setGroupColor(n.id, color, headerColor),
          onResize: (width: number, height: number) => store.resizeFrame(n.id, width, height),
          onFitToContent: () => store.fitFrameToContent(n.id),
          onUngroup: () => store.ungroup(n.id),
        },
      });
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
      // node object.
      const chart = n as SceneNodeRowWithChart;
      flowNodes.push({
        id: n.id,
        type: "chart" as const,
        position: { x: n.x, y: n.y },
        width: chart.chartWidth,
        height: chart.chartHeight,
        data: {
          chartType: chart.chartType,
          chartData: chart.chartData,
          chartError: chart.chartError,
          chartAssetId: chart.chartAssetId,
          chartAssetVersion: chart.chartAssetVersion,
          chartWidth: chart.chartWidth,
          chartHeight: chart.chartHeight,
          chartAspectLocked: chart.chartAspectLocked,
          chartSourceNodeId: chart.chartSourceNodeId,
          onToggleAspectLock: () => store.toggleChartAspectLock(n.id),
          onResize: (width: number, height: number) => store.resizeChart(n.id, width, height),
        },
      });
      continue;
    }
    flowNodes.push({
      id: n.id,
      type: "placeholder" as const,
      position: { x: n.x, y: n.y },
      data: { title: n.title },
    });
  }

  return flowNodes;
}

// R5.1: the onSelectionChange callback's actual logic, pulled out standalone
// for direct unit testing (same posture as toFlowNodes/scaleDragPosition
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
// scaleDragPosition/toFlowNodes/handleSelectionChange above.
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
function collectTransitiveMemberIds(
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
// - anything else (web_research/artifact/gitlink/pycoder/code_sandbox/frame/
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
 * R7.5c: carry the current selection across a snapshot rebuild.
 *
 * toFlowNodes mints brand-new node objects from every scene snapshot, so
 * anything React Flow keeps on the node object rather than in the scene -
 * selection above all - is dropped unless copied over explicitly.
 *
 * Found live, not by a test: Ctrl+Arrow calls setCenter, setCenter fires
 * onMove, onMove reports the viewport to the backend, the backend echoes a
 * fresh scene, and the selection the keystroke had just made vanished about
 * 300ms later. That left the NEXT Ctrl+Arrow with no single selected node,
 * so branch-walking died after exactly one hop. The same wipe hits any
 * selection that merely overlaps a snapshot - an autosave tick, a streaming
 * token - which is why it is fixed here at the rebuild rather than inside
 * the shortcut handler.
 *
 * A node the backend actually removed is simply absent from `rebuilt`, so a
 * stale id can never resurrect one.
 */
export function withPreservedSelection(
  rebuilt: SceneFlowNode[],
  current: SceneFlowNode[],
): SceneFlowNode[] {
  const selectedIds = new Set(current.filter((n) => n.selected).map((n) => n.id));
  if (selectedIds.size === 0) return rebuilt;
  return rebuilt.map((n) => (selectedIds.has(n.id) ? { ...n, selected: true } : n));
}

// Exported standalone for direct unit testing, same posture as toFlowNodes
// above - R7.5b-1's fade-connections opacity logic doesn't need a mounted
// <ReactFlow> to verify.
export function toFlowEdges(scene: SceneState, hoveredEdgeId: string | null): Edge[] {
  // An edge pointing at a docked node must not render either - mirrors the
  // legacy connection-item self-suppression when its end node is docked.
  const dockedNodeIds = new Set(scene.nodes.filter((n) => n.isDocked).map((n) => n.id));
  const kindOf = new Map(scene.nodes.map((n) => [n.id, n.kind]));
  return scene.edges
    .filter((e) => !dockedNodeIds.has(e.target))
    .map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type:
        scene.orthogonalRouting && isOrthogonalEligible(kindOf.get(e.source), kindOf.get(e.target))
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
// posture as scaleDragPosition/toFlowNodes/handleSelectionChange above.
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
}: {
  store: SceneStore;
  onOpenDocumentView: (markdown: string, sourceLabel: string) => void;
}) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const grid = useSyncExternalStore(store.subscribe, store.getGrid);
  // ADR-002 Workstream 1 ("Branch status and lifecycle") - "Focus Accepted
  // Paths", toggled from ViewPopover.tsx's own checkbox (a sibling
  // component, hence living on sceneStore rather than as useState here -
  // see that field's own comment).
  const focusAcceptedPaths = useSyncExternalStore(store.subscribe, store.getFocusAcceptedPaths);
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
  // truth and reconcile in whenever nothing is being dragged. dragStartRef
  // powers the drag-speed scaling contract (see scaleDragPosition).
  const [nodes, setNodes] = useState<SceneFlowNode[]>([]);
  const dragStartRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const draggingRef = useRef(false);

  // R7.5b-1: which edge (if any) is under the mouse right now - the one
  // exemption from faded-connections' blanket low-opacity effect. Local-only
  // (never scene state), same posture as legacy's purely presentational hover
  // flag.
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);

  // R7.5b-3: the smart-guide lines currently visible during a drag - local
  // component state only, never scene state, matching legacy's non-persisted
  // ChatScene.smart_guide_lines. Set per drag frame in onNodesChange, cleared
  // on drag end; the render-time gate below (not an effect) hides any stale
  // lines instantly if the toggle flips off mid-drag.
  const [smartGuideLines, setSmartGuideLines] = useState<GuideLine[]>([]);
  const visibleGuideLines = scene.smartGuides ? smartGuideLines : [];

  // R8a (UI/UX issue list finding #11): the View popover's FONT section
  // (family/size/color) already round-trips real intents into scene state -
  // nothing ever consumed them. Written as CSS custom properties on the
  // canvas wrapper (not per-node inline styles) so every current AND future
  // node inherits them for free through .scene-node's own rules in
  // styles.css, the same way .scene-canvas already carries grid state down
  // via React Flow's own Background props.
  const canvasWrapperRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = canvasWrapperRef.current;
    if (!el) return;
    el.style.setProperty("--gl-node-font-family", scene.fontFamily);
    // fontSizePt is POINTS (backend field: font_size_pt, default 9 -> the
    // FONT_SIZE_MIN/MAX range of 8-16 only makes sense as points, not
    // pixels: 16px node text would be barely a size change from the 12px/
    // 11px base, while 16pt is a real, visible jump). `pt` is a real CSS
    // unit (1pt = 1/72in = 4/3px), not print-only, so this needs no
    // conversion - just the right unit suffix.
    el.style.setProperty("--gl-node-font-size", `${scene.fontSizePt}pt`);
    el.style.setProperty("--gl-node-font-color", scene.fontColor);
  }, [scene.fontFamily, scene.fontSizePt, scene.fontColor]);

  // R8a: Open Document View - the read-only markdown panel a chat/
  // conversation node's card menu opens (see toFlowNodes' own
  // onOpenDocumentView field on each of those two branches). Lives in App
  // now, not here: the panel is a real docked layout sibling of this WHOLE
  // canvas region (composer/token-counter/search/etc included), not just
  // this component's own canvas div, so its open/closed state has to live
  // above all of them (see App.tsx).
  //
  // R8a: "Hide Other Branches" - which node id branch focus is currently
  // anchored to, or null when off. Also local-only state (never scene
  // state), same posture as documentViewContent above - it is a pure
  // display concern, not something that needs to survive a reload or be
  // shared with a hypothetical second viewer.
  const [branchFocusOriginId, setBranchFocusOriginId] = useState<string | null>(null);
  // Derived, not stored: raw state alone can go stale the moment its origin
  // node is deleted while focus is active, and "is this state still
  // meaningful" is exactly the kind of derivation React's own guidance says
  // to compute during render rather than reconcile via a setState-in-effect
  // (the first version of this self-heal DID use such an effect, and the
  // react-hooks/set-state-in-effect rule correctly flagged it as the
  // cascading-render anti-pattern it is - this replaces it, not suppresses
  // it). Every consumer below reads this derived value, never the raw
  // state directly, so a deleted origin self-heals within the SAME render
  // instead of flashing "Show All Branches" for one extra frame first.
  const isBranchFocusOriginValid =
    branchFocusOriginId !== null && scene.nodes.some((n) => n.id === branchFocusOriginId);
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
        const currentIsValid = current !== null && scene.nodes.some((n) => n.id === current);
        return currentIsValid ? null : nodeId;
      });
    },
    [scene.nodes],
  );

  // R6.3: viewport (pan/zoom) reporting - see makeDebouncedViewportReport's
  // own doc above. onMove fires on every frame of a pan/zoom gesture (never
  // just once at the end, unlike NodeResizer's onResizeEnd), so the debounce
  // here is load-bearing, not just a burst guard: without it, setViewState
  // would fire a WS intent every animation frame of every pan/zoom gesture.
  const viewportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (viewportTimerRef.current) clearTimeout(viewportTimerRef.current);
    },
    [],
  );
  const onMove: OnMove = useCallback(
    (_event, viewport) => {
      makeDebouncedViewportReport(viewportTimerRef, (zoomFactor, scrollX, scrollY) =>
        store.setViewState(zoomFactor, scrollX, scrollY),
      )(viewport.zoom, viewport.x, viewport.y);
    },
    [store],
  );

  useEffect(() => {
    if (draggingRef.current) return;
    setNodes((current) =>
      withPreservedSelection(
        toFlowNodes(scene, store, onOpenDocumentView, effectiveBranchFocusOriginId, onToggleBranchFocus, focusAcceptedPaths),
        current,
      ),
    );
  }, [scene, store, onOpenDocumentView, effectiveBranchFocusOriginId, onToggleBranchFocus, focusAcceptedPaths]);

  const edges = useMemo(() => toFlowEdges(scene, hoveredEdgeId), [scene, hoveredEdgeId]);

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

  const onNodesChange = useCallback(
    (changes: NodeChange<SceneFlowNode>[]) => {
      // Synthetic member-position changes generated below (via
      // applyGroupDragDelta), alongside the real changes React Flow
      // reported - both go through the SAME applyNodeChanges call so a
      // member's local position updates in lockstep with its group, every
      // drag frame.
      const memberChanges: NodeChange<SceneFlowNode>[] = [];
      // Every drag-end position this change batch produces - the dragged
      // node(s) own settled position AND every cascaded group member -
      // collected here and committed as ONE atomic store.moveNodes call
      // below, never as individual moveNode calls (see that call site's
      // own comment for why).
      const settledMoveIntents: Array<{ id: string; x: number; y: number }> = [];
      // R7.5b-3: guides produced by this frame's drag changes - applied via
      // one setSmartGuideLines call after the map, cleared on drag end.
      // DELIBERATE deviation for multi-select drags (review-confirmed):
      // legacy cleared guides per-item, so only the LAST-processed item's
      // guides survived each frame - an artifact of Qt's per-item itemChange
      // ordering, not a design choice. Accumulating every co-mover's guides
      // shows all live alignments instead of an arbitrary one.
      const frameGuides: GuideLine[] = [];
      let sawDragging = false;
      let sawDragEnd = false;

      const scaled = changes.map((change) => {
        if (change.type !== "position" || !change.position) return change;
        if (change.dragging) {
          draggingRef.current = true;
          sawDragging = true;
          let start = dragStartRef.current.get(change.id);
          if (!start) {
            const node = nodes.find((n) => n.id === change.id);
            start = node ? { ...node.position } : { ...change.position };
            dragStartRef.current.set(change.id, start);
          }
          let finalPosition = scaleDragPosition(start, change.position, scene.dragFactor);
          // R7.5b-3: smart-guide snap, as a LAYERED PASS on top of React
          // Flow's native grid-snap (which, when enabled, already ran inside
          // RF before this change was emitted) - the recorded design
          // decision, chosen over re-implementing grid-snap manually. Smart
          // guides win per-axis when both would apply, reproducing legacy
          // snap_position's own per-axis priority. Runs BEFORE
          // applyGroupDragDelta below so carried group members ride the
          // corrected delta. Legacy's !isSelected() candidate exclusion is
          // translated as !n.selected (every co-mover in a multi-select drag
          // reports its own dragging change with selected=true); a dragged
          // group's own members are additionally excluded - they move in
          // lockstep with the group, so "aligning" against them is always
          // trivially true and would freeze the drag (a gap legacy never had
          // to answer: this canvas carries members via synthetic deltas, not
          // Qt child-item parenting - per the recorded design decision, only
          // the group's own rect snaps and members ride the delta).
          if (scene.smartGuides) {
            const moving = nodes.find((n) => n.id === change.id);
            const movingSize = measuredNodeSize(reactFlow, change.id);
            if (moving && movingSize) {
              const memberIds = groupDragKindOf(moving)
                ? collectTransitiveMemberIds(nodes, moving)
                : new Set<string>();
              const candidates: Rect[] = [];
              for (const n of nodes) {
                if (n.id === change.id || n.selected || memberIds.has(n.id)) continue;
                const size = measuredNodeSize(reactFlow, n.id);
                if (!size) continue;
                candidates.push({ x: n.position.x, y: n.position.y, width: size.width, height: size.height });
              }
              const snap = computeSmartGuideSnap(
                { x: finalPosition.x, y: finalPosition.y, width: movingSize.width, height: movingSize.height },
                candidates,
              );
              finalPosition = { x: snap.x, y: snap.y };
              frameGuides.push(...snap.guides);
            }
          }
          memberChanges.push(...applyGroupDragDelta(nodes, change.id, finalPosition));
          return {
            ...change,
            position: finalPosition,
          };
        }
        // Drag end: commit the node's final (already-scaled) position.
        draggingRef.current = false;
        sawDragEnd = true;
        const settled = nodes.find((n) => n.id === change.id);
        dragStartRef.current.delete(change.id);
        if (settled) {
          // R6.1 follow-up: collected, not committed here directly - see
          // the single store.moveNodes call below for why a group drag's
          // whole commit (the group's own node plus every cascaded member)
          // must land as ONE atomic batch, not N individual moveNode
          // calls.
          settledMoveIntents.push({ id: change.id, x: settled.position.x, y: settled.position.y });
          if (groupDragKindOf(settled)) {
            for (const memberId of collectTransitiveMemberIds(nodes, settled)) {
              const member = nodes.find((n) => n.id === memberId);
              if (member) settledMoveIntents.push({ id: memberId, x: member.position.x, y: member.position.y });
            }
          }
          // R7.5b-3 review fix: RF's drag-stop change carries its own
          // internal RAW pointer-derived position - the drag-factor/smart-
          // guide corrections applied per frame above never feed back into
          // RF's drag state. Passing the raw change through here made the
          // node visibly bounce off its corrected position on release, then
          // reconcile after the backend echo. Return the settled (last
          // corrected frame's) position instead, which is also exactly what
          // the batch below commits - local state and the wire now agree at
          // the instant of release.
          return { ...change, position: { ...settled.position } };
        }
        return change;
      });
      // R6.1 follow-up: ONE atomic batch for the WHOLE drag-end (the
      // dragged node's own settled position plus every cascaded member),
      // not the group's own moveNode call followed by N separate member
      // moveNode calls. Each individual moveNode intent publishes its own
      // scene snapshot the instant it lands - calling it once per node in
      // a group drag meant the frontend rendered N genuinely inconsistent
      // intermediate states (some members caught up, some not) in rapid
      // succession right after release, and since a frame/container's
      // bounds correctly grow to enclose whatever the CURRENT member bbox
      // is, those intermediate states visibly stretched and resettled
      // instead of just being briefly stale - a real glitch on every group
      // drag, not a cosmetic footnote. moveNodes commits every position in
      // one pass server-side and publishes exactly once.
      if (settledMoveIntents.length > 0) store.moveNodes(settledMoveIntents);
      // Guides re-derive every drag frame (legacy cleared + re-added its
      // QGraphicsLineItems per recompute); drag end always clears.
      if (sawDragging) setSmartGuideLines(frameGuides);
      else if (sawDragEnd) setSmartGuideLines([]);
      setNodes((current) => applyNodeChanges([...scaled, ...memberChanges], current));
    },
    [nodes, scene.dragFactor, scene.smartGuides, reactFlow, store],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        store.connectNodes(connection.source, connection.target);
      }
    },
    [store],
  );

  const onDelete = useCallback(
    ({ nodes: deletedNodes, edges: deletedEdges }: { nodes: Node[]; edges: Edge[] }) => {
      // Chat nodes delete through their own reparent-preserving intent
      // (backend/canvas.py's delete_chat_node) so the Delete key matches the
      // context menu's "Delete Node" exactly - a plain cascade-delete would
      // orphan every child branch instead of splicing it back to the
      // grandparent. Every other kind still uses the generic cascade-delete.
      const chatNodeIds: string[] = [];
      const otherNodeIds: string[] = [];
      for (const deleted of deletedNodes) {
        const flowNode = nodes.find((n) => n.id === deleted.id);
        (flowNode?.type === "chat" ? chatNodeIds : otherNodeIds).push(deleted.id);
      }
      for (const id of chatNodeIds) store.deleteChatNode(id);
      store.removeNodes(otherNodeIds);
      // Skip edges an already-deleted node takes with it server-side
      // (cascade-delete or the chat reparent both manage their own edges).
      const dying = new Set(deletedNodes.map((n) => n.id));
      store.removeEdges(
        deletedEdges.filter((e) => !dying.has(e.source) && !dying.has(e.target)).map((e) => e.id),
      );
    },
    [store, nodes],
  );

  const { screenToFlowPosition } = reactFlow;
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
    <div className="scene-canvas" ref={canvasWrapperRef} onDoubleClick={onDoubleClick}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        onDelete={onDelete}
        onMove={onMove}
        // R5.1: mirrors React Flow's own selection state into the store so
        // PluginPicker can attach "which node was selected" to executePlugin
        // without either component reaching into the other's internals.
        onSelectionChange={({ nodes: sel }) => handleSelectionChange(store, sel)}
        onEdgeMouseEnter={(_event, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        snapToGrid={scene.snapToGrid}
        snapGrid={[grid.gridSize, grid.gridSize]}
        // Double-click is the R1 create-node gesture (wrapper onDoubleClick);
        // RF's default dblclick-zoom would consume it before it ever bubbles.
        zoomOnDoubleClick={false}
        fitView
        minZoom={0.1}
        maxZoom={2.5}
        deleteKeyCode={["Delete", "Backspace"]}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: "default" }}
      >
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
      </ReactFlow>
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
}: {
  store: SceneStore;
  onOpenDocumentView: (markdown: string, sourceLabel: string) => void;
}) {
  return <CanvasInner store={store} onOpenDocumentView={onOpenDocumentView} />;
}
