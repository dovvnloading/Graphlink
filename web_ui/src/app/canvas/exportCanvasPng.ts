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
 * WHY THE LIVE VIEWPORT IS SET AND RESTORED (audit finding, was a real bug):
 * an earlier version applied the export transform ONLY to html-to-image's own
 * clone and never touched the live canvas - which sounds strictly better
 * (no flicker, nothing to restore) but silently made the exported IMAGE
 * CONTENT depend on the viewer's current zoom, the exact thing the FRAMING
 * paragraph above promises it doesn't. Every node view derives its collapsed
 * state from React Flow's LIVE store zoom (ChatNodeView.tsx's
 * `lodCollapsed = zoom < LOD_ZOOM_THRESHOLD`, threshold 0.5 in
 * canvasConstants.ts), and a node's body is behind `{!collapsed && ...}` -
 * so a user zoomed out to 0.4 to see their whole graph (the natural thing to
 * do right before exporting it) got a PNG of title bars with no content.
 * Overriding the clone's CSS transform cannot fix that: the clone is taken
 * from DOM React has already rendered collapsed. The only fix is to put the
 * live store at the export zoom, let React re-render, and capture that -
 * hence setViewport + a double-rAF wait for the resulting paint, with the
 * user's own viewport restored in a `finally` so a failed export can never
 * strand them somewhere they didn't navigate to. The cost is one frame of
 * visible movement during an export; the benefit is that the exported image
 * no longer depends on where the user happened to be looking.
 *
 * OUTPUT SIZE: a fixed 1920x1080 (a first, simple v1 default rather than
 * dynamically sizing to the content's own aspect ratio - avoids a much
 * larger, more speculative "what's the right image size for an arbitrarily
 * shaped graph" design problem for a first cut of this feature). `pixelRatio:
 * 1` is passed explicitly to make that literally true: html-to-image
 * otherwise multiplies the canvas by window.devicePixelRatio, so the same
 * graph exported from a 150%-scaled Windows display came out 2880x1620 and
 * from a 200% display 3840x2160 - three users, three different files, none of
 * them the documented size.
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
 * FAILURE HANDLING: toPng genuinely rejects in states this app already
 * anticipates - one unreachable /api/assets/{id} (ImageNodeView renders an
 * "Image unavailable" placeholder for exactly that case) makes html-to-image's
 * own embedImageNode reject the whole capture. Both call sites `void` this
 * function's promise, so an unhandled rejection would mean the button does
 * nothing, silently, with no way for the user to tell a failed export from a
 * slow one. Caught and logged here instead, matching ImageNodeView.tsx's own
 * handleExportImage - the directly analogous "rasterize/fetch, then download
 * via a temporary anchor" helper in this codebase.
 *
 * REVIEW-FIX: chart nodes render through a lazily-loaded chunk
 * (ChartNodeView.tsx's LazyChartRenderer, `import("./charts/ChartRenderer")`)
 * with a "Loading chart…" Suspense fallback (`.chart-node-placeholder`) shown
 * until that chunk resolves. The two rAF frames nextPaint() waits for are
 * enough for React Flow's OWN re-render at the export zoom to paint, but say
 * nothing about a separate async chunk load that may still be in flight -
 * the very first time a chart node mounts in a session (nothing before this
 * export has ever triggered that import) captured the literal placeholder
 * text instead of the chart. waitForChartPlaceholdersToClear polls the
 * captured subtree for that class and proceeds only once none remain (or a
 * bounded timeout elapses - a chunk load failure must degrade to "export
 * anyway, placeholder and all" rather than hang the whole export forever).
 */

const IMAGE_WIDTH = 1920;
const IMAGE_HEIGHT = 1080;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 2.5;
const PADDING = 0.1;
const FALLBACK_BACKGROUND_COLOR = "#1a1a1a"; // matches graphlink_desktop.py's own pywebview window background_color
const CHART_CHUNK_LOAD_TIMEOUT_MS = 5000;
const CHART_CHUNK_POLL_INTERVAL_MS = 50;

function timestampedFilename(): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `graphlink-canvas-${stamp}.png`;
}

function resolveBackgroundColor(cssCustomPropertyName: string): string {
  const resolved = getComputedStyle(document.documentElement).getPropertyValue(cssCustomPropertyName).trim();
  return resolved || FALLBACK_BACKGROUND_COLOR;
}

/** Two frames, not one: the first rAF fires after React has committed the
 * setViewport state update, the second after the browser has actually laid
 * out and painted it. Capturing on the first would race the very re-render
 * this wait exists to observe. */
function nextPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

/** Every Suspense fallback that means "this node has not rendered yet".
 * Kept as one constant so a third fallback cannot be added without a reader
 * of the export path seeing it. */
export const NODE_LOADING_SELECTOR = ".chart-node-placeholder, .scene-node-loading";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Polls `root` for any still-loading node and resolves once none remain.
 *
 * TWO fallbacks qualify. `.chart-node-placeholder` is ChartNodeView.tsx's own,
 * and was the only one this waited on. ADR-019's node-view code split added
 * `.scene-node-loading` (lazyNodeViews.tsx), which is shown while ANY node
 * kind's chunk loads - and export is precisely when the most chunks are
 * unloaded, because exportCanvasAsPng disables onlyRenderVisibleElements to
 * mount the off-viewport nodes, which are exactly the ones whose chunks were
 * never fetched. Without this the PNG captures "Loading..." shells.
 * Bounded by `timeoutMs` so a genuinely stuck or failed chunk load (offline,
 * a bad deploy) degrades to "capture whatever is there" rather than hanging
 * the export indefinitely - consistent with this module's broader "never
 * strand the user" posture (see the try/finally around the capture below).
 * Exported, and `timeoutMs`/`pollIntervalMs` are parameters rather than bare
 * references to the module constants, purely so the timeout path is directly
 * unit-testable in real time without either waiting out the real 5s default
 * or reaching for fake timers against a function that also awaits a real
 * requestAnimationFrame upstream (nextPaint, below) - same "parameterize the
 * interval for direct testing" shape as makeDebouncedChartResize's own
 * `debounceMs` parameter in ChartNodeView.tsx. */
export async function waitForChartPlaceholdersToClear(
  root: HTMLElement,
  timeoutMs: number = CHART_CHUNK_LOAD_TIMEOUT_MS,
  pollIntervalMs: number = CHART_CHUNK_POLL_INTERVAL_MS,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (root.querySelector(NODE_LOADING_SELECTOR) && Date.now() < deadline) {
    await sleep(pollIntervalMs);
  }
}

export async function exportCanvasAsPng(
  rf: Pick<ReactFlowInstance, "getNodes" | "getViewport" | "setViewport">,
  backgroundColorVar: string,
  // ADR-011 stage 11.2 (virtualization audit): defaults to a no-op so every
  // existing caller/test that predates onlyRenderVisibleElements keeps
  // working unchanged - SceneCanvas.tsx's real <ReactFlow> is the only
  // consumer that needs this to do anything (it reads the flag back via
  // SceneStore.getExportInProgress). See that field's own doc in
  // sceneStore.ts/SceneCanvas.tsx for the full reasoning: the export
  // computes a viewport that fits every node into a FIXED 1920x1080 frame,
  // which the LIVE on-screen canvas container can be smaller than -
  // onlyRenderVisibleElements filters against the container's REAL client
  // size, so a node that fits inside the export frame can still fall
  // outside the live container's actual bounds and never mount, silently
  // missing from the captured DOM regardless of the viewport math below
  // being correct. Suspending virtualization for the capture's duration is
  // the only fix that doesn't depend on the live container happening to
  // already be at least 1920x1080.
  setExportInProgress: (value: boolean) => void = () => {},
): Promise<void> {
  const nodes = rf.getNodes();
  if (nodes.length === 0) {
    // An empty canvas has nothing worth exporting - silent no-op rather
    // than producing a blank image, mirroring this codebase's own
    // established "nothing to do yet" guards (autosave's empty-canvas
    // skip, saveChat's own "nothing was added" guard). Deliberately before
    // setExportInProgress(true) below - nothing to suspend virtualization
    // for either.
    return;
  }

  const viewportEl = document.querySelector(".react-flow__viewport") as HTMLElement | null;
  if (!viewportEl) {
    return;
  }

  const bounds = getNodesBounds(nodes);
  const viewport = getViewportForBounds(bounds, IMAGE_WIDTH, IMAGE_HEIGHT, MIN_ZOOM, MAX_ZOOM, PADDING);

  const userViewport = rf.getViewport();
  setExportInProgress(true);
  try {
    await rf.setViewport(viewport);
    await nextPaint();
    await waitForChartPlaceholdersToClear(viewportEl);

    const dataUrl = await toPng(viewportEl, {
      backgroundColor: resolveBackgroundColor(backgroundColorVar),
      width: IMAGE_WIDTH,
      height: IMAGE_HEIGHT,
      pixelRatio: 1,
      style: {
        // Redundant with the live viewport we just set, deliberately: it
        // pins the clone's framing to the exact numbers computed above even
        // if React Flow's own transform hasn't settled, and it is what React
        // Flow's documented export recipe does.
        width: `${IMAGE_WIDTH}px`,
        height: `${IMAGE_HEIGHT}px`,
        transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`,
      },
    });

    const anchor = document.createElement("a");
    anchor.href = dataUrl;
    anchor.download = timestampedFilename();
    anchor.click();
  } catch (error) {
    console.error("[export-png] Export failed:", error);
  } finally {
    await rf.setViewport(userViewport);
    setExportInProgress(false);
  }
}
