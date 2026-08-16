import { useReactFlow } from "@xyflow/react";
import { useSyncExternalStore } from "react";
import { exportCanvasAsPng } from "../canvas/exportCanvasPng";
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
 * - Related actions live in `.appbar-group` containers with uniform inner
 *   spacing and a shared surface. Grouping is what carries the visual
 *   organisation; the old bar was one undifferentiated run of twenty text
 *   buttons separated by ad-hoc 1px rules.
 *
 * ICONS vs LABELS. Frequent, app-specific verbs (Library, Save, Organize,
 * View, Plugins) keep text - they are the vocabulary of the product and
 * nothing draws them unambiguously. Universally-recognised mechanics (undo,
 * redo, the four viewport controls) and the utility surfaces on the right
 * become icons with `title` + `aria-label` carrying the exact same wording
 * they had as text, which is what keeps them findable by keyboard, by
 * screen reader, and by every existing test.
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
 * R8a (finding #5): below a narrow width this toolbar's buttons had no
 * shrink/wrap/overflow behavior at all, so its right end ran off the window
 * edge, dragging a horizontal scrollbar across the WHOLE document with it.
 * Fixed with a real overflow menu: `.appbar` declares `container-type:
 * inline-size` (styles.css) so its descendants can query how much room THIS
 * toolbar - not the window - actually has, and whole GROUPS collapse in
 * tiers (least-used first). Collapsing by group rather than by button keeps
 * related actions together at every width instead of leaving fragments of a
 * cluster behind. Every collapsible action is rendered twice - once inline,
 * once inside the overflow menu, tagged with the same data-tier - and pure
 * CSS decides which copy is visible. No ResizeObserver or width measurement
 * anywhere; container queries are declarative and this app targets one
 * engine (WebView2).
 *
 * The overflow menu is the shared `Popover` (overlays.tsx), NOT `NodeMenu`
 * (canvas/NodeMenu.tsx) - tried first, reverted after live testing caught a
 * real bug: NodeMenu portals to document.body, and a portaled element is no
 * longer a DESCENDANT of `.appbar`, so it falls OUTSIDE the `@container
 * appbar` scope entirely and every item in it would have silently stayed
 * hidden forever. `.appbar` therefore does NOT get `overflow: hidden`
 * either; the horizontal-spill backstop is the tier breakpoints being
 * correctly tuned, plus the grid track above that cannot be overrun.
 *
 * Both copies of a collapsible action call the exact same handler - the
 * handler is the single source of truth for BEHAVIOR, only the label markup
 * is duplicated.
 */

/** Text button: an app verb, where the word is the affordance. */
function BarButton({
  label,
  className,
  title,
  disabled,
  trigger,
  pressed,
  onClick,
}: {
  label: string;
  className: string;
  title?: string;
  disabled?: boolean;
  trigger?: string;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={className}
      title={title}
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
 * Icon button. `label` is the accessible name AND the tooltip, so an
 * icon-only control is never nameless - it reads identically to the text
 * button it replaced for anything that is not a pair of eyes.
 */
function BarIconButton({
  icon,
  label,
  className,
  disabled,
  trigger,
  pressed,
  onClick,
}: {
  icon: AppBarIconName;
  label: string;
  className: string;
  disabled?: boolean;
  trigger?: string;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`${className} appbar-btn-icon`}
      title={label}
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
  const exportPng = () =>
    void exportCanvasAsPng(
      { getNodes, getViewport, setViewport },
      "--gl-surface-window",
      (value) => store.setExportInProgress(value),
    );

  // Overlay-opening actions (Pins/View/Plugins/About/Help) close this popover
  // for free via OverlayProvider's own single-open policy - opening any
  // surface replaces whatever else was open, "toolbar-overflow" included.
  // Plain actions (Organize/Zoom/Fit/Export) never touch the overlay
  // registry at all, so their overflow copies close it explicitly.
  const closeOverflow = () => overlays.close();
  const overflowItem = (surface: string) =>
    "appbar-overflow-item" + (overlays.isOpen(surface) ? " checked" : "");

  return (
    <div className="appbar" role="toolbar" aria-label="Application bar">
      {/* SESSION - the document itself. Never collapses: losing the way to
          open or save your work at a narrow width is what finding #5 was
          about in the first place. */}
      <div className="appbar-group">
        <BarButton
          label="Library"
          className={chip("library")}
          trigger="library"
          pressed={overlays.isOpen("library")}
          onClick={() => overlays.toggle("library", "dialog")}
        />
        <BarButton label="Save" className="appbar-btn" onClick={() => store.saveChat()} />
      </div>

      {/* HISTORY - keyboard equivalents exist (Ctrl+Z / Ctrl+Shift+Z), so
          this is the first group to fold away. */}
      <div className="appbar-group appbar-tier" data-tier="1">
        <BarIconButton
          icon="undo"
          label="Undo"
          className="appbar-btn"
          disabled={!scene.canUndo}
          onClick={() => store.undo()}
        />
        <BarIconButton
          icon="redo"
          label="Redo"
          className="appbar-btn"
          disabled={!scene.canRedo}
          onClick={() => store.redo()}
        />
      </div>

      {/* VIEWPORT - wheel and trackpad cover zooming, so these fold early
          too. */}
      <div className="appbar-group appbar-tier" data-tier="1">
        <BarIconButton
          icon="zoom-out"
          label="Zoom Out"
          className="appbar-btn"
          onClick={() => zoomOut({ duration: motionDuration(150) })}
        />
        <BarIconButton
          icon="zoom-in"
          label="Zoom In"
          className="appbar-btn"
          onClick={() => zoomIn({ duration: motionDuration(150) })}
        />
        <BarIconButton icon="zoom-reset" label="Reset" className="appbar-btn" onClick={resetZoom} />
        <BarIconButton
          icon="fit"
          label="Fit All"
          className="appbar-btn"
          onClick={() => fitView({ duration: motionDuration(200) })}
        />
      </div>

      {/* ARRANGE - acts on the graph's layout and landmarks. */}
      <div className="appbar-group appbar-tier" data-tier="2">
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
      <div className="appbar-group appbar-tier" data-tier="3">
        <BarButton
          label="View"
          className={chip("view")}
          trigger="view"
          pressed={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}
        />
        <button
          type="button"
          className={chip("plugins")}
          data-overlay-trigger="plugins"
          aria-pressed={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}
        >
          Plugins <span className="appbar-chevron">&#9662;</span>
        </button>
      </div>

      <span className="appbar-spacer" />

      {/* Tier-gated the same way as every collapsible group above: CSS only
          shows this once at least one tier is hidden, so it never appears
          as a menu button opening an empty menu at full width. */}
      <button
        type="button"
        className={"appbar-btn appbar-btn-icon appbar-overflow-trigger" + (overlays.isOpen("toolbar-overflow") ? " checked" : "")}
        data-overlay-trigger="toolbar-overflow"
        aria-label="More toolbar actions"
        aria-haspopup="dialog"
        aria-expanded={overlays.isOpen("toolbar-overflow")}
        onClick={() => overlays.toggle("toolbar-overflow", "popover")}
      >
        <AppBarIcon name="more" />
      </button>
      <Popover name="toolbar-overflow" label="More toolbar actions" className="appbar-overflow-menu">
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            store.undo();
            closeOverflow();
          }}
          disabled={!scene.canUndo}
        >
          Undo
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            store.redo();
            closeOverflow();
          }}
          disabled={!scene.canRedo}
        >
          Redo
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            zoomIn({ duration: motionDuration(150) });
            closeOverflow();
          }}
        >
          Zoom In
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            zoomOut({ duration: motionDuration(150) });
            closeOverflow();
          }}
        >
          Zoom Out
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            resetZoom();
            closeOverflow();
          }}
        >
          Reset
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            fitView({ duration: motionDuration(200) });
            closeOverflow();
          }}
        >
          Fit All
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="2"
          onClick={() => {
            store.organizeNodes();
            closeOverflow();
          }}
        >
          Organize
        </button>
        <button
          type="button"
          className={overflowItem("pins")}
          data-tier="2"
          aria-pressed={overlays.isOpen("pins")}
          onClick={() => overlays.toggle("pins", "popover")}
        >
          Pins
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="2"
          onClick={() => {
            exportPng();
            closeOverflow();
          }}
        >
          Export PNG
        </button>
        <button
          type="button"
          className={overflowItem("view")}
          data-tier="3"
          aria-pressed={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}
        >
          View
        </button>
        <button
          type="button"
          className={overflowItem("plugins")}
          data-tier="3"
          aria-pressed={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}
        >
          Plugins
        </button>
        <button
          type="button"
          className={overflowItem("global-search")}
          data-tier="4"
          aria-pressed={overlays.isOpen("global-search")}
          onClick={() => overlays.toggle("global-search", "dialog")}
        >
          Global Search
        </button>
        <button
          type="button"
          className={overflowItem("knowledge")}
          data-tier="4"
          aria-pressed={overlays.isOpen("knowledge")}
          onClick={() => overlays.toggle("knowledge", "dialog")}
        >
          Knowledge
        </button>
        <button
          type="button"
          className={overflowItem("builder-launch")}
          data-tier="4"
          aria-pressed={overlays.isOpen("builder-launch")}
          onClick={() => overlays.toggle("builder-launch", "dialog")}
        >
          Builder
        </button>
        <button
          type="button"
          className={overflowItem("diagnostics")}
          data-tier="4"
          aria-pressed={overlays.isOpen("diagnostics")}
          onClick={() => overlays.toggle("diagnostics", "dialog")}
        >
          Diagnostics
        </button>
        <button
          type="button"
          className={overflowItem("help")}
          data-tier="4"
          aria-pressed={overlays.isOpen("help")}
          onClick={() => overlays.toggle("help", "dialog")}
        >
          Help
        </button>
        <button
          type="button"
          className={overflowItem("about")}
          data-tier="4"
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
      <div className="appbar-group appbar-tier" data-tier="4">
        <BarIconButton
          icon="search"
          label="Global Search"
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
          icon="diagnostics"
          label="Diagnostics"
          className={chip("diagnostics")}
          trigger="diagnostics"
          pressed={overlays.isOpen("diagnostics")}
          onClick={() => overlays.toggle("diagnostics", "dialog")}
        />
        <BarIconButton
          icon="help"
          label="Help"
          className={chip("help")}
          trigger="help"
          pressed={overlays.isOpen("help")}
          onClick={() => overlays.toggle("help", "dialog")}
        />
        <BarIconButton
          icon="about"
          label="About"
          className={chip("about")}
          trigger="about"
          pressed={overlays.isOpen("about")}
          onClick={() => overlays.toggle("about", "dialog")}
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
    </div>
  );
}
