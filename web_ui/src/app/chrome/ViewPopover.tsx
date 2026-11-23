import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { FILTERABLE_NODE_KINDS } from "../canvas/SceneCanvas";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover } from "../overlays/overlays";
import { Slider } from "../ui/Slider";
import { Switch } from "../ui/Switch";
import { SegmentedControl } from "../ui/SegmentedControl";
import { ToggleButton } from "../ui/ToggleButton";
import { FieldGroup } from "../ui/Field";
import { Button } from "../ui/Button";
import { Select } from "../ui/Select";
import "./ViewPopover.css";

// ADR-012 stage 12.5: display labels for FILTERABLE_NODE_KINDS' own raw
// kind strings - most already read fine title-cased, but "web_research"/
// "code_sandbox" need a human label. Kept local to this file (not
// exported from SceneCanvas.tsx) since nothing else needs a display label
// for a node kind today.
const FILTER_KIND_LABELS: Record<string, string> = {
  chat: "Chat",
  code: "Code",
  document: "Document",
  thinking: "Thinking",
  html: "HTML",
  image: "Image",
  fal_media: "Fal Media Studio",
  conversation: "Conversation",
  web_research: "Web Research",
  plan: "Plan",
  artifact: "Artifact",
  gitlink: "Gitlink",
  code_review: "Code Review",
  code_sandbox: "Code Sandbox",
  note: "Note",
  chart: "Chart",
  harness: "Agent",
  scientific: "Scientific workbench",
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
  // "" = follow the theme - the backend default (backend/domain/graph.py).
  // Reset returns to adaptive text, not to a dark-theme white.
  fontColor: "",
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

/** A field header: what the setting is, and what it currently reads. */
function FieldRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="view-field-row">
      <span className="view-field-label">{label}</span>
      <span className="view-field-value">{value}</span>
    </div>
  );
}

/** Slider values are buffered to avoid a backend publish for every pixel dragged. */
function SliderField({
  label, ariaLabel, value, display, min, max, presets, formatPreset, onInput, onPreset,
}: {
  label: string;
  ariaLabel: string;
  value: number;
  display: string;
  min: number;
  max: number;
  presets?: readonly number[];
  formatPreset?: (value: number) => string;
  onInput: (value: number) => void;
  onPreset?: (value: number) => void;
}) {
  return (
    <div className="view-field">
      <FieldRow label={label} value={display} />
      <Slider label={ariaLabel} min={min} max={max} value={value} onValueChange={onInput}
        ticks={presets?.map((tick) => ({ value: tick, label: formatPreset ? formatPreset(tick) : String(tick) }))} />
      {presets && onPreset && (
        <div className="view-presets" role="group" aria-label={`${ariaLabel} presets`}>
          {presets.map((preset) => (
            <ToggleButton key={preset} size="sm" label={formatPreset ? formatPreset(preset) : String(preset)}
              pressed={value === preset} onPressedChange={() => onPreset(preset)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToggleRow({
  label, hint, checked, onChange,
}: {
  label: string; hint: string; checked: boolean; onChange: (next: boolean) => void;
}) {
  return <Switch label={label} description={hint} checked={checked} onCheckedChange={onChange} />;
}

function SwatchRow({
  presets, current, ariaPrefix, autoLabel, onPick,
}: {
  presets: readonly string[];
  current: string;
  ariaPrefix: string;
  autoLabel?: string;
  onPick: (color: string) => void;
}) {
  const currentIsPreset = presets.includes(current);
  return (
    <div className="view-row" role="group" aria-label={`${ariaPrefix} presets`}>
      {autoLabel && (
        <ToggleButton size="sm" className="view-color-swatch view-color-auto" label="Auto"
          aria-label={`${ariaPrefix}, ${autoLabel}`} pressed={current === ""}
          icon={current === "" ? "check" : undefined} onPressedChange={() => onPick("")} />
      )}
      {presets.map((color) => (
        <ToggleButton key={color} size="sm" className="view-color-swatch"
          aria-label={`${ariaPrefix} ${color}`} pressed={color === current}
          icon={color === current ? "check" : undefined}
          style={{ backgroundColor: color }} onPressedChange={() => onPick(color)} />
      ))}
      <label className={"view-color-custom" + (!currentIsPreset && current ? " active" : "")}
        style={!currentIsPreset && current ? { backgroundColor: current } : undefined}>
        <input type="color" value={current || "#949494"}
          aria-label={`${ariaPrefix}, custom`} onChange={(event) => onPick(event.currentTarget.value)} />
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
 * WHAT THIS PASS FIXED, and why it was there. The first version of this
 * panel was a consolidation of three Qt islands, and it consolidated them
 * additively: whatever each island had, plus whatever the SPA rewrite
 * introduced, stacked in the order it was written. The result read as a
 * port rather than as a panel.
 *
 * - DUPLICATE CONTROLS. Pan speed and grid spacing each rendered a value
 *   badge, a slider, and a four-button segmented control - three
 *   representations of one number, stacked. Grid style rendered its value
 *   as a badge directly above a segmented control already showing it. Each
 *   setting has one control now; the published landmark values live on the
 *   slider's own scale (see SliderField).
 * - READOUTS DRESSED AS BUTTONS. Every value badge was a bordered,
 *   inset-filled pill - the exact shape of .view-chip, which IS a button.
 *   They are plain tabular text now, aligned right, and nothing that cannot
 *   be clicked looks like it can.
 * - OS CHECKBOXES. The toggles were the one place in this panel wearing
 *   engine chrome, next to a fully custom slider, segmented control and
 *   swatch row.
 * - A TRACK THAT SHOWED NOTHING. The slider's track was a single flat bar,
 *   so the only cue for a value was thumb position; the track is filled to
 *   the value now, which is what makes a row of sliders readable at a
 *   glance.
 * - SECTIONS BY BRIDGE, NOT BY MEANING. "Snap to Grid" and "Smart Guides"
 *   are drag behaviours and sat under GRID (appearance) because the legacy
 *   grid bridge happened to own their checkboxes - the same accident that
 *   had already put the connection toggles there. They are in CANVAS now,
 *   with the pan speed, which is where the rest of "how moving around
 *   behaves" lives. "Focus Accepted Paths" was a one-toggle BRANCHES
 *   section sitting immediately above FILTER while doing the same job as
 *   it - dimming what you are not looking at - so it opens that section
 *   instead of preceding it.
 * - A FOOTER THAT SCROLLED AWAY. Reset to Defaults was the last thing in a
 *   panel taller than the popover, so getting to it meant scrolling past
 *   every control it undoes. It is pinned to the bottom.
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
      <div className="view-scroll">
        <FieldGroup {...{ title: "Canvas" }} className="view-section">
          <SliderField
            label="Pan speed"
            ariaLabel="Canvas pan speed"
            value={dragPercent}
            display={`${dragPercent}%`}
            min={dragConfig.percentMin}
            max={dragConfig.percentMax}
            presets={dragConfig.percentPresets}
            formatPreset={(p) => `${p}%`}
            onInput={setDragPercent}
            onPreset={(p) => store.setDragFactor(p / 100)}
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
        </FieldGroup>

        <FieldGroup {...{ title: "Grid" }} className="view-section">
          <SliderField
            label="Spacing"
            ariaLabel="Grid spacing"
            value={gridSize}
            display={`${gridSize}px`}
            min={GRID_SIZE_MIN}
            max={GRID_SIZE_MAX}
            presets={grid.sizePresets}
            formatPreset={(s) => `${s}px`}
            onInput={setGridSize}
            onPreset={(s) => store.setGridSize(s)}
          />
          <SliderField
            label="Opacity"
            ariaLabel="Grid opacity"
            value={gridOpacity}
            display={`${gridOpacity}%`}
            min={0}
            max={100}
            onInput={setGridOpacity}
          />
          <div className="view-field">
            <span className="view-field-label">Style</span>
            <SegmentedControl size="sm" label="Grid style" value={grid.gridStyle}
              options={grid.stylePresets.map((style) => ({
                value: style, label: style,
                icon: style === "Dots" ? "grid-dots" : style === "Lines" ? "grid-lines" : "grid-cross",
              }))}
              onChange={(style) => store.setGridStyle(style)} />
          </div>
          <div className="view-field">
            <FieldRow label="Color" value={gridColor.toUpperCase()} />
            <SwatchRow
              presets={grid.colorPresets}
              current={gridColor}
              ariaPrefix="Grid color"
              onPick={setGridColor}
            />
          </div>
        </FieldGroup>

        {/* Fade/orthogonal lived under GRID in the straight port only because
            the legacy grid-control bridge happened to own their checkboxes;
            they configure connections, so they get a section that says so. */}
        <FieldGroup {...{ title: "Connections" }} className="view-section">
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
        </FieldGroup>

        <FieldGroup {...{ title: "Node font" }} className="view-section">
          <div className="view-field">
            <span className="view-field-label">Family</span>
            <Select
              value={scene.fontFamily}
              options={fontConfig.fontFamilies.map((family) => ({ value: family, label: family }))}
              onChange={(family) => store.setFontFamily(family)}
              ariaLabel="Font family"
            />
          </div>
          <SliderField
            label="Size"
            ariaLabel="Font size"
            value={fontSizePt}
            display={`${fontSizePt}pt`}
            min={fontConfig.sizeMin}
            max={fontConfig.sizeMax}
            onInput={setFontSizePt}
          />
          <div className="view-field">
            <FieldRow label="Color" value={fontColor ? fontColor.toUpperCase() : "Auto"} />
            <SwatchRow
              presets={fontConfig.colorPresets}
              current={fontColor}
              ariaPrefix="Font color"
              autoLabel="Follow the theme"
              onPick={setFontColor}
            />
          </div>
          {/* What the settings above actually produce on a node card - the
              readout the three separate controls never had. */}
          <div
            className="view-font-preview"
            aria-hidden="true"
            style={{
              fontFamily: scene.fontFamily,
              fontSize: `${fontSizePt}pt`,
              // Unset = the palette's own text token, exactly what the
              // canvas will render (useCanvasFontVars.ts).
              color: fontColor || "var(--gl-surface-text-primary)",
            }}
          >
            The quick brown fox jumps over the lazy dog
          </div>
        </FieldGroup>

        <FieldGroup {...{ title: "Focus" }} className="view-section">
          <div className="view-section-head">
              {filterCount > 0 && (
              <Button variant="ghost" size="sm" onClick={() => store.clearFilters()}>Clear ({filterCount})</Button>
            )}
          </div>
          {/* ADR-002 Workstream 1 ("Branch status and lifecycle"): dims every
              node outside the accepted paths (rejected/superseded branches
              and their descendants, unless an explicit "accepted" override
              reactivates a sub-branch) - the whole-graph counterpart to a
              single chat node's own "Hide Other Branches" menu action.
              Backed by sceneStore's own local focusAcceptedPaths field
              rather than `scene` - see that field's own comment. It opens
              this section rather than owning a one-toggle section of its
              own directly above it: dimming rejected branches and dimming
              filtered-out kinds are the same job. */}
          <ToggleRow
            label="Focus Accepted Paths"
            hint="Dim rejected and superseded branches"
            checked={focusAcceptedPaths}
            onChange={(v) => store.setFocusAcceptedPaths(v)}
          />
          {/* ADR-012 stage 12.5: multi-select toggle chips - "active" means
              "toggled into the filter set," not mutual exclusion; a click
              only ever flips ITS OWN membership in sceneStore's filterKinds/
              filterStatuses Sets. An empty set (no chip active) means no
              filter at all - every node shows at full opacity. */}
          <p className="view-subsection-title">By kind</p>
          <div className="view-row" role="group" aria-label="Filter by node kind">
            {FILTERABLE_NODE_KINDS.map((kind) => (
              <ToggleButton key={kind} size="sm" label={FILTER_KIND_LABELS[kind]}
                pressed={filterKinds.has(kind)} onPressedChange={() => store.toggleFilterKind(kind)} />
            ))}
          </div>
          <p className="view-subsection-title">By branch status</p>
          <div className="view-row" role="group" aria-label="Filter by branch status">
            {FILTER_STATUS_VALUES.map((status) => (
              <ToggleButton key={status} size="sm" label={FILTER_STATUS_LABELS[status]}
                pressed={filterStatuses.has(status)} onPressedChange={() => store.toggleFilterStatus(status)} />
            ))}
          </div>
        </FieldGroup>
      </div>

      {/* Outside .view-scroll on purpose: the panel is taller than the
          popover, and a footer inside the scroller means scrolling past
          every control this button undoes in order to reach it. */}
      <div className="view-footer">
        <Button variant="ghost" size="sm" fullWidth onClick={resetAll}>Reset to defaults</Button>
      </div>
    </Popover>
  );
}
