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
import { SceneStore, scaleDragPosition } from "./sceneStore";

/**
 * The React Flow canvas (Qt-removal plan R1) - the QGraphicsScene/ChatView
 * successor. R1 scope: pan/zoom, model-driven grid (size/style/color/opacity
 * + snap), node drag with the drag-speed factor, edges, selection + delete,
 * minimap, an LOD threshold, navigation pins. Placeholder nodes only; real
 * node types land per-increment in R3.
 */

// Below this zoom the node body collapses to its title bar - the R1 seed of
// the Qt canvas's LOD thresholds (full LOD tiers return with real nodes).
const LOD_ZOOM_THRESHOLD = 0.5;

const GRID_VARIANTS: Record<string, BackgroundVariant> = {
  Dots: BackgroundVariant.Dots,
  Lines: BackgroundVariant.Lines,
  Cross: BackgroundVariant.Cross,
};

type PlaceholderNode = Node<{ title: string }, "placeholder">;

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

const NODE_TYPES = { placeholder: PlaceholderNodeView };

function toFlowNodes(scene: SceneState): PlaceholderNode[] {
  return scene.nodes.map((n) => ({
    id: n.id,
    type: "placeholder" as const,
    position: { x: n.x, y: n.y },
    data: { title: n.title },
  }));
}

function toFlowEdges(scene: SceneState): Edge[] {
  return scene.edges.map((e) => ({ id: e.id, source: e.source, target: e.target }));
}

function CanvasInner({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const grid = useSyncExternalStore(store.subscribe, store.getGrid);

  // Local node state exists so dragging is fluid; backend snapshots are the
  // truth and reconcile in whenever nothing is being dragged. dragStartRef
  // powers the drag-speed scaling contract (see scaleDragPosition).
  const [nodes, setNodes] = useState<PlaceholderNode[]>([]);
  const dragStartRef = useRef<Map<string, { x: number; y: number }>>(new Map());
  const draggingRef = useRef(false);

  useEffect(() => {
    if (!draggingRef.current) setNodes(toFlowNodes(scene));
  }, [scene]);

  const edges = useMemo(() => toFlowEdges(scene), [scene]);

  const onNodesChange = useCallback(
    (changes: NodeChange<PlaceholderNode>[]) => {
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
      store.removeNodes(deletedNodes.map((n) => n.id));
      // Skip edges an already-deleted node takes with it server-side.
      const dying = new Set(deletedNodes.map((n) => n.id));
      store.removeEdges(
        deletedEdges.filter((e) => !dying.has(e.source) && !dying.has(e.target)).map((e) => e.id),
      );
    },
    [store],
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
