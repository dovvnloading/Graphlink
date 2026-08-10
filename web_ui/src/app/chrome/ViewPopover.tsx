import { useSyncExternalStore } from "react";
import { FILTERABLE_NODE_KINDS } from "../canvas/SceneCanvas";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover } from "../overlays/overlays";

// ADR-012 stage 12.5: display labels for FILTERABLE_NODE_KINDS' own raw
// kind strings - most already read fine title-cased, but "web_research"/
// "code_sandbox"/"pycoder" need a human label. Kept local to this file (not
// exported from SceneCanvas.tsx) since nothing else needs a display label
// for a node kind today.
const FILTER_KIND_LABELS: Record<string, string> = {
  chat: "Chat",
  code: "Code",
  document: "Document",
  thinking: "Thinking",
  html: "HTML",
  image: "Image",
  conversation: "Conversation",
  web_research: "Web Research",
  plan: "Plan",
  artifact: "Artifact",
  gitlink: "Gitlink",
  pycoder: "Py-Coder",
  code_sandbox: "Code Sandbox",
  note: "Note",
  chart: "Chart",
};

// ADR-012 stage 12.5: mirrors backend/domain/branches.py's own
// BRANCH_STATUS_VALUES exactly (SceneDocument.set_branch_status's legal
// values) - "active" is every node's default (including every non-chat
// kind, which never has a real branch status of its own - see graph.py's
// own wire-builder), so it reads first as the common case.
const FILTER_STATUS_VALUES = ["active", "accepted", "rejected", "superseded"] as const;
const FILTER_STATUS_LABELS: Record<string, string> = {
  active: "Active",
  accepted: "Accepted",
  rejected: "Rejected",
  superseded: "Superseded",
};

/**
 * The View popover (Qt-removal plan R2, audit P5): ONE surface consolidating
 * the drag-speed, grid-control, and font-control islands - their controls,
 * their presets (published by the backend), their intent names - instead of
 * three separately-positioned popover cards.
 */

export function ViewPopover({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const grid = useSyncExternalStore(store.subscribe, store.getGrid);
  const dragConfig = useSyncExternalStore(store.subscribe, store.getDragConfig);
  const fontConfig = useSyncExternalStore(store.subscribe, store.getFontConfig);
  // ADR-002 Workstream 1 ("Branch status and lifecycle"): UNLIKE every
  // other value read here, this one is NOT part of `scene` (backend-synced
  // state) - it is sceneStore's own local, unpersisted UI-state field (see
  // that field's own comment for why: a view-only review lens, the same
  // posture as "Hide Other Branches", not a real document property).
  const focusAcceptedPaths = useSyncExternalStore(store.subscribe, store.getFocusAcceptedPaths);
  // ADR-012 stage 12.5: "node filter-by-kind/status" - same local,
  // unpersisted posture as focusAcceptedPaths just above, see
  // sceneStore.ts's own comment on filterKinds/filterStatuses.
  const filterKinds = useSyncExternalStore(store.subscribe, store.getFilterKinds);
  const filterStatuses = useSyncExternalStore(store.subscribe, store.getFilterStatuses);

  const dragPercent = Math.round(scene.dragFactor * 100);

  return (
    <Popover name="view" label="View settings" className="view-popover" anchored>
      <section className="view-section" aria-label="Drag speed">
        <p className="view-section-title">DRAG</p>
        <input
          type="range"
          className="view-slider"
          min={dragConfig.percentMin}
          max={dragConfig.percentMax}
          value={dragPercent}
          aria-label="Drag speed"
          onChange={(e) => store.setDragFactor(Number(e.target.value) / 100)}
        />
        <div className="view-row">
          {dragConfig.percentPresets.map((percent) => (
            <button
              key={percent}
              type="button"
              className={"view-preset-btn" + (percent === dragPercent ? " active" : "")}
              onClick={() => store.setDragFactor(percent / 100)}
            >
              {percent}%
            </button>
          ))}
        </div>
      </section>

      <section className="view-section" aria-label="Grid">
        <p className="view-section-title">GRID</p>
        <input
          type="range"
          className="view-slider"
          min={0}
          max={100}
          value={grid.gridOpacityPercent}
          aria-label="Grid opacity"
          onChange={(e) => store.setGridOpacityPercent(Number(e.target.value))}
        />
        <div className="view-row">
          {grid.sizePresets.map((size) => (
            <button
              key={size}
              type="button"
              className={"view-preset-btn" + (size === grid.gridSize ? " active" : "")}
              onClick={() => store.setGridSize(size)}
            >
              {size}px
            </button>
          ))}
        </div>
        <div className="view-row">
          {grid.stylePresets.map((style) => (
            <button
              key={style}
              type="button"
              className={"view-preset-btn" + (style === grid.gridStyle ? " active" : "")}
              onClick={() => store.setGridStyle(style)}
            >
              {style}
            </button>
          ))}
        </div>
        <div className="view-row">
          {grid.colorPresets.map((color) => (
            <button
              key={color}
              type="button"
              className={"view-color-swatch" + (color === grid.gridColor ? " active" : "")}
              style={{ backgroundColor: color }}
              aria-label={`Grid color ${color}`}
              onClick={() => store.setGridColor(color)}
            />
          ))}
        </div>
        <label className="view-check-row">
          <input
            type="checkbox"
            checked={scene.snapToGrid}
            onChange={(e) => store.setSnapToGrid(e.target.checked)}
          />
          Snap to Grid
        </label>
        {/* R7.5b-1: same view-check-row pattern as Snap to Grid above. */}
        <label className="view-check-row">
          <input
            type="checkbox"
            checked={scene.fadeConnectionsEnabled}
            onChange={(e) => store.setFadeConnections(e.target.checked)}
          />
          Fade Connections
        </label>
        {/* R7.5b-2: same view-check-row pattern again. */}
        <label className="view-check-row">
          <input
            type="checkbox"
            checked={scene.orthogonalRouting}
            onChange={(e) => store.setOrthogonalConnections(e.target.checked)}
          />
          Orthogonal Routing
        </label>
        {/* R7.5b-3: the fourth and final legacy grid-control toggle. */}
        <label className="view-check-row">
          <input
            type="checkbox"
            checked={scene.smartGuides}
            onChange={(e) => store.setSmartGuides(e.target.checked)}
          />
          Smart Guides
        </label>
      </section>

      <section className="view-section" aria-label="Font">
        <p className="view-section-title">FONT</p>
        <select
          className="view-select"
          value={scene.fontFamily}
          aria-label="Font family"
          onChange={(e) => store.setFontFamily(e.target.value)}
        >
          {fontConfig.fontFamilies.map((family) => (
            <option key={family} value={family}>
              {family}
            </option>
          ))}
        </select>
        <input
          type="range"
          className="view-slider"
          min={fontConfig.sizeMin}
          max={fontConfig.sizeMax}
          value={scene.fontSizePt}
          aria-label="Font size"
          onChange={(e) => store.setFontSize(Number(e.target.value))}
        />
        <div className="view-row">
          {fontConfig.colorPresets.map((color) => (
            <button
              key={color}
              type="button"
              className={"view-color-swatch" + (color === scene.fontColor ? " active" : "")}
              style={{ backgroundColor: color }}
              aria-label={`Font color ${color}`}
              onClick={() => store.setFontColor(color)}
            />
          ))}
        </div>
      </section>

      <section className="view-section" aria-label="Branches">
        <p className="view-section-title">BRANCHES</p>
        {/* ADR-002 Workstream 1 ("Branch status and lifecycle"): dims every
            node outside the accepted paths (rejected/superseded branches
            and their descendants, unless an explicit "accepted" override
            reactivates a sub-branch) - the whole-graph counterpart to a
            single chat node's own "Hide Other Branches" menu action. Same
            view-check-row pattern as every other toggle in this section,
            but backed by sceneStore's own local focusAcceptedPaths field
            rather than `scene` - see that field's own comment. */}
        <label className="view-check-row">
          <input
            type="checkbox"
            checked={focusAcceptedPaths}
            onChange={(e) => store.setFocusAcceptedPaths(e.target.checked)}
          />
          Focus Accepted Paths
        </label>
      </section>

      <section className="view-section" aria-label="Filter">
        <p className="view-section-title">FILTER</p>
        {/* ADR-012 stage 12.5: multi-select toggle chips, same view-row/
            view-preset-btn markup every other section's preset row already
            uses - "active" here means "toggled into the filter set," not
            mutual exclusion (unlike, say, the grid-size presets above, a
            click here only ever flips ITS OWN membership in
            sceneStore's filterKinds Set, see toggleFilterKind's own doc).
            An empty set (no chip active) means no filter at all - every
            node shows at full opacity, exactly as before this stage. */}
        <div className="view-row" role="group" aria-label="Filter by node kind">
          {FILTERABLE_NODE_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              className={"view-preset-btn" + (filterKinds.has(kind) ? " active" : "")}
              aria-pressed={filterKinds.has(kind)}
              onClick={() => store.toggleFilterKind(kind)}
            >
              {FILTER_KIND_LABELS[kind]}
            </button>
          ))}
        </div>
        <div className="view-row" role="group" aria-label="Filter by branch status">
          {FILTER_STATUS_VALUES.map((status) => (
            <button
              key={status}
              type="button"
              className={"view-preset-btn" + (filterStatuses.has(status) ? " active" : "")}
              aria-pressed={filterStatuses.has(status)}
              onClick={() => store.toggleFilterStatus(status)}
            >
              {FILTER_STATUS_LABELS[status]}
            </button>
          ))}
        </div>
      </section>
    </Popover>
  );
}
