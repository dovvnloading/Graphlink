import { useReactFlow } from "@xyflow/react";
import { exportCanvasAsPng } from "../canvas/exportCanvasPng";
import type { SceneStore } from "../canvas/sceneStore";
import { useOverlays } from "../overlays/overlays";

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
 *   app-chat-library topic - see SceneStore's own comment on why); provider
 *   mode select -> still deferred to R4's own remaining work, rendered
 *   disabled with the phase called out.
 * - Export PNG -> real as of R6.8, a net-new capability (no legacy
 *   canvas-wide export exists) - pure client-side DOM rasterization via
 *   exportCanvasAsPng, same "zero backend round-trip" shape as the
 *   Zoom/Fit All buttons right next to it, not an intent dispatch.
 */

export function AppBar({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const { zoomIn, zoomOut, setViewport, fitView, getViewport, getNodes } = useReactFlow();

  const chip = (surface: string) =>
    "appbar-btn appbar-btn-checkable" + (overlays.isOpen(surface) ? " checked" : "");

  const resetZoom = () => {
    const viewport = getViewport();
    setViewport({ ...viewport, zoom: 1 }, { duration: 200 });
  };

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
      <button
        type="button"
        className={chip("pins")}
        data-overlay-trigger="pins"
        aria-pressed={overlays.isOpen("pins")}
        title="Navigation pins"
        onClick={() => overlays.toggle("pins", "popover")}
      >
        Pins
      </button>
      <button type="button" className="appbar-btn" onClick={() => store.organizeNodes()}>
        Organize
      </button>

      <span className="appbar-separator" />

      <button type="button" className="appbar-btn" onClick={() => zoomIn({ duration: 150 })}>
        Zoom In
      </button>
      <button type="button" className="appbar-btn" onClick={() => zoomOut({ duration: 150 })}>
        Zoom Out
      </button>
      <button type="button" className="appbar-btn" onClick={resetZoom}>
        Reset
      </button>
      <button type="button" className="appbar-btn" onClick={() => fitView({ duration: 200 })}>
        Fit All
      </button>
      <button
        type="button"
        className="appbar-btn"
        title="Export the whole canvas as a PNG image"
        onClick={() => void exportCanvasAsPng({ getNodes, getViewport, setViewport }, "--gl-surface-window")}
      >
        Export PNG
      </button>

      <span className="appbar-separator" />

      <button
        type="button"
        className={chip("view")}
        data-overlay-trigger="view"
        aria-pressed={overlays.isOpen("view")}
        onClick={() => overlays.toggle("view", "popover")}
      >
        View
      </button>
      <button
        type="button"
        className={chip("plugins")}
        data-overlay-trigger="plugins"
        aria-pressed={overlays.isOpen("plugins")}
        onClick={() => overlays.toggle("plugins", "popover")}
      >
        Plugins <span className="appbar-chevron">&#9662;</span>
      </button>

      <span className="appbar-spacer" />

      <select
        className="appbar-mode-select"
        value="Ollama (Local)"
        aria-label="Provider mode"
        disabled
        title="Provider modes land in R4"
        onChange={() => {}}
      >
        <option>Ollama (Local)</option>
      </select>

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
        className={chip("about")}
        data-overlay-trigger="about"
        aria-pressed={overlays.isOpen("about")}
        onClick={() => overlays.toggle("about", "dialog")}
      >
        About
      </button>
      <button
        type="button"
        className={chip("help")}
        data-overlay-trigger="help"
        aria-pressed={overlays.isOpen("help")}
        onClick={() => overlays.toggle("help", "dialog")}
      >
        Help
      </button>
    </div>
  );
}
