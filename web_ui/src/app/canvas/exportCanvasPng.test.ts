import { getNodesBounds, getViewportForBounds } from "@xyflow/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// html-to-image performs real DOM-to-canvas rasterization that jsdom can't
// meaningfully produce - this is the first vi.mock (module-level) in this
// codebase; every prior test mocked globals (fetch/clipboard/URL) instead,
// since there was always a real browser global to fake. Here there's no
// global to stand in for an npm package's own exported function, so mocking
// the module itself is the only option.
const toPngMock = vi.fn();
vi.mock("html-to-image", () => ({
  toPng: (...args: unknown[]) => toPngMock(...args),
}));

import { exportCanvasAsPng } from "./exportCanvasPng";

function fakeNode(id: string, x: number, y: number) {
  return {
    id,
    position: { x, y },
    width: 160,
    height: 80,
    measured: { width: 160, height: 80 },
    data: {},
  };
}

describe("exportCanvasAsPng", () => {
  let viewportEl: HTMLDivElement;

  beforeEach(() => {
    viewportEl = document.createElement("div");
    viewportEl.className = "react-flow__viewport";
    document.body.appendChild(viewportEl);
    toPngMock.mockReset();
    toPngMock.mockResolvedValue("data:image/png;base64,fake");
  });

  afterEach(() => {
    document.body.removeChild(viewportEl);
    document.documentElement.style.removeProperty("--gl-surface-window");
    vi.restoreAllMocks();
  });

  it("does nothing when the canvas has no nodes - no rasterization, no download", async () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng({ getNodes: () => [] }, "--gl-surface-window");

    expect(toPngMock).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it("does nothing when .react-flow__viewport isn't in the DOM", async () => {
    document.body.removeChild(viewportEl); // remove it before calling

    await exportCanvasAsPng({ getNodes: () => [fakeNode("n1", 0, 0)] }, "--gl-surface-window");

    expect(toPngMock).not.toHaveBeenCalled();

    document.body.appendChild(viewportEl); // restore for afterEach's own removal
  });

  it("resolves the CSS custom property to a concrete color from the real document, not a raw var() reference", async () => {
    // Adversarial-review finding: html-to-image serializes the capture into
    // an isolated data:image/svg+xml document with no access to this app's
    // own stylesheets - a raw "var(--x)" string passed straight through
    // resolves to nothing there. document.documentElement is where the
    // token IS actually defined in the real app, so resolving against it
    // BEFORE calling toPng is what makes the color actually work.
    document.documentElement.style.setProperty("--gl-surface-window", "#1E1E1E");
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng({ getNodes: () => [fakeNode("n1", 0, 0)] }, "--gl-surface-window");

    const [, options] = toPngMock.mock.calls[0];
    expect(options.backgroundColor).toBe("#1E1E1E");
  });

  it("falls back to a concrete dark color when the CSS custom property isn't defined", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng({ getNodes: () => [fakeNode("n1", 0, 0)] }, "--gl-surface-window-does-not-exist");

    const [, options] = toPngMock.mock.calls[0];
    expect(options.backgroundColor).toBe("#1a1a1a");
  });

  it("rasterizes the .react-flow__viewport element at a fixed 1920x1080, framed to the REAL bounds of all given nodes", async () => {
    // jsdom has no navigation implementation - a real anchor.click() would
    // spam "Not implemented: navigation" to the virtual console (same
    // reasoning as ImageNodeView.test.tsx's own handleExportImage tests).
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    const nodes = [fakeNode("n1", 0, 0), fakeNode("n2", 300, 200)];
    // Adversarial-review finding: a bare regex-shape match on the transform
    // string would still pass even if bounds computation silently ignored
    // node positions entirely. Computing the SAME expected viewport via the
    // real @xyflow/react functions (not hand-derived math) and asserting
    // exact equality actually exercises that the two nodes' real positions
    // drove the result.
    const expectedBounds = getNodesBounds(nodes);
    const expectedViewport = getViewportForBounds(expectedBounds, 1920, 1080, 0.1, 2.5, 0.1);

    await exportCanvasAsPng({ getNodes: () => nodes }, "--gl-surface-window");

    expect(toPngMock).toHaveBeenCalledOnce();
    const [target, options] = toPngMock.mock.calls[0];
    expect(target).toBe(viewportEl);
    expect(options.width).toBe(1920);
    expect(options.height).toBe(1080);
    expect(options.style.width).toBe("1920px");
    expect(options.style.height).toBe("1080px");
    expect(options.style.transform).toBe(
      `translate(${expectedViewport.x}px, ${expectedViewport.y}px) scale(${expectedViewport.zoom})`,
    );
  });

  it("a single node produces a different transform than two spread-out nodes", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng({ getNodes: () => [fakeNode("n1", 0, 0)] }, "--gl-surface-window");
    const singleNodeTransform = toPngMock.mock.calls[0][1].style.transform;

    toPngMock.mockClear();
    await exportCanvasAsPng({ getNodes: () => [fakeNode("n1", 0, 0), fakeNode("n2", 900, 700)] }, "--gl-surface-window");
    const twoNodeTransform = toPngMock.mock.calls[0][1].style.transform;

    expect(twoNodeTransform).not.toBe(singleNodeTransform);
  });

  it("triggers a download of the resulting data URL with a graphlink-canvas-*.png filename", async () => {
    const captured: { anchor: HTMLAnchorElement | null } = { anchor: null };
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      captured.anchor = this;
    });

    await exportCanvasAsPng({ getNodes: () => [fakeNode("n1", 0, 0)] }, "--gl-surface-window");

    expect(captured.anchor?.getAttribute("href")).toBe("data:image/png;base64,fake");
    expect(captured.anchor?.getAttribute("download")).toMatch(/^graphlink-canvas-.+\.png$/);
  });
});
