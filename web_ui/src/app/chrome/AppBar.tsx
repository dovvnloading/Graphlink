import { useReactFlow, useStore } from "@xyflow/react";
import { useSyncExternalStore } from "react";
import { FIT_VIEW_MAX_ZOOM } from "../canvas/canvasConstants";
import { motionDuration } from "../reducedMotion";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";
import { AppBarIcon, type AppBarIconName } from "./AppBarIcon";

/**
 * The app bar (Qt-removal plan R2) - the toolbar island's SPA successor.
 *
 * LAYOUT CONTRACT. This bar is on screen at all times, so its geometry is
 * fixed rather than emergent:
 *
 * - `.app-topbar` (styles.css) is a THREE-COLUMN GRID - brand, this
 *   toolbar, connection status - so those three regions occupy declared
 *   tracks and cannot encroach on one another. The previous layout was one
 *   flex row in which the status badge sat outside the toolbar and relied
 *   on `margin-left: auto` against a `flex: 1` sibling that had already
 *   eaten the free space, which is why it ended up jammed against the last
 *   button instead of anchored to the window edge.
 * - The row has a FIXED height and every control a fixed height, so the bar
 *   never changes size with its contents.
 * - Related actions live in `.appbar-group` containers. Grouping is what
 *   carries the visual organisation; the bar this replaced was one
 *   undifferentiated run of twenty text buttons separated by ad-hoc rules.
 *
 * GROUPING, and why it is drawn the way it is. Each group used to be its
 * own filled, bordered, padded box. Nine such boxes sat in a 44px strip, so
 * the loudest marks in the bar were its containers rather than its
 * controls, and a two-button cluster like Library/Save read as a segmented
 * control - one widget with two states - rather than as two unrelated
 * commands. Groups are flat now: 2px inside a group, 12px between groups,
 * and a single hairline rule between adjacent groups (styles.css,
 * `.appbar-group + .appbar-group::before`). Proximity plus one hairline is
 * the standard toolbar-grouping treatment - PatternFly's toolbar guidance
 * calls for exactly this once a bar carries enough items to need it - and
 * it spends far less ink than a box per cluster.
 *
 * The separator is a pseudo-element on the FOLLOWING group rather than a
 * real element between two groups, because groups collapse (see the spill
 * guard below) and a standalone separator element would be left behind as a
 * stray rule floating in the gap where its neighbours used to be.
 * Attached to the group, a separator disappears exactly when the thing it
 * separates does. `+` adjacency is DOM-based, not layout-based, so a
 * display:none group does not break the chain for the groups after it.
 *
 * ICONS vs LABELS. Frequent, app-specific verbs (Library, Save, View,
 * Plugins) keep text - they are the vocabulary of the product and nothing
 * draws them unambiguously. Universally-recognised mechanics (undo, redo,
 * the viewport controls) and the utility surfaces on the right are icons
 * with `title` + `aria-label` carrying the exact same wording they had as
 * text, which is what keeps them findable by keyboard, by screen reader,
 * and by every existing test. Where an action has a global keybinding
 * (shortcuts.ts) the tooltip - and ONLY the tooltip - names it; the
 * accessible name stays the bare verb, so a screen reader announces the
 * control rather than reciting its shortcut.
 *
 * ZOOM READOUT. The viewport group's middle control is the live zoom
 * percentage, and clicking it resets to 100%. It replaces an icon button
 * whose glyph (two concentric circles) named neither its action nor the
 * current state - it read as a record button. Every canvas tool this app
 * is measured against shows the zoom level as text for the same reason:
 * the number is the one part of viewport state that cannot be inferred by
 * looking at the canvas. It subscribes to the React Flow transform in its
 * OWN component so a wheel-zoom re-renders one <button> rather than all
 * twenty controls in this bar (ADR-011).
 *
 * HELP MENU. Help, Diagnostics and About are three low-frequency
 * destinations that were three permanent icons in the right-hand cluster,
 * carrying the same visual weight as Search and the Builder. They are one
 * menu now, which is where a rarely-used destination belongs, and which
 * takes the right-hand run of icons from seven down to five. Nothing became
 * unreachable: each is still its own command in the palette (commands.ts,
 * `open-help`/`open-about`) and still its own item in the overflow menu.
 *
 * Intent routing, surface by surface, against the ToolbarBridge @Slot list:
 * - zoomIn/zoomOut/resetZoom/fitAll -> React Flow viewport ops (they were
 *   pure ChatView viewport calls; the viewport lives HERE now)
 * - organizeNodes -> scene intent (backend tidy layout)
 * - togglePins -> the pins overlay (R2.4: full search + rename/note editing)
 * - toggleControls -> the View popover (audit P5: ONE popover for
 *   drag/grid/font instead of three stacked cards)
 * - library/settings/about/help/plugins -> overlay dialogs; chips read REAL
 *   open state from the overlay context (audit B6), never latched clicks
 * - saveChat -> real as of R6.5 (store.saveChat(), targets the
 *   app-chat-library topic - see SceneStore's own comment on why)
 * - Export PNG -> real as of R6.8, a net-new capability (no legacy
 *   canvas-wide export exists) - pure client-side DOM rasterization via
 *   exportCanvasAsPng, same "zero backend round-trip" shape as the
 *   Zoom/Fit All buttons right next to it, not an intent dispatch.
 *
 * R8a (UI/UX issue list finding #8): the provider-mode <select> that used to
 * sit here was permanently `disabled`, held exactly one hardcoded option
 * ("Ollama (Local)"), and its onChange was a literal no-op. Removed outright
 * rather than left as a dead control. ADR-006 stage 6.5 later added a real
 * setProviderMode intent and ADR-012 stage 12.6 wired it up - but NOT back
 * into this toolbar: a cramped toolbar select is exactly what got removed,
 * and Settings' own per-mode pages already give that switch a home
 * co-located with the configuration it affects.
 *
 * SPILL GUARD, not a responsive layout. Graphlink is desktop-only software
 * and this bar is laid out for a desktop window; there is no mobile
 * breakpoint ladder here and none is wanted. What there is is a single
 * floor. R8a (finding #5): dragged narrower than its own contents, this
 * toolbar had no shrink, wrap or overflow behaviour at all, so its right
 * end ran off the window edge and dragged a horizontal scrollbar across the
 * WHOLE document with it. Below one width, therefore, every group that is
 * not Library/Save or Settings moves into an overflow menu in one step -
 * not four graded stages, which would be a phone layout wearing a desk.
 *
 * `.appbar` declares `container-type: inline-size` (styles.css) so the rule
 * measures how much room THIS toolbar has rather than how wide the window
 * is, which is the honest question given the brand and status regions
 * either side of it. Whole GROUPS move, never individual buttons, so a
 * cluster is never left as a fragment of itself. Every collapsible action
 * is rendered twice - once inline, once in the overflow menu - and pure CSS
 * picks the visible copy. No ResizeObserver and no width measurement
 * anywhere.
 *
 * The overflow menu is the shared `Popover` (overlays.tsx) in its
 * NON-anchored form, unlike the Help menu right next to it, and that
 * difference is forced rather than chosen: an anchored Popover portals to
 * document.body, and a portaled element is no longer a DESCENDANT of
 * `.appbar`, so it falls OUTSIDE the `@container appbar` scope entirely and
 * every gated item in it would silently stay hidden forever. The Help menu
 * has no gated contents, so it takes the anchored form and gets the
 * viewport flip/clamp every other anchored popover gets. `.appbar`
 * therefore does NOT get `overflow: hidden` either; the horizontal-spill
 * backstop is that one breakpoint, plus the grid track above it that cannot
 * be overrun.
 *
 * Both copies of a collapsible action call the exact same handler - the
 * handler is the single source of truth for BEHAVIOR, only the label markup
 * is duplicated.
 */

/** Text button: an app verb, where the word is the affordance. */
function BarButton({
  label,
  className,
  hint,
  disabled,
  trigger,
  pressed,
  onClick,
}: {
  label: string;
  className: string;
  /** Keybinding, shown in the tooltip only - never in the accessible name. */
  hint?: string;
  disabled?: boolean;
  trigger?: string;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={className}
      title={hint ? `${label} (${hint})` : undefined}
      disabled={disabled}
      data-overlay-trigger={trigger}
      aria-pressed={pressed}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

/**
 * Icon button. `label` is the accessible name AND the base tooltip, so an
 * icon-only control is never nameless - it reads identically to the text
 * button it replaced for anything that is not a pair of eyes.
 */
function BarIconButton({
  icon,
  label,
  className,
  hint,
  disabled,
  trigger,
  pressed,
  onClick,
}: {
  icon: AppBarIconName;
  label: string;
  className: string;
  hint?: string;
  disabled?: boolean;
  trigger?: string;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`${className} appbar-btn-icon`}
      title={hint ? `${label} (${hint})` : label}
      aria-label={label}
      disabled={disabled}
      data-overlay-trigger={trigger}
      aria-pressed={pressed}
      onClick={onClick}
    >
      <AppBarIcon name={icon} />
    </button>
  );
}

/**
 * A control that opens a menu or panel rather than performing an action,
 * and says so with a chevron. Distinct from BarButton/BarIconButton by
 * `aria-haspopup="dialog"` + `aria-expanded` - Popover renders
 * role="dialog", not role="menu", so those are the honest values.
 */
function BarMenuButton({
  label,
  icon,
  surface,
  isOpen,
  labelled,
  onClick,
}: {
  label: string;
  icon?: AppBarIconName;
  surface: string;
  isOpen: boolean;
  /** false => icon-only, so `label` becomes the accessible name instead. */
  labelled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={
        "appbar-btn appbar-btn-checkable appbar-btn-menu" +
        (labelled ? "" : " appbar-btn-icon") +
        (isOpen ? " checked" : "")
      }
      title={label}
      aria-label={labelled ? undefined : label}
      data-overlay-trigger={surface}
      aria-haspopup="dialog"
      aria-expanded={isOpen}
      onClick={onClick}
    >
      {icon && <AppBarIcon name={icon} />}
      {labelled ? label : null}
      <AppBarIcon name="chevron" />
    </button>
  );
}

/**
 * The live zoom readout and the reset-to-100% control, in one.
 *
 * Its own component purely so the transform subscription is scoped: a
 * wheel-zoom pushes a new transform every animation frame, and reading it
 * in AppBar itself would re-render the whole bar at that rate for a
 * three-character label - ADR-011's entire subject. Tabular figures and a
 * fixed min-width (styles.css) keep the controls either side of it from
 * shifting as the number changes width.
 */
function ZoomLevelButton({ onReset }: { onReset: () => void }) {
  const zoom = useStore((s) => s.transform[2]);
  return (
    <button
      type="button"
      className="appbar-btn appbar-btn-zoom"
      title="Reset zoom to 100%"
      aria-label="Reset zoom to 100%"
      onClick={onReset}
    >
      {Math.round(zoom * 100)}%
    </button>
  );
}

export function AppBar({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  // ADR-010 stage 10.2: the backend owns the undo stack, so enablement AND
  // the action name both come off the scene payload - the frontend never
  // guesses what the next undo would do.
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const { zoomIn, zoomOut, setViewport, fitView, getViewport, getNodes } = useReactFlow();

  const chip = (surface: string) =>
    "appbar-btn appbar-btn-checkable" + (overlays.isOpen(surface) ? " checked" : "");

  const resetZoom = () => {
    const viewport = getViewport();
    setViewport({ ...viewport, zoom: 1 }, { duration: motionDuration(200) });
  };
  // Imported on click, not at module scope: exportCanvasAsPng pulls in
  // html-to-image (13 KB of the initial chunk) to rasterize the canvas, and
  // a session that never exports a PNG never needs it. The await lands
  // inside the handler, so the "export in progress" flag the callback below
  // sets still brackets the real work exactly as before.
  const exportPng = async () => {
    const { exportCanvasAsPng } = await import("../canvas/exportCanvasPng");
    await exportCanvasAsPng(
      { getNodes, getViewport, setViewport },
      "--gl-surface-window",
      (value) => store.setExportInProgress(value),
    );
  };

  // Overlay-opening actions (Pins/View/Plugins/About/Help) close the open
  // menu for free via OverlayProvider's own single-open policy - opening any
  // surface replaces whatever else was open, "toolbar-overflow" included.
  // Plain actions (Organize/Zoom/Fit/Export) never touch the overlay
  // registry at all, so their menu copies close it explicitly.
  const closeMenu = () => overlays.close();
  const menuItem = (surface: string) =>
    "appbar-menu-item" + (overlays.isOpen(surface) ? " checked" : "");
  const overflowItem = (surface: string) => `${menuItem(surface)} appbar-overflow-item`;

  return (
    <div className="appbar" role="toolbar" aria-label="Application bar">
      {/* SESSION - the document itself. Never collapses: losing the way to
          open or save your work at a narrow width is what finding #5 was
          about in the first place. */}
      <div className="appbar-group">
        <BarButton
          label="Library"
          hint="Ctrl+L"
          className={chip("library")}
          trigger="library"
          pressed={overlays.isOpen("library")}
          onClick={() => overlays.toggle("library", "dialog")}
        />
        <BarButton
          label="Save"
          hint="Ctrl+S"
          className="appbar-btn"
          onClick={() => store.saveChat()}
        />
      </div>

      {/* HISTORY - keyboard equivalents exist (Ctrl+Z / Ctrl+Shift+Z), so
          this is the first group to fold away. */}
      <div className="appbar-group appbar-tier">
        <BarIconButton
          icon="undo"
          label="Undo"
          hint="Ctrl+Z"
          className="appbar-btn"
          disabled={!scene.canUndo}
          onClick={() => store.undo()}
        />
        <BarIconButton
          icon="redo"
          label="Redo"
          hint="Ctrl+Shift+Z"
          className="appbar-btn"
          disabled={!scene.canRedo}
          onClick={() => store.redo()}
        />
      </div>

      {/* VIEWPORT - wheel and trackpad cover zooming, so these fold early
          too. Out / readout / in reads left to right as one continuous
          scale, with Fit All after it as the "show me everything" escape. */}
      <div className="appbar-group appbar-tier">
        <BarIconButton
          icon="zoom-out"
          label="Zoom Out"
          className="appbar-btn"
          onClick={() => zoomOut({ duration: motionDuration(150) })}
        />
        <ZoomLevelButton onReset={resetZoom} />
        <BarIconButton
          icon="zoom-in"
          label="Zoom In"
          className="appbar-btn"
          onClick={() => zoomIn({ duration: motionDuration(150) })}
        />
        <BarIconButton
          icon="fit"
          label="Fit All"
          className="appbar-btn"
          onClick={() => fitView({ duration: motionDuration(200), maxZoom: FIT_VIEW_MAX_ZOOM })}
        />
      </div>

      {/* ARRANGE - acts on the graph's layout and landmarks. */}
      <div className="appbar-group appbar-tier">
        <BarIconButton
          icon="organize"
          label="Organize"
          className="appbar-btn"
          onClick={() => store.organizeNodes()}
        />
        <BarIconButton
          icon="pin"
          label="Pins"
          className={chip("pins")}
          trigger="pins"
          pressed={overlays.isOpen("pins")}
          onClick={() => overlays.toggle("pins", "popover")}
        />
        <BarIconButton
          icon="export"
          label="Export PNG"
          className="appbar-btn"
          onClick={exportPng}
        />
      </div>

      {/* PANELS - canvas appearance and the plugin launcher. Text, because
          both open a surface whose contents the word names. */}
      <div className="appbar-group appbar-tier">
        <BarMenuButton
          label="View"
          surface="view"
          labelled
          isOpen={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}
        />
        <BarMenuButton
          label="Plugins"
          surface="plugins"
          labelled
          isOpen={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}
        />
      </div>

      <span className="appbar-spacer" />

      {/* Gated by the same one breakpoint as every collapsible group above,
          so it never appears as a menu button opening an empty menu at a
          normal desktop width. */}
      <button
        type="button"
        className={
          "appbar-btn appbar-btn-icon appbar-overflow-trigger" +
          (overlays.isOpen("toolbar-overflow") ? " checked" : "")
        }
        data-overlay-trigger="toolbar-overflow"
        aria-label="More toolbar actions"
        aria-haspopup="dialog"
        aria-expanded={overlays.isOpen("toolbar-overflow")}
        onClick={() => overlays.toggle("toolbar-overflow", "popover")}
      >
        <AppBarIcon name="more" />
      </button>
      {/* Headings name the toolbar cluster each run of items came from. A
          seventeen-item flat list is a list of everything, not a menu, and
          this menu only ever opens as a whole - so the sections are the
          only structure standing between the reader and the bar they just
          lost. */}
      <Popover
        name="toolbar-overflow"
        label="More toolbar actions"
        className="appbar-menu appbar-overflow-menu"
      >
        <p className="appbar-menu-heading">
          Edit and view
        </p>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            store.undo();
            closeMenu();
          }}
          disabled={!scene.canUndo}
        >
          Undo
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            store.redo();
            closeMenu();
          }}
          disabled={!scene.canRedo}
        >
          Redo
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            zoomIn({ duration: motionDuration(150) });
            closeMenu();
          }}
        >
          Zoom In
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            zoomOut({ duration: motionDuration(150) });
            closeMenu();
          }}
        >
          Zoom Out
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            resetZoom();
            closeMenu();
          }}
        >
          Reset
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            fitView({ duration: motionDuration(200), maxZoom: FIT_VIEW_MAX_ZOOM });
            closeMenu();
          }}
        >
          Fit All
        </button>

        <p className="appbar-menu-heading">
          Arrange
        </p>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            store.organizeNodes();
            closeMenu();
          }}
        >
          Organize
        </button>
        <button
          type="button"
          className={overflowItem("pins")}
          aria-pressed={overlays.isOpen("pins")}
          onClick={() => overlays.toggle("pins", "popover")}
        >
          Pins
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            exportPng();
            closeMenu();
          }}
        >
          Export PNG
        </button>

        <p className="appbar-menu-heading">
          Panels
        </p>
        <button
          type="button"
          className={overflowItem("view")}
          aria-pressed={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}
        >
          View
        </button>
        <button
          type="button"
          className={overflowItem("plugins")}
          aria-pressed={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}
        >
          Plugins
        </button>

        <p className="appbar-menu-heading">
          Workspace
        </p>
        <button
          type="button"
          className={overflowItem("global-search")}
          aria-pressed={overlays.isOpen("global-search")}
          onClick={() => overlays.toggle("global-search", "dialog")}
        >
          Global Search
        </button>
        <button
          type="button"
          className={overflowItem("knowledge")}
          aria-pressed={overlays.isOpen("knowledge")}
          onClick={() => overlays.toggle("knowledge", "dialog")}
        >
          Knowledge
        </button>
        <button
          type="button"
          className={overflowItem("builder-launch")}
          aria-pressed={overlays.isOpen("builder-launch")}
          onClick={() => overlays.toggle("builder-launch", "dialog")}
        >
          Builder
        </button>
        <button
          type="button"
          className={overflowItem("harness-launch")}
          aria-pressed={overlays.isOpen("harness-launch")}
          onClick={() => overlays.toggle("harness-launch", "dialog")}
        >
          Agent
        </button>
        <button
          type="button"
          className={overflowItem("diagnostics")}
          aria-pressed={overlays.isOpen("diagnostics")}
          onClick={() => overlays.toggle("diagnostics", "dialog")}
        >
          Diagnostics
        </button>
        <button
          type="button"
          className={overflowItem("help")}
          aria-pressed={overlays.isOpen("help")}
          onClick={() => overlays.toggle("help", "dialog")}
        >
          Help
        </button>
        <button
          type="button"
          className={overflowItem("about")}
          aria-pressed={overlays.isOpen("about")}
          onClick={() => overlays.toggle("about", "dialog")}
        >
          About
        </button>
      </Popover>

      {/* WORKSPACE TOOLS - cross-cutting surfaces rather than canvas verbs.
          Icon-only: the right end of a permanently-visible bar is where
          density pays, and each carries its full name as tooltip and
          accessible name. */}
      <div className="appbar-group appbar-tier">
        <BarIconButton
          icon="search"
          label="Global Search"
          hint="Ctrl+F"
          className={chip("global-search")}
          trigger="global-search"
          pressed={overlays.isOpen("global-search")}
          onClick={() => overlays.toggle("global-search", "dialog")}
        />
        <BarIconButton
          icon="knowledge"
          label="Knowledge"
          className={chip("knowledge")}
          trigger="knowledge"
          pressed={overlays.isOpen("knowledge")}
          onClick={() => overlays.toggle("knowledge", "dialog")}
        />
        <BarIconButton
          icon="builder"
          label="Builder"
          className={chip("builder-launch")}
          trigger="builder-launch"
          pressed={overlays.isOpen("builder-launch")}
          onClick={() => overlays.toggle("builder-launch", "dialog")}
        />
        <BarIconButton
          icon="agent"
          label="Agent"
          className={chip("harness-launch")}
          trigger="harness-launch"
          pressed={overlays.isOpen("harness-launch")}
          onClick={() => overlays.toggle("harness-launch", "dialog")}
        />
        <BarMenuButton
          label="Help and diagnostics"
          icon="help"
          surface="help-menu"
          labelled={false}
          isOpen={overlays.isOpen("help-menu")}
          onClick={() => overlays.toggle("help-menu", "popover")}
        />
      </div>

      {/* SETTINGS stands alone at the end - the one destination that
          configures the app rather than acting on the graph, and like
          Library/Save it never collapses. */}
      <div className="appbar-group">
        <BarIconButton
          icon="settings"
          label="Settings"
          className={chip("settings")}
          trigger="settings"
          pressed={overlays.isOpen("settings")}
          onClick={() => overlays.toggle("settings", "dialog")}
        />
      </div>

      {/* Anchored (portaled, then placed from the trigger's own rect),
          unlike the overflow menu above - nothing in here is width-gated, so
          it has no reason to stay inside the @container scope, and
          portaling buys it the viewport flip/clamp every other anchored
          popover in the app already gets. */}
      <Popover name="help-menu" label="Help and diagnostics" anchored className="appbar-menu">
        <button
          type="button"
          className={menuItem("help")}
          aria-pressed={overlays.isOpen("help")}
          onClick={() => overlays.toggle("help", "dialog")}
        >
          Help
        </button>
        <button
          type="button"
          className={menuItem("diagnostics")}
          aria-pressed={overlays.isOpen("diagnostics")}
          onClick={() => overlays.toggle("diagnostics", "dialog")}
        >
          Diagnostics
        </button>
        <button
          type="button"
          className={menuItem("about")}
          aria-pressed={overlays.isOpen("about")}
          onClick={() => overlays.toggle("about", "dialog")}
        >
          About
        </button>
      </Popover>
    </div>
  );
}
