import { useReactFlow } from "@xyflow/react";
import { useSyncExternalStore } from "react";
import { exportCanvasAsPng } from "../canvas/exportCanvasPng";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";

/**
 * The app bar (Qt-removal plan R2) - the toolbar island's SPA successor.
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
 * ("Ollama (Local)"), and its onChange was a literal no-op - there has never
 * been a setProviderMode intent anywhere in backend/ for it to call. Removed
 * outright rather than left as a dead control; Settings' own provider pages
 * are the real, complete switcher.
 *
 * R8a (finding #5): below ~1120px window width this toolbar's 12 buttons
 * (13 with the now-removed select) had no shrink/wrap/overflow behavior at
 * all, so the low end of it - Settings, About, Help, the connection status
 * next to this component - ran off the right edge of the window, dragging a
 * horizontal scrollbar across the WHOLE document with it (the canvas and
 * composer went with it, off-screen).
 *
 * Fixed with a real overflow menu, not the audit's own "minimum stopgap"
 * (bare overflow-x: auto). `.appbar` gets `min-width: 0` (a flex item
 * otherwise floors at its content's min-content width - that is what forced
 * the overflow in the first place) and `container-type: inline-size`
 * (styles.css), so its own descendants can query how much room THIS
 * toolbar - not the window - actually has. Every collapsible button is
 * rendered TWICE: once inline, once as a duplicate inside the overflow
 * menu, tagged with the same data-tier either way. Pure CSS @container
 * rules (styles.css) decide which copy is visible at the current width, in
 * three tiers (least-used collapses first) - no ResizeObserver/width-
 * measurement JS anywhere; container queries are declarative, and this app
 * targets one Chromium engine (WebView2), so there is no compatibility
 * reason to reach for JS instead. Library, Save and Settings never
 * collapse - those three are exactly what the finding flagged as becoming
 * unreachable.
 *
 * The overflow menu is the shared `Popover` (overlays.tsx), NOT `NodeMenu`
 * (canvas/NodeMenu.tsx) - tried first, reverted after live testing caught a
 * real bug: NodeMenu portals to document.body, and a portaled element is no
 * longer a DESCENDANT of `.appbar` in the DOM, so it falls OUTSIDE the
 * `@container appbar` scope entirely - every item in it would have silently
 * stayed hidden forever, regardless of width. `.appbar` therefore does NOT
 * get `overflow: hidden` either (the version that used NodeMenu needed it,
 * to stop tier-hidden buttons spilling past the toolbar mid-resize, and
 * could afford it because the portaled menu didn't live inside that box to
 * begin with); the horizontal-spill backstop instead lives one level up, on
 * `.app-topbar` (`overflow-x: hidden`, with `overflow-y: visible` so it
 * does not clip this dropdown, which extends below the header row by
 * design). `Popover`'s own light-dismiss (outside pointerdown) and the
 * OverlayProvider's single-open policy (opening Settings while this is open
 * correctly closes it, same as every other surface) both apply for free.
 *
 * Both copies of a collapsible button call the exact same handler - the
 * handler is the single source of truth for BEHAVIOR, only the two bits of
 * JSX markup (label text) are duplicated, which is what stays in sync via
 * ordinary code review rather than an abstraction neither codebase
 * precedent nor this component's small, fixed button set actually needs.
 */

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
    setViewport({ ...viewport, zoom: 1 }, { duration: 200 });
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

  return (
    <div className="appbar" role="toolbar" aria-label="Application bar">
      <button
        type="button"
        className={chip("library")}
        data-overlay-trigger="library"
        aria-pressed={overlays.isOpen("library")}
        onClick={() => overlays.toggle("library", "dialog")}
      >
        Library
      </button>
      <button type="button" className="appbar-btn" onClick={() => store.saveChat()}>
        Save
      </button>

      <span className="appbar-separator appbar-tier" data-tier="2" />
      <button
        type="button"
        className={chip("pins") + " appbar-tier"}
        data-tier="2"
        data-overlay-trigger="pins"
        aria-pressed={overlays.isOpen("pins")}
        title="Navigation pins"
        onClick={() => overlays.toggle("pins", "popover")}
      >
        Pins
      </button>
      <button type="button" className="appbar-btn appbar-tier" data-tier="2" onClick={() => store.organizeNodes()}>
        Organize
      </button>

      <span className="appbar-separator appbar-tier" data-tier="1" />
      <button
        type="button"
        className="appbar-btn appbar-tier"
        data-tier="1"
        disabled={!scene.canUndo}
        title={scene.canUndo ? `Undo ${scene.undoLabel} (Ctrl+Z)` : "Nothing to undo"}
        onClick={() => store.undo()}
      >
        Undo
      </button>
      <button
        type="button"
        className="appbar-btn appbar-tier"
        data-tier="1"
        disabled={!scene.canRedo}
        title={scene.canRedo ? `Redo ${scene.redoLabel} (Ctrl+Shift+Z)` : "Nothing to redo"}
        onClick={() => store.redo()}
      >
        Redo
      </button>

      <span className="appbar-separator appbar-tier" data-tier="3" />
      <button
        type="button"
        className="appbar-btn appbar-tier"
        data-tier="3"
        onClick={() => zoomIn({ duration: 150 })}
      >
        Zoom In
      </button>
      <button
        type="button"
        className="appbar-btn appbar-tier"
        data-tier="3"
        onClick={() => zoomOut({ duration: 150 })}
      >
        Zoom Out
      </button>
      <button type="button" className="appbar-btn appbar-tier" data-tier="3" onClick={resetZoom}>
        Reset
      </button>
      <button
        type="button"
        className="appbar-btn appbar-tier"
        data-tier="3"
        onClick={() => fitView({ duration: 200 })}
      >
        Fit All
      </button>

      <span className="appbar-separator appbar-tier" data-tier="1" />
      <button
        type="button"
        className="appbar-btn appbar-tier"
        data-tier="1"
        title="Export the whole canvas as a PNG image"
        onClick={exportPng}
      >
        Export PNG
      </button>

      <span className="appbar-separator appbar-tier" data-tier="2" />
      <button
        type="button"
        className={chip("view") + " appbar-tier"}
        data-tier="2"
        data-overlay-trigger="view"
        aria-pressed={overlays.isOpen("view")}
        onClick={() => overlays.toggle("view", "popover")}
      >
        View
      </button>
      <button
        type="button"
        className={chip("plugins") + " appbar-tier"}
        data-tier="2"
        data-overlay-trigger="plugins"
        aria-pressed={overlays.isOpen("plugins")}
        onClick={() => overlays.toggle("plugins", "popover")}
      >
        Plugins <span className="appbar-chevron">&#9662;</span>
      </button>

      <span className="appbar-spacer" />

      {/* Tier-gated the same way as every collapsible button above: CSS
          only shows this once at least one tier is hidden, so it never
          appears as a "..." button opening an empty menu at full width. */}
      <button
        type="button"
        className={"appbar-btn appbar-overflow-trigger" + (overlays.isOpen("toolbar-overflow") ? " checked" : "")}
        data-overlay-trigger="toolbar-overflow"
        aria-label="More toolbar actions"
        aria-haspopup="dialog"
        aria-expanded={overlays.isOpen("toolbar-overflow")}
        onClick={() => overlays.toggle("toolbar-overflow", "popover")}
      >
        <span aria-hidden="true">&#8942;</span>
      </button>
      <Popover name="toolbar-overflow" label="More toolbar actions" className="appbar-overflow-menu">
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="1"
          onClick={() => {
            exportPng();
            closeOverflow();
          }}
        >
          Export PNG
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("pins") ? " checked" : "")}
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
            store.organizeNodes();
            closeOverflow();
          }}
        >
          Organize
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("view") ? " checked" : "")}
          data-tier="2"
          aria-pressed={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}
        >
          View
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("plugins") ? " checked" : "")}
          data-tier="2"
          aria-pressed={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}
        >
          Plugins
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="3"
          onClick={() => {
            zoomIn({ duration: 150 });
            closeOverflow();
          }}
        >
          Zoom In
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="3"
          onClick={() => {
            zoomOut({ duration: 150 });
            closeOverflow();
          }}
        >
          Zoom Out
        </button>
        <button
          type="button"
          className="appbar-overflow-item"
          data-tier="3"
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
          data-tier="3"
          onClick={() => {
            fitView({ duration: 200 });
            closeOverflow();
          }}
        >
          Fit All
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("about") ? " checked" : "")}
          data-tier="1"
          aria-pressed={overlays.isOpen("about")}
          onClick={() => overlays.toggle("about", "dialog")}
        >
          About
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("help") ? " checked" : "")}
          data-tier="1"
          aria-pressed={overlays.isOpen("help")}
          onClick={() => overlays.toggle("help", "dialog")}
        >
          Help
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("diagnostics") ? " checked" : "")}
          data-tier="1"
          aria-pressed={overlays.isOpen("diagnostics")}
          onClick={() => overlays.toggle("diagnostics", "dialog")}
        >
          Diagnostics
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("knowledge") ? " checked" : "")}
          data-tier="1"
          aria-pressed={overlays.isOpen("knowledge")}
          onClick={() => overlays.toggle("knowledge", "dialog")}
        >
          Knowledge
        </button>
        <button
          type="button"
          className={"appbar-overflow-item" + (overlays.isOpen("builder-launch") ? " checked" : "")}
          data-tier="1"
          aria-pressed={overlays.isOpen("builder-launch")}
          onClick={() => overlays.toggle("builder-launch", "dialog")}
        >
          Builder
        </button>
      </Popover>

      <button
        type="button"
        className={chip("settings")}
        data-overlay-trigger="settings"
        aria-pressed={overlays.isOpen("settings")}
        onClick={() => overlays.toggle("settings", "dialog")}
      >
        Settings
      </button>
      <button
        type="button"
        className={chip("about") + " appbar-tier"}
        data-tier="1"
        data-overlay-trigger="about"
        aria-pressed={overlays.isOpen("about")}
        onClick={() => overlays.toggle("about", "dialog")}
      >
        About
      </button>
      <button
        type="button"
        className={chip("help") + " appbar-tier"}
        data-tier="1"
        data-overlay-trigger="help"
        aria-pressed={overlays.isOpen("help")}
        onClick={() => overlays.toggle("help", "dialog")}
      >
        Help
      </button>
      <button
        type="button"
        className={chip("diagnostics") + " appbar-tier"}
        data-tier="1"
        data-overlay-trigger="diagnostics"
        aria-pressed={overlays.isOpen("diagnostics")}
        onClick={() => overlays.toggle("diagnostics", "dialog")}
      >
        Diagnostics
      </button>
      <button
        type="button"
        className={chip("knowledge") + " appbar-tier"}
        data-tier="1"
        data-overlay-trigger="knowledge"
        aria-pressed={overlays.isOpen("knowledge")}
        onClick={() => overlays.toggle("knowledge", "dialog")}
      >
        Knowledge
      </button>
      <button
        type="button"
        className={chip("builder-launch") + " appbar-tier"}
        data-tier="1"
        data-overlay-trigger="builder-launch"
        aria-pressed={overlays.isOpen("builder-launch")}
        onClick={() => overlays.toggle("builder-launch", "dialog")}
      >
        Builder
      </button>
    </div>
  );
}
