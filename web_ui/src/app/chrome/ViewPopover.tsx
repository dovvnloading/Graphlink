import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { FILTERABLE_NODE_KINDS } from "../canvas/SceneCanvas";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover } from "../overlays/overlays";
import { CustomSelect } from "./CustomSelect";

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
  conversation: "Conversation",
  web_research: "Web Research",
  plan: "Plan",
  artifact: "Artifact",
  gitlink: "Gitlink",
  code_sandbox: "Code Sandbox",
  note: "Note",
  chart: "Chart",
  harness: "Agent",
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

/**
 * A numeric setting: name, live value, one slider, and - where the backend
 * publishes them - its landmark values as scale marks under the track.
 *
 * ONE CONTROL PER VALUE, which is the main thing this popover was missing.
 * Pan speed and grid spacing each used to render a value badge, a slider,
 * AND a four-button segmented control, all bound to the same number and
 * stacked vertically: three representations of one setting, with nothing
 * saying which was authoritative. That is a straight artifact of the port -
 * the Qt controls offered presets, the SPA rewrite added sliders, and
 * neither was taken away.
 *
 * The presets are worth keeping (they are the values the backend actually
 * publishes as landmarks), so they stay - as small marks belonging to the
 * slider's scale rather than as a competing control. Half the height, and
 * no ambiguity about what sets the value.
 *
 * The track's fill is driven by --view-slider-fill rather than by a second
 * element: a custom property set on the input inherits into the
 * ::-webkit-slider-runnable-track pseudo-element, which is the only way to
 * paint progress on a range input in this engine.
 */
function SliderField({
  label,
  ariaLabel,
  value,
  display,
  min,
  max,
  presets,
  formatPreset,
  onInput,
  onPreset,
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
  const span = max - min;
  const fill = span > 0 ? ((value - min) / span) * 100 : 0;
  return (
    <div className="view-field">
      <FieldRow label={label} value={display} />
      <input
        type="range"
        className="view-slider"
        style={{ ["--view-slider-fill" as string]: `${Math.min(100, Math.max(0, fill))}%` }}
        min={min}
        max={max}
        value={value}
        aria-label={ariaLabel}
        onChange={(e) => onInput(Number(e.target.value))}
      />
      {presets && presets.length > 0 && onPreset && (
        <div className="view-ticks" role="group" aria-label={`${ariaLabel} presets`}>
          {presets.map((preset) => (
            <button
              key={preset}
              type="button"
              className={"view-tick" + (preset === value ? " active" : "")}
              aria-pressed={preset === value}
              onClick={() => onPreset(preset)}
            >
              {formatPreset ? formatPreset(preset) : preset}
            </button>
          ))}
        </div>
      )}
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
      <input
        type="checkbox"
        className="gl-checkbox"
        aria-label={label}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="view-toggle-text">
        <span className="view-toggle-label">{label}</span>
        <span className="view-toggle-hint">{hint}</span>
      </span>
    </label>
  );
}

/** Preset color swatches plus a free-choice picker at the end of the row.
 * `autoLabel`, when given, prepends an "follow the theme" swatch that picks
 * the empty string - the reset-to-adaptive affordance the font color needs
 * (an unset font color inherits the palette's own text token and stays
 * readable in both themes). Grid color has no such state and omits it. */
function SwatchRow({
  presets,
  current,
  ariaPrefix,
  autoLabel,
  onPick,
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
        <button
          type="button"
          className={"view-color-swatch view-color-auto" + (current === "" ? " active" : "")}
          title={autoLabel}
          aria-label={`${ariaPrefix}, ${autoLabel}`}
          aria-pressed={current === ""}
          onClick={() => onPick("")}
        >
          A
        </button>
      )}
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
          // A color input cannot hold "" - the browser coerces invalid
          // values (to #000000, with a console warning). When the current
          // value is "follow the theme" the picker just needs a sane
          // starting point for the dialog it opens.
          value={current || "#949494"}
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
        <section className="view-section" aria-label="Canvas">
          <p className="view-section-title">Canvas</p>
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
        </section>

        <section className="view-section" aria-label="Grid">
          <p className="view-section-title">Grid</p>
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
          {/* No value readout above this one: the segmented control IS the
              readout - the selected segment says "Dots" as plainly as a
              badge above it did. */}
          <div className="view-field">
            <span className="view-field-label">Style</span>
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

        <section className="view-section" aria-label="Node font">
          <p className="view-section-title">Node Font</p>
          <div className="view-field">
            <span className="view-field-label">Family</span>
            <CustomSelect
              value={scene.fontFamily}
              options={fontConfig.fontFamilies.map((family) => ({ id: family, label: family }))}
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
        </section>

        <section className="view-section" aria-label="Focus">
          <div className="view-section-head">
            <p className="view-section-title">Focus</p>
            {filterCount > 0 && (
              <button type="button" className="view-clear-btn" onClick={() => store.clearFilters()}>
                Clear ({filterCount})
              </button>
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
      </div>

      {/* Outside .view-scroll on purpose: the panel is taller than the
          popover, and a footer inside the scroller means scrolling past
          every control this button undoes in order to reach it. */}
      <div className="view-footer">
        <button type="button" className="view-reset-btn" onClick={resetAll}>
          Reset to Defaults
        </button>
      </div>
    </Popover>
  );
}
