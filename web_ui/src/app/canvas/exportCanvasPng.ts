import { getNodesBounds, getViewportForBounds, type ReactFlowInstance } from "@xyflow/react";
import { toPng } from "html-to-image";

/**
 * Export canvas as PNG (Qt-removal plan R6.8) - a NET-NEW capability, not a
 * port. Recon confirmed the legacy Qt app has no canvas-wide image export at
 * all (graphlink_exporter.py only exports individual nodes' text content;
 * ChartItem/ImageNode each have their own separate per-item PNG save) - there
 * is nothing here to reimplement faithfully.
 *
 * CAPTURE TARGET: ".react-flow__viewport" - the transformed layer React Flow
 * renders nodes+edges into. Deliberately NOT the root ".react-flow" container
 * (which also contains the Background grid and MiniMap as DOM SIBLINGS of
 * the viewport, not descendants of it - confirmed against the installed
 * @xyflow/react stylesheet). Capturing the viewport directly means the grid
 * dots and minimap are excluded from the exported image with no extra
 * filter/hide logic needed - a deliberate default (a clean image of the
 * conversation graph, not a screenshot of the editing chrome around it), not
 * an oversight.
 *
 * FRAMING: always fits ALL nodes into the output image, regardless of the
 * viewer's current pan/zoom - getNodesBounds(getNodes()) + getViewportForBounds
 * (both real @xyflow/react exports, not reimplemented) compute the exact
 * translate/scale transform that frames the whole graph, matching React
 * Flow's own documented export recipe. This is intentionally NOT "screenshot
 * of what's currently visible" - the point of a canvas export is a complete,
 * shareable picture of the conversation, not a viewport crop.
 *
 * OUTPUT SIZE: a fixed 1920x1080 (a first, simple v1 default rather than
 * dynamically sizing to the content's own aspect ratio - avoids a much
 * larger, more speculative "what's the right image size for an arbitrarily
 * shaped graph" design problem for a first cut of this feature).
 *
 * MIN/MAX ZOOM: reuses the SAME 0.1-2.5 bounds SceneCanvas.tsx's own
 * <ReactFlow minZoom={0.1} maxZoom={2.5}> already enforces on the live
 * canvas, rather than inventing separate export-only limits. Adversarial
 * review caught an earlier draft's own 0.5-2 range silently CROPPING large
 * graphs whenever the required fit-everything zoom fell below 0.5,
 * contradicting this module's own "always fits ALL nodes" promise above -
 * 0.1 makes that failure mode far less likely in practice, and ties the
 * guarantee to limits the live app already respects (a graph too large to
 * fit even at 0.1 zoom couldn't be viewed in full live either, so that
 * residual case is a pre-existing whole-app constraint, not one export
 * specifically introduces).
 *
 * BACKGROUND COLOR: html-to-image's toPng() serializes the clone into a
 * standalone `data:image/svg+xml` document loaded via `new Image()` - that
 * document has NO access to this app's own stylesheets, so an unresolved
 * `var(--token)` reference passed as backgroundColor computes to nothing
 * (CSS custom properties spec) and toPng's own canvas-fill fallback path
 * also silently ignores an unresolvable color string. Passing a bare custom
 * property NAME here (not `var(...)`) and resolving it via getComputedStyle
 * against the real, live document.documentElement - where the token IS
 * defined - before ever handing anything to toPng is what actually avoids
 * that trap.
 *
 * html-to-image's toPng() clones the target node into an off-screen SVG
 * foreignObject, applies the `style` overrides ONLY to that clone, rasterizes
 * it, then discards the clone - the live page's actual pan/zoom is never
 * touched, so there is no visible flicker and nothing to restore afterward.
 */

const IMAGE_WIDTH = 1920;
const IMAGE_HEIGHT = 1080;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 2.5;
const PADDING = 0.1;
const FALLBACK_BACKGROUND_COLOR = "#1a1a1a"; // matches graphlink_desktop.py's own pywebview window background_color

function timestampedFilename(): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `graphlink-canvas-${stamp}.png`;
}

function resolveBackgroundColor(cssCustomPropertyName: string): string {
  const resolved = getComputedStyle(document.documentElement).getPropertyValue(cssCustomPropertyName).trim();
  return resolved || FALLBACK_BACKGROUND_COLOR;
}

export async function exportCanvasAsPng(
  rf: Pick<ReactFlowInstance, "getNodes">,
  backgroundColorVar: string,
): Promise<void> {
  const nodes = rf.getNodes();
  if (nodes.length === 0) {
    // An empty canvas has nothing worth exporting - silent no-op rather
    // than producing a blank image, mirroring this codebase's own
    // established "nothing to do yet" guards (autosave's empty-canvas
    // skip, saveChat's own "nothing was added" guard).
    return;
  }

  const viewportEl = document.querySelector(".react-flow__viewport") as HTMLElement | null;
  if (!viewportEl) {
    return;
  }

  const bounds = getNodesBounds(nodes);
  const viewport = getViewportForBounds(bounds, IMAGE_WIDTH, IMAGE_HEIGHT, MIN_ZOOM, MAX_ZOOM, PADDING);

  const dataUrl = await toPng(viewportEl, {
    backgroundColor: resolveBackgroundColor(backgroundColorVar),
    width: IMAGE_WIDTH,
    height: IMAGE_HEIGHT,
    style: {
      width: `${IMAGE_WIDTH}px`,
      height: `${IMAGE_HEIGHT}px`,
      transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`,
    },
  });

  const anchor = document.createElement("a");
  anchor.href = dataUrl;
  anchor.download = timestampedFilename();
  anchor.click();
}
