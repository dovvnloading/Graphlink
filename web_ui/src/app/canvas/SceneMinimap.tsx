import { createContext, memo, useCallback, useContext, useId, useMemo, useState, useSyncExternalStore } from "react";
import type { CSSProperties, MouseEvent } from "react";
import { MiniMap, Panel, useReactFlow, useStore, type MiniMapNodeProps, type ReactFlowState } from "@xyflow/react";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import { motionDuration } from "../reducedMotion";
import { hiddenGroupMemberIds } from "./collapsedGroups";
import { minimapMeta, type MinimapNodeMeta } from "./minimapNodeMeta";
import type { SceneStore } from "./sceneStore";
import "./SceneMinimap.css";

interface MapEntry extends MinimapNodeMeta {
  sources: string[];
  label: string;
}

const MinimapMetaContext = createContext<ReadonlyMap<string, MapEntry>>(new Map());
const WIDE_MAP = { width: 172, height: 92 };
const COMPACT_MAP = { width: 140, height: 74 };
const STATE_LABEL = { idle: "", running: "running", failed: "failed", attention: "waiting for you" };

const COLLAPSED_KEY = "graphlink.minimap.collapsed";

/** Read the same live, measured rectangles as the built-in minimap, including during a drag. */
const IncomingLinks = memo(function IncomingLinks({
  sources, x, y,
}: { sources: string[]; x: number; y: number }) {
  const path = useStore(useCallback((state: ReactFlowState) => {
    const segments: string[] = [];
    for (const id of sources) {
      const node = state.nodeLookup.get(id);
      if (!node || node.hidden) continue;
      const width = node.measured.width ?? node.width ?? node.initialWidth;
      const height = node.measured.height ?? node.height ?? node.initialHeight;
      if (!width || !height) continue;
      const position = node.internals.positionAbsolute;
      segments.push(`M${position.x + width / 2} ${position.y + height} L${x} ${y}`);
    }
    return segments.join(" ");
  }, [sources, x, y]));
  return path ? <path className="scene-minimap-links" d={path} /> : null;
});

const MinimapNode = memo(function MinimapNode({
  id, x, y, width, height, selected, onClick,
}: MiniMapNodeProps) {
  const entry = useContext(MinimapMetaContext).get(id);
  if (!entry) return null;
  const stateLabel = STATE_LABEL[entry.state];
  return (
    <>
      {entry.sources.length > 0 && (
        <IncomingLinks sources={entry.sources} x={x + width / 2} y={y} />
      )}
      <rect
        className={`scene-minimap-node scene-minimap-node-${entry.category} scene-minimap-node-${entry.state}${selected ? " selected" : ""}`}
        x={x} y={y} width={width} height={height} rx={4}
        style={entry.color ? { "--map-node-color": entry.color } as CSSProperties : undefined}
        onClick={(event) => onClick?.(event, id)}
      >
        <title>{stateLabel ? `${entry.label} · ${stateLabel}` : entry.label}</title>
      </rect>
    </>
  );
});

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function SceneMinimap({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const reactFlow = useReactFlow();
  const compact = useStore((state) => state.width > 0 && state.width <= 700);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const mapId = useId();

  const { entries, waiting } = useMemo(() => {
    const entries = new Map<string, MapEntry>();
    const waiting: SceneNodeRow[] = [];
    const hidden = hiddenGroupMemberIds(scene.nodes);
    for (const node of scene.nodes) {
      if (node.isDocked || hidden.has(node.id)) continue;
      const meta = minimapMeta(node);
      entries.set(node.id, {
        ...meta, sources: [], label: node.title || node.kind.replaceAll("_", " "),
      });
      if (meta.state === "attention") waiting.push(node);
    }
    for (const edge of scene.edges) {
      if (entries.has(edge.source)) entries.get(edge.target)?.sources.push(edge.source);
    }
    return { entries, waiting };
  }, [scene.nodes, scene.edges]);

  function toggleCollapsed() {
    setCollapsed(!collapsed);
    try {
      window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "0" : "1");
    } catch {
      // A blocked storage write only affects restoration on the next launch.
    }
  }

  const centerOn = useCallback((x: number, y: number) => {
    reactFlow.setCenter(x, y, { zoom: reactFlow.getZoom(), duration: motionDuration(300) });
  }, [reactFlow]);

  const selectAndCenter = useCallback((id: string, fallback: { x: number; y: number }) => {
    reactFlow.setNodes((nodes) => nodes.map((node) =>
      Boolean(node.selected) === (node.id === id) ? node : { ...node, selected: node.id === id }
    ));
    if (reactFlow.getInternalNode(id)) {
      const bounds = reactFlow.getNodesBounds([id]);
      centerOn(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    } else {
      centerOn(fallback.x, fallback.y);
    }
  }, [centerOn, reactFlow]);

  const onMapClick = useCallback(
    (_event: MouseEvent, position: { x: number; y: number }) => centerOn(position.x, position.y),
    [centerOn],
  );

  const onMapNodeClick = useCallback(
    (event: MouseEvent, node: { id: string; position: { x: number; y: number } }) => {
      event.stopPropagation();
      selectAndCenter(node.id, node.position);
    },
    [selectAndCenter],
  );

  if (entries.size === 0) return null;
  const size = compact ? COMPACT_MAP : WIDE_MAP;

  return (
    <MinimapMetaContext.Provider value={entries}>
      <Panel
        position="bottom-right"
        className={`scene-minimap-panel${collapsed ? " collapsed" : ""}`}
        role="group"
        aria-label={`Canvas overview, ${entries.size} ${entries.size === 1 ? "node" : "nodes"}`}
        style={{ "--map-width": `${size.width}px` } as CSSProperties}
      >
        <div id={mapId} hidden={collapsed} className="scene-minimap-map"
          title="Click a node to select it. Drag to pan or scroll to zoom.">
          {!collapsed && (
            <MiniMap
              pannable zoomable
              style={size}
              className="scene-minimap"
              ariaLabel="Canvas minimap"
              nodeComponent={MinimapNode}
              onClick={onMapClick}
              onNodeClick={onMapNodeClick}
              offsetScale={8}
            />
          )}
        </div>
        <div className="scene-minimap-footer">
          <button
            type="button"
            className="scene-minimap-toggle"
            aria-label={collapsed ? "Expand minimap" : "Collapse minimap"}
            aria-expanded={!collapsed}
            aria-controls={mapId}
            title={collapsed ? "Expand minimap" : "Collapse minimap"}
            onClick={toggleCollapsed}
          >
            <span className="scene-minimap-count">{entries.size} {entries.size === 1 ? "node" : "nodes"}</span>
            <svg aria-hidden="true" viewBox="0 0 16 16" className="scene-minimap-chevron">
              <path d={collapsed ? "m4 10 4-4 4 4" : "m4 6 4 4 4-4"} />
            </svg>
          </button>
          {waiting.length > 0 && (
            <button
              type="button"
              className="scene-minimap-waiting"
              title="Go to the first node waiting on you"
              onClick={() => selectAndCenter(waiting[0].id, waiting[0])}
            >
              {waiting.length} waiting
            </button>
          )}
        </div>
      </Panel>
    </MinimapMetaContext.Provider>
  );
}
