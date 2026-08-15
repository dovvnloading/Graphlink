import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { FILTERABLE_NODE_KINDS } from "../canvas/SceneCanvas";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover } from "../overlays/overlays";
import { CustomSelect } from "./CustomSelect";

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

// The values every control returns to on "Reset to defaults". These mirror
// the backend's own construction-time defaults (graphlink_grid_view_settings
// .py's DEFAULT_GRID_* constants; SceneDocument's drag/font defaults) - the
// reset fires the ordinary intents with these values rather than needing a
// backend reset endpoint, so it behaves exactly like the user setting each
// control by hand.
const DEFAULTS = {
  dragFactor: 1,
  gridSize: 10,
  gridOpacityPercent: 30,
  gridStyle: "Dots",
  gridColor: "#555555",
  fontFamily: "Segoe UI",
  fontSizePt: 9,
  fontColor: "#F0F0F0",
} as const;

// The grid-size slider's range. The presets (10/20/50/100) remain the
// landmark values; the slider makes everything between them reachable,
// which the preset-only conversion had silently dropped (the legacy control
// was a full spinbox). Floor of 4 keeps the background pattern drawable -
// see intents_grid.py's own clamp, which enforces the same bound
// server-side.
const GRID_SIZE_MIN = 4;
const GRID_SIZE_MAX = 120;

/**
 * Keeps a continuous control responsive while sending far fewer intents.
 *
 * A range input or a native colour picker fires change events for every
 * pixel of pointer movement, and each intent here triggers a full state
 * republish - so dragging one slider used to put ~100 round trips on the
 * wire. This shows the in-flight value immediately and commits the last
 * one after a short pause, the same debounce posture the canvas already
 * uses for viewport reporting.
 *
 * Pending state clears whenever a fresh value arrives with nothing in
 * flight, so a server-side clamp (grid spacing, drag factor and font size
 * are all clamped) is always what ends up displayed - never a local value
 * the backend rejected.
 */
function useDebouncedSetting<T>(remote: T, commit: (value: T) => void, delayMs = 120) {
  const [pending, setPending] = useState<T | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commitRef = useRef(commit);
  useEffect(() => {
    commitRef.current = commit;
  }, [commit]);
  useEffect(() => {
    if (timerRef.current === null) setPending(null);
  }, [remote]);
  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );
  const set = useCallback(
    (value: T) => {
      setPending(value);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        commitRef.current(value);
      }, delayMs);
    },
    [delayMs],
  );
  return [pending === null ? remote : pending, set] as const;
}

/** A slider header: what the value is, and what it currently reads. */
function FieldRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="view-field-row">
      <span className="view-field-label">{label}</span>
      <span className="view-field-value">{value}</span>
    </div>
  );
}

/** One toggle with an explanatory hint, the Settings checkbox-row idiom. */
function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="view-toggle-row">
      {/* Explicit aria-label: the wrapping label would otherwise fold the
          hint into the checkbox's accessible NAME, when the hint is a
          description of the behaviour, not part of what the control is
          called. */}
      <input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="view-toggle-text">
        <span className="view-toggle-label">{label}</span>
        <span className="view-toggle-hint">{hint}</span>
      </span>
    </label>
  );
}

/** Preset color swatches plus a free-choice picker at the end of the row. */
function SwatchRow({
  presets,
  current,
  ariaPrefix,
  onPick,
}: {
  presets: readonly string[];
  current: string;
  ariaPrefix: string;
  onPick: (color: string) => void;
}) {
  const currentIsPreset = presets.includes(current);
  return (
    <div className="view-row" role="group" aria-label={`${ariaPrefix} presets`}>
      {presets.map((color) => (
        <button
          key={color}
          type="button"
          className={"view-color-swatch" + (color === current ? " active" : "")}
          style={{ backgroundColor: color }}
          title={color}
          aria-label={`${ariaPrefix} ${color}`}
          onClick={() => onPick(color)}
        />
      ))}
      {/* The custom-color affordance the preset-only conversion dropped: the
          backend accepts any hex (set_grid_color/set_font store the string
          verbatim), so limiting the UI to five fixed swatches was purely a
          porting gap. A native color input, styled as one more swatch,
          carries the current CUSTOM value when one is active. */}
      <label
        className={"view-color-swatch view-color-custom" + (currentIsPreset ? "" : " active")}
        style={currentIsPreset ? undefined : { backgroundColor: current }}
        title="Custom color"
      >
        <input
          type="color"
          value={current}
          aria-label={`${ariaPrefix}, custom`}
          onChange={(e) => onPick(e.target.value)}
        />
      </label>
    </div>
  );
}

/**
 * The View popover (Qt-removal plan R2, audit P5): ONE surface consolidating
 * the drag-speed, grid-control, and font-control islands - their controls,
 * their presets (published by the backend), their intent names - instead of
 * three separately-positioned popover cards.
 *
 * Redesigned past the literal port: every slider carries a label and a live
 * value readout; grid style is a segmented control; the font family uses
 * the app's own CustomSelect (the component built precisely because bare
 * <select> was ruled off-idiom) with a live preview of the resulting node
 * typography; connection toggles live in their own CONNECTIONS section
 * rather than under GRID (an artifact of which Qt bridge happened to own
 * them); color rows gained a free-choice picker; the filter section grew
 * group labels and a clear affordance; and a footer resets everything to
 * the documented defaults.
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

  // Continuous controls commit through the debounce above; discrete ones
  // (presets, style, toggles) stay immediate - one event, one intent.
  const commitDragPercent = useCallback((percent: number) => store.setDragFactor(percent / 100), [store]);
  const [dragPercent, setDragPercent] = useDebouncedSetting(
    Math.round(scene.dragFactor * 100),
    commitDragPercent,
  );
  const commitGridSize = useCallback((size: number) => store.setGridSize(size), [store]);
  const [gridSize, setGridSize] = useDebouncedSetting(grid.gridSize, commitGridSize);
  const commitGridOpacity = useCallback((percent: number) => store.setGridOpacityPercent(percent), [store]);
  const [gridOpacity, setGridOpacity] = useDebouncedSetting(grid.gridOpacityPercent, commitGridOpacity);
  const commitGridColor = useCallback((color: string) => store.setGridColor(color), [store]);
  const [gridColor, setGridColor] = useDebouncedSetting(grid.gridColor, commitGridColor);
  const commitFontSize = useCallback((size: number) => store.setFontSize(size), [store]);
  const [fontSizePt, setFontSizePt] = useDebouncedSetting(scene.fontSizePt, commitFontSize);
  const commitFontColor = useCallback((color: string) => store.setFontColor(color), [store]);
  const [fontColor, setFontColor] = useDebouncedSetting(scene.fontColor, commitFontColor);
  const filterCount = filterKinds.size + filterStatuses.size;

  const resetAll = () => {
    store.setDragFactor(DEFAULTS.dragFactor);
    store.setGridSize(DEFAULTS.gridSize);
    store.setGridOpacityPercent(DEFAULTS.gridOpacityPercent);
    store.setGridStyle(DEFAULTS.gridStyle);
    store.setGridColor(DEFAULTS.gridColor);
    store.setSnapToGrid(false);
    store.setSmartGuides(false);
    store.setFadeConnections(false);
    store.setOrthogonalConnections(false);
    store.setFontFamily(DEFAULTS.fontFamily);
    store.setFontSize(DEFAULTS.fontSizePt);
    store.setFontColor(DEFAULTS.fontColor);
    store.setFocusAcceptedPaths(false);
    store.clearFilters();
  };

  return (
    <Popover name="view" label="View settings" className="view-popover" anchored>
      <section className="view-section" aria-label="Canvas pan speed">
        <p className="view-section-title">Navigation</p>
        <FieldRow label="Canvas pan speed" value={`${dragPercent}%`} />
        <input
          type="range"
          className="view-slider"
          min={dragConfig.percentMin}
          max={dragConfig.percentMax}
          value={dragPercent}
          aria-label="Canvas pan speed"
          onChange={(e) => setDragPercent(Number(e.target.value))}
        />
        <div className="view-segment" role="group" aria-label="Drag speed presets">
          {dragConfig.percentPresets.map((percent) => (
            <button
              key={percent}
              type="button"
              className={"view-segment-btn" + (percent === dragPercent ? " active" : "")}
              aria-pressed={percent === dragPercent}
              onClick={() => store.setDragFactor(percent / 100)}
            >
              {percent}%
            </button>
          ))}
        </div>
      </section>

      <section className="view-section" aria-label="Grid">
        <p className="view-section-title">Grid</p>
        <FieldRow label="Spacing" value={`${gridSize}px`} />
        <input
          type="range"
          className="view-slider"
          min={GRID_SIZE_MIN}
          max={GRID_SIZE_MAX}
          value={gridSize}
          aria-label="Grid spacing"
          onChange={(e) => setGridSize(Number(e.target.value))}
        />
        <div className="view-segment" role="group" aria-label="Grid spacing presets">
          {grid.sizePresets.map((size) => (
            <button
              key={size}
              type="button"
              className={"view-segment-btn" + (size === gridSize ? " active" : "")}
              aria-pressed={size === gridSize}
              onClick={() => store.setGridSize(size)}
            >
              {size}px
            </button>
          ))}
        </div>
        <FieldRow label="Opacity" value={`${gridOpacity}%`} />
        <input
          type="range"
          className="view-slider"
          min={0}
          max={100}
          value={gridOpacity}
          aria-label="Grid opacity"
          onChange={(e) => setGridOpacity(Number(e.target.value))}
        />
        <FieldRow label="Style" value={grid.gridStyle} />
        <div className="view-segment" role="group" aria-label="Grid style">
          {grid.stylePresets.map((style) => (
            <button
              key={style}
              type="button"
              className={"view-segment-btn" + (style === grid.gridStyle ? " active" : "")}
              aria-pressed={style === grid.gridStyle}
              onClick={() => store.setGridStyle(style)}
            >
              {style}
            </button>
          ))}
        </div>
        <FieldRow label="Color" value={gridColor.toUpperCase()} />
        <SwatchRow
          presets={grid.colorPresets}
          current={gridColor}
          ariaPrefix="Grid color"
          onPick={setGridColor}
        />
        <ToggleRow
          label="Snap to Grid"
          hint="Dragged nodes land on grid lines"
          checked={scene.snapToGrid}
          onChange={(v) => store.setSnapToGrid(v)}
        />
        <ToggleRow
          label="Smart Guides"
          hint="Snap to alignments with nearby nodes"
          checked={scene.smartGuides}
          onChange={(v) => store.setSmartGuides(v)}
        />
      </section>

      {/* Fade/orthogonal lived under GRID in the straight port only because
          the legacy grid-control bridge happened to own their checkboxes;
          they configure connections, so they get a section that says so. */}
      <section className="view-section" aria-label="Connections">
        <p className="view-section-title">Connections</p>
        <ToggleRow
          label="Fade Connections"
          hint="Dim all lines except the one under the pointer"
          checked={scene.fadeConnectionsEnabled}
          onChange={(v) => store.setFadeConnections(v)}
        />
        <ToggleRow
          label="Orthogonal Routing"
          hint="Route eligible lines at right angles"
          checked={scene.orthogonalRouting}
          onChange={(v) => store.setOrthogonalConnections(v)}
        />
      </section>

      <section className="view-section" aria-label="Font">
        <p className="view-section-title">Node Font</p>
        <CustomSelect
          value={scene.fontFamily}
          options={fontConfig.fontFamilies.map((family) => ({ id: family, label: family }))}
          onChange={(family) => store.setFontFamily(family)}
          ariaLabel="Font family"
        />
        <FieldRow label="Size" value={`${fontSizePt}pt`} />
        <input
          type="range"
          className="view-slider"
          min={fontConfig.sizeMin}
          max={fontConfig.sizeMax}
          value={fontSizePt}
          aria-label="Font size"
          onChange={(e) => setFontSizePt(Number(e.target.value))}
        />
        <FieldRow label="Color" value={fontColor.toUpperCase()} />
        <SwatchRow
          presets={fontConfig.colorPresets}
          current={fontColor}
          ariaPrefix="Font color"
          onPick={setFontColor}
        />
        {/* What the settings above actually produce on a node card - the
            readout the three separate controls never had. */}
        <div
          className="view-font-preview"
          aria-hidden="true"
          style={{
            fontFamily: scene.fontFamily,
            fontSize: `${fontSizePt}pt`,
            color: fontColor,
          }}
        >
          The quick brown fox jumps over the lazy dog
        </div>
      </section>

      <section className="view-section" aria-label="Branches">
        <p className="view-section-title">Branches</p>
        {/* ADR-002 Workstream 1 ("Branch status and lifecycle"): dims every
            node outside the accepted paths (rejected/superseded branches
            and their descendants, unless an explicit "accepted" override
            reactivates a sub-branch) - the whole-graph counterpart to a
            single chat node's own "Hide Other Branches" menu action.
            Backed by sceneStore's own local focusAcceptedPaths field
            rather than `scene` - see that field's own comment. */}
        <ToggleRow
          label="Focus Accepted Paths"
          hint="Dim rejected and superseded branches"
          checked={focusAcceptedPaths}
          onChange={(v) => store.setFocusAcceptedPaths(v)}
        />
      </section>

      <section className="view-section" aria-label="Filter">
        <div className="view-section-head">
          <p className="view-section-title">Filter</p>
          {filterCount > 0 && (
            <button type="button" className="view-clear-btn" onClick={() => store.clearFilters()}>
              Clear ({filterCount})
            </button>
          )}
        </div>
        {/* ADR-012 stage 12.5: multi-select toggle chips - "active" means
            "toggled into the filter set," not mutual exclusion; a click
            only ever flips ITS OWN membership in sceneStore's filterKinds/
            filterStatuses Sets. An empty set (no chip active) means no
            filter at all - every node shows at full opacity. */}
        <p className="view-subsection-title">By kind</p>
        <div className="view-row" role="group" aria-label="Filter by node kind">
          {FILTERABLE_NODE_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              className={"view-chip" + (filterKinds.has(kind) ? " active" : "")}
              aria-pressed={filterKinds.has(kind)}
              onClick={() => store.toggleFilterKind(kind)}
            >
              {FILTER_KIND_LABELS[kind]}
            </button>
          ))}
        </div>
        <p className="view-subsection-title">By branch status</p>
        <div className="view-row" role="group" aria-label="Filter by branch status">
          {FILTER_STATUS_VALUES.map((status) => (
            <button
              key={status}
              type="button"
              className={"view-chip" + (filterStatuses.has(status) ? " active" : "")}
              aria-pressed={filterStatuses.has(status)}
              onClick={() => store.toggleFilterStatus(status)}
            >
              {FILTER_STATUS_LABELS[status]}
            </button>
          ))}
        </div>
      </section>

      <div className="view-footer">
        <button type="button" className="view-reset-btn" onClick={resetAll}>
          Reset to Defaults
        </button>
      </div>
    </Popover>
  );
}
