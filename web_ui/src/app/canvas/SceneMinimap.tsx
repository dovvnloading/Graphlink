import { createContext, memo, useCallback, useContext, useMemo, useState, useSyncExternalStore } from "react";
import type { MouseEvent } from "react";
import { MiniMap, Panel, useReactFlow, type MiniMapNodeProps } from "@xyflow/react";
import { minimapMeta, minimapState, type MinimapNodeMeta } from "./minimapNodeMeta";
import type { SceneStore } from "./sceneStore";
import { motionDuration } from "../reducedMotion";

/**
 * The canvas minimap.
 *
 * WHAT IT WAS. React Flow's stock <MiniMap> with three colour props on it.
 * Every node rendered as the same grey rounded rectangle - the one exception
 * being frames, containers and notes, which showed their user-assigned
 * colour. On a canvas whose entire premise is MIXED content, the one surface
 * meant to answer "where is the thing I am looking for" flattened a chat, a
 * chart, a running agent and a failed build into identical blobs. There was
 * no chrome around it, so on a small window it was an unlabelled box
 * permanently occupying a corner with no way to put it away, and it rendered
 * at full size over an empty canvas, mapping nothing.
 *
 * WHAT IT IS FOR. Two questions, and the second is the one a stock minimap
 * never answers:
 *
 *   1. Where is it? - answered by giving each CATEGORY of node its own
 *      treatment, so the shape of the graph is readable rather than uniform.
 *   2. What needs me? - answered by surfacing run state. A build waiting on
 *      a tool approval, an agent that asked a question, a node that failed:
 *      three screens away, these are invisible, and they are exactly what
 *      you would want a map for. The header carries the count and jumps to
 *      the first one.
 *
 * WHY CATEGORIES, NOT KINDS. There are fifteen node kinds and this palette
 * is a deliberate monochrome - no hue to spend, and fifteen distinguishable
 * greys do not exist at minimap scale. Four categories do, separated by fill
 * weight, and groups are drawn as outlines because a frame IS an outline
 * around other things. That is legible without a legend, which is the test a
 * legend-less map has to pass.
 *
 * WHY THE ENGINE STAYS. React Flow's MiniMap owns the viewport rectangle,
 * the flow-to-map projection, drag-to-pan and scroll-to-zoom - fiddly things
 * that are correct today. This replaces everything it draws (nodeComponent)
 * and everything around it (a real panel), and keeps the parts that work.
 */

/**
 * The per-node lookup, passed by context rather than closed over by the
 * nodeComponent. React Flow takes nodeComponent as a component TYPE, so a
 * closure over the scene would be a new type on every scene update and would
 * remount every node in the map on every keystroke of a streaming reply.
 */
const MinimapMetaContext = createContext<Map<string, MinimapNodeMeta>>(new Map());

const EMPTY_META: MinimapNodeMeta = { category: "conversation", state: "idle" };

/**
 * One node on the map. Fill weight carries the category; the stroke carries
 * run state; groups are outlines. Everything is a class rather than an
 * inline colour so the whole map re-themes with the palette and so the
 * running pulse can honour prefers-reduced-motion in CSS.
 */
const MinimapNode = memo(function MinimapNode({
  id,
  x,
  y,
  width,
  height,
  selected,
}: MiniMapNodeProps) {
  const meta = useContext(MinimapMetaContext).get(id) ?? EMPTY_META;
  const className =
    `scene-minimap-node scene-minimap-node-${meta.category} scene-minimap-node-${meta.state}` +
    (selected ? " selected" : "");
  return (
    <rect
      className={className}
      x={x}
      y={y}
      rx={6}
      ry={6}
      width={width}
      height={height}
      // A group's own colour is the one piece of real hue in this app, and
      // it is the user's own labelling system - it overrides the category
      // treatment rather than being averaged with it.
      style={meta.color ? { stroke: meta.color } : undefined}
    />
  );
});

function ChevronIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="scene-minimap-chevron">
      <path d={collapsed ? "M4 10.5 8 6.5l4 4" : "M4 6.5 8 10.5l4-4"} />
    </svg>
  );
}

const COLLAPSED_KEY = "graphlink.minimap.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    // Private windows and blocked site data both throw here. A minimap that
    // cannot remember its own state is fine; one that crashes the canvas is
    // not.
    return false;
  }
}

export function SceneMinimap({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const reactFlow = useReactFlow();
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const meta = useMemo(() => {
    const map = new Map<string, MinimapNodeMeta>();
    for (const node of scene.nodes) {
      map.set(node.id, minimapMeta(node));
    }
    return map;
  }, [scene.nodes]);

  // Nodes parked on a human decision, in canvas order, so "jump to the next
  // one" is stable rather than depending on Map iteration luck.
  const waiting = useMemo(
    () => scene.nodes.filter((node) => minimapState(node) === "attention"),
    [scene.nodes],
  );

  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // See readCollapsed: remembering is a convenience, not a contract.
      }
      return next;
    });
  }, []);

  // The zoom is passed explicitly and deliberately. React Flow's setCenter
  // defaults `zoom` to maxZoom when it is omitted, so the obvious-looking
  // call - setCenter(x, y, { duration }) - slams the canvas to 250% on every
  // jump. Holding the current zoom is what makes this a pan rather than a
  // teleport. (The pin jump passes `zoom: 1` for the same reason; it wants a
  // fixed zoom, this wants the one you were already at.)
  const centerOn = useCallback(
    (x: number, y: number) => {
      reactFlow.setCenter(x, y, { zoom: reactFlow.getZoom(), duration: motionDuration(300) });
    },
    [reactFlow],
  );

  // Clicking the map moves the viewport there. The stock minimap only
  // supported dragging the viewport rectangle, which means the fastest way
  // to cross a large graph was a gesture you had to already know about.
  const onMapClick = useCallback(
    (_event: MouseEvent, position: { x: number; y: number }) => centerOn(position.x, position.y),
    [centerOn],
  );

  // Selection is React Flow's, mirrored INTO the store by the canvas's own
  // onSelectionChange - so it is set the same way the command palette's
  // select-all does (commands.ts), not by reaching into sceneStore, which
  // has no selection setter to reach for.
  const selectOnly = useCallback(
    (id: string) => {
      reactFlow.setNodes((nodes) => nodes.map((node) => ({ ...node, selected: node.id === id })));
    },
    [reactFlow],
  );

  const onMapNodeClick = useCallback(
    (_event: MouseEvent, node: { id: string }) => {
      const row = scene.nodes.find((candidate) => candidate.id === node.id);
      if (!row) return;
      selectOnly(row.id);
      centerOn(row.x, row.y);
    },
    [centerOn, scene.nodes, selectOnly],
  );

  const jumpToWaiting = useCallback(() => {
    const first = waiting[0];
    if (!first) return;
    selectOnly(first.id);
    centerOn(first.x, first.y);
  }, [centerOn, selectOnly, waiting]);

  // An empty canvas has nothing to map. The stock minimap rendered its full
  // box regardless, so a fresh session opened with an empty grey rectangle
  // pinned to the corner.
  if (scene.nodes.length === 0) return null;

  const nodeCount = scene.nodes.length;

  return (
    <MinimapMetaContext.Provider value={meta}>
      <Panel
        position="bottom-right"
        className={"scene-minimap-panel" + (collapsed ? " collapsed" : "")}
      >
        <div className="scene-minimap-header">
          <span className="scene-minimap-count">
            {nodeCount} {nodeCount === 1 ? "node" : "nodes"}
          </span>
          {waiting.length > 0 && (
            <button
              type="button"
              className="scene-minimap-waiting"
              title="Go to the first node waiting on you"
              onClick={jumpToWaiting}
            >
              {waiting.length} waiting
            </button>
          )}
          <button
            type="button"
            className="scene-minimap-toggle"
            aria-label={collapsed ? "Expand minimap" : "Collapse minimap"}
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
          >
            <ChevronIcon collapsed={collapsed} />
          </button>
        </div>
        {!collapsed && (
          <MiniMap
            pannable
            zoomable
            className="scene-minimap"
            ariaLabel="Canvas minimap"
            nodeComponent={MinimapNode}
            maskStrokeWidth={2}
            onClick={onMapClick}
            onNodeClick={onMapNodeClick}
          />
        )}
      </Panel>
    </MinimapMetaContext.Provider>
  );
}
