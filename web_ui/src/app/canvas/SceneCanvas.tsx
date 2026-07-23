import {
  Background,
  BackgroundVariant,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  useReactFlow,
  useStore,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  applyNodeChanges,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { SceneState } from "../../lib/bridge-core/generated/scene-state";
import { ChatNodeView, type ChatFlowNode } from "./ChatNodeView";
import { CodeNodeView, type CodeFlowNode } from "./CodeNodeView";
import { ConversationNodeView, type ConversationFlowNode } from "./ConversationNodeView";
import { DocumentNodeView, type DocumentFlowNode } from "./DocumentNodeView";
import { HtmlNodeView, type HtmlFlowNode } from "./HtmlNodeView";
import { ImageNodeView, type ImageFlowNode } from "./ImageNodeView";
import { ThinkingNodeView, type ThinkingFlowNode } from "./ThinkingNodeView";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { SceneStore, scaleDragPosition } from "./sceneStore";

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
type SceneFlowNode =
  | PlaceholderNode
  | ChatFlowNode
  | CodeFlowNode
  | DocumentFlowNode
  | ThinkingFlowNode
  | HtmlFlowNode
  | ImageFlowNode
  | ConversationFlowNode;

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
      {!collapsed && <div className="scene-node-body">placeholder — real nodes land in R3</div>}
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
};

function toFlowNodes(scene: SceneState, store: SceneStore): SceneFlowNode[] {
  // Looked up per-chat-node below to build dockedChildren - a docked node is
  // omitted from the returned array entirely (see the "thinking" branch), so
  // this is the only remaining way a chat node's dock badge/menu can find it.
  const nodesById = new Map(scene.nodes.map((n) => [n.id, n]));
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
      flowNodes.push({
        id: n.id,
        type: "chat" as const,
        position: { x: n.x, y: n.y },
        data: {
          content: n.content,
          isUser: n.isUser,
          isCollapsed: n.isCollapsed,
          dockedChildren,
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.deleteChatNode(n.id),
          onUndockChild: (childId: string) => store.setNodeDocked(childId, false),
        },
      });
      continue;
    }
    if (n.kind === "code") {
      flowNodes.push({
        id: n.id,
        type: "code" as const,
        position: { x: n.x, y: n.y },
        data: {
          code: n.code,
          language: n.language,
          onDelete: () => store.removeNodes([n.id]),
        },
      });
      continue;
    }
    if (n.kind === "document") {
      flowNodes.push({
        id: n.id,
        type: "document" as const,
        position: { x: n.x, y: n.y },
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
        data: {
          thinkingText: n.content,
          onDock: () => store.setNodeDocked(n.id, true),
          onDelete: () => store.removeNodes([n.id]),
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
      flowNodes.push({
        id: n.id,
        type: "html" as const,
        position: { x: n.x, y: n.y },
        data: {
          htmlContent: n.content,
          isCollapsed: n.isCollapsed,
          onToggleCollapse: () => store.setChatCollapsed(n.id, !n.isCollapsed),
          onDelete: () => store.removeNodes([n.id]),
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
        data: {
          imageAssetId: n.imageAssetId,
          prompt: n.content,
          onDelete: () => store.removeNodes([n.id]),
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

function toFlowEdges(scene: SceneState): Edge[] {
  // An edge pointing at a docked node must not render either - mirrors the
  // legacy connection-item self-suppression when its end node is docked.
  const dockedNodeIds = new Set(scene.nodes.filter((n) => n.isDocked).map((n) => n.id));
  return scene.edges
    .filter((e) => !dockedNodeIds.has(e.target))
    .map((e) => ({ id: e.id, source: e.source, target: e.target }));
}

function CanvasInner({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const grid = useSyncExternalStore(store.subscribe, store.getGrid);

  // Local node state exists so dragging is fluid; backend snapshots are the
  // truth and reconcile in whenever nothing is being dragged. dragStartRef
  // powers the drag-speed scaling contract (see scaleDragPosition).
  const [nodes, setNodes] = useState<SceneFlowNode[]>([]);
  const dragStartRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const draggingRef = useRef(false);

  useEffect(() => {
    if (!draggingRef.current) setNodes(toFlowNodes(scene, store));
  }, [scene, store]);

  const edges = useMemo(() => toFlowEdges(scene), [scene]);

  const onNodesChange = useCallback(
    (changes: NodeChange<SceneFlowNode>[]) => {
      const scaled = changes.map((change) => {
        if (change.type !== "position" || !change.position) return change;
        if (change.dragging) {
          draggingRef.current = true;
          let start = dragStartRef.current.get(change.id);
          if (!start) {
            const node = nodes.find((n) => n.id === change.id);
            start = node ? { ...node.position } : { ...change.position };
            dragStartRef.current.set(change.id, start);
          }
          return {
            ...change,
            position: scaleDragPosition(start, change.position, scene.dragFactor),
          };
        }
        // Drag end: commit the node's final (already-scaled) position.
        draggingRef.current = false;
        const settled = nodes.find((n) => n.id === change.id);
        if (settled) store.moveNode(change.id, settled.position.x, settled.position.y);
        dragStartRef.current.delete(change.id);
        return change;
      });
      setNodes((current) => applyNodeChanges(scaled, current));
    },
    [nodes, scene.dragFactor, store],
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

  const { screenToFlowPosition } = useReactFlow();
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
    <div className="scene-canvas" onDoubleClick={onDoubleClick}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        onDelete={onDelete}
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
        <MiniMap pannable zoomable className="scene-minimap" />
      </ReactFlow>
    </div>
  );
}

// The ReactFlowProvider lives in App (R2): the app bar's zoom/fit buttons and
// the R2.4 PinOverlay (jump-to-pin via setCenter) need the same React Flow
// instance the canvas renders into - pins moved out to their own overlay,
// ported with real search + rename/note editing (R2.4).
export function SceneCanvas({ store }: { store: SceneStore }) {
  return <CanvasInner store={store} />;
}
