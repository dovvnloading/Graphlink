import { getNodesBounds, getViewportForBounds, type Viewport } from "@xyflow/react";
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

const USER_VIEWPORT: Viewport = { x: -50, y: -25, zoom: 0.4 };

/** A stand-in for the bits of ReactFlowInstance this module actually uses,
 * recording every setViewport call so the set-then-restore contract can be
 * asserted on rather than assumed. */
function fakeRf(nodes: ReturnType<typeof fakeNode>[]) {
  const setViewportCalls: Viewport[] = [];
  return {
    getNodes: () => nodes,
    getViewport: () => USER_VIEWPORT,
    setViewport: (viewport: Viewport) => {
      setViewportCalls.push(viewport);
      return Promise.resolve(true);
    },
    setViewportCalls,
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
    const rf = fakeRf([]);

    await exportCanvasAsPng(rf, "--gl-surface-window");

    expect(toPngMock).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
    // Nothing to export must also mean nothing moved on screen.
    expect(rf.setViewportCalls).toEqual([]);
  });

  it("does nothing when .react-flow__viewport isn't in the DOM", async () => {
    document.body.removeChild(viewportEl); // remove it before calling
    const rf = fakeRf([fakeNode("n1", 0, 0)]);

    await exportCanvasAsPng(rf, "--gl-surface-window");

    expect(toPngMock).not.toHaveBeenCalled();
    expect(rf.setViewportCalls).toEqual([]);

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

    await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window");

    const [, options] = toPngMock.mock.calls[0];
    expect(options.backgroundColor).toBe("#1E1E1E");
  });

  it("falls back to a concrete dark color when the CSS custom property isn't defined", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window-does-not-exist");

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

    await exportCanvasAsPng(fakeRf(nodes), "--gl-surface-window");

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

  it("pins pixelRatio to 1 so the output really is 1920x1080 on a high-DPI display", async () => {
    // Audit finding: without this, html-to-image multiplies the canvas by
    // window.devicePixelRatio - the same graph exported from a 150%-scaled
    // Windows display came out 2880x1620, contradicting the fixed size this
    // module documents and giving two users different files for one graph.
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window");

    const [, options] = toPngMock.mock.calls[0];
    expect(options.pixelRatio).toBe(1);
  });

  it("a single node produces a different transform than two spread-out nodes", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window");
    const singleNodeTransform = toPngMock.mock.calls[0][1].style.transform;

    toPngMock.mockClear();
    await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0), fakeNode("n2", 900, 700)]), "--gl-surface-window");
    const twoNodeTransform = toPngMock.mock.calls[0][1].style.transform;

    expect(twoNodeTransform).not.toBe(singleNodeTransform);
  });

  it("triggers a download of the resulting data URL with a graphlink-canvas-*.png filename", async () => {
    const captured: { anchor: HTMLAnchorElement | null } = { anchor: null };
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      captured.anchor = this;
    });

    await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window");

    expect(captured.anchor?.getAttribute("href")).toBe("data:image/png;base64,fake");
    expect(captured.anchor?.getAttribute("download")).toMatch(/^graphlink-canvas-.+\.png$/);
  });

  // -- audit fix: the live viewport must be at the export zoom during capture --

  it("puts the LIVE viewport at the export framing before rasterizing, then restores the user's own", async () => {
    // Audit finding (real bug): node views read React Flow's live store zoom
    // to decide whether to render collapsed (lodCollapsed = zoom < 0.5). With
    // the export transform applied only to html-to-image's clone, a user
    // zoomed out below that threshold exported title bars with no content -
    // the clone is taken from DOM React already rendered collapsed, so no
    // CSS override on the clone can bring the bodies back. Only moving the
    // live store makes React re-render at the export zoom.
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const nodes = [fakeNode("n1", 0, 0), fakeNode("n2", 900, 700)];
    const expectedViewport = getViewportForBounds(getNodesBounds(nodes), 1920, 1080, 0.1, 2.5, 0.1);

    const rf = fakeRf(nodes);
    let viewportDuringCapture: Viewport | undefined;
    toPngMock.mockImplementation(() => {
      viewportDuringCapture = rf.setViewportCalls[rf.setViewportCalls.length - 1];
      return Promise.resolve("data:image/png;base64,fake");
    });

    await exportCanvasAsPng(rf, "--gl-surface-window");

    // The zoom in force AT capture time is the computed export zoom, not the
    // user's 0.4 - this is the assertion the old implementation would fail.
    expect(viewportDuringCapture).toEqual(expectedViewport);
    expect(expectedViewport.zoom).not.toBe(USER_VIEWPORT.zoom);
    // ...and the user is put back exactly where they were.
    expect(rf.setViewportCalls).toEqual([expectedViewport, USER_VIEWPORT]);
  });

  it("restores the user's viewport and logs, without rejecting, when rasterization fails", async () => {
    // Audit finding: toPng genuinely rejects when a node's image asset is
    // unreachable (a state ImageNodeView already renders a placeholder for).
    // Both call sites `void` this promise, so an unhandled rejection meant a
    // dead button and a user stranded at the export zoom.
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    toPngMock.mockRejectedValue(new Error("Failed to embed image"));

    const rf = fakeRf([fakeNode("n1", 0, 0)]);

    await expect(exportCanvasAsPng(rf, "--gl-surface-window")).resolves.toBeUndefined();

    expect(clickSpy).not.toHaveBeenCalled();
    expect(consoleSpy).toHaveBeenCalledWith("[export-png] Export failed:", expect.any(Error));
    // The user must not be left stranded at the export zoom.
    expect(rf.setViewportCalls[rf.setViewportCalls.length - 1]).toEqual(USER_VIEWPORT);
  });

  // -- ADR-011 stage 11.2: virtualization suspended for the capture --

  describe("setExportInProgress (ADR-011 stage 11.2)", () => {
    it("is set true before rasterizing and false again once the capture completes", async () => {
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
      const calls: boolean[] = [];
      const setExportInProgress = (value: boolean) => calls.push(value);

      await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window", setExportInProgress);

      expect(calls).toEqual([true, false]);
    });

    it("is already true by the time toPng actually rasterizes", async () => {
      // Audit finding (ADR-011 stage 11.2): the export computes a viewport
      // that fits every node into a fixed 1920x1080 frame, which the LIVE
      // on-screen canvas container can be smaller than - a node that fits
      // the export frame can still fall outside the live container's real
      // bounds under onlyRenderVisibleElements and never mount for capture.
      // Suspending virtualization only works if it is ALREADY suspended
      // by the time toPng actually reads the DOM, not merely by the time
      // this function returns.
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
      const rf = fakeRf([fakeNode("n1", 0, 0)]);
      let currentFlag = false;
      let flagAtCaptureTime: boolean | null = null;
      toPngMock.mockImplementation(() => {
        flagAtCaptureTime = currentFlag;
        return Promise.resolve("data:image/png;base64,fake");
      });

      await exportCanvasAsPng(rf, "--gl-surface-window", (value) => {
        currentFlag = value;
      });

      expect(flagAtCaptureTime).toBe(true);
      // ...and restored to false once the whole export settles.
      expect(currentFlag).toBe(false);
    });

    it("still resolves to false even when rasterization fails", async () => {
      vi.spyOn(console, "error").mockImplementation(() => {});
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
      toPngMock.mockRejectedValue(new Error("Failed to embed image"));
      const calls: boolean[] = [];

      await exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window", (value) => calls.push(value));

      expect(calls).toEqual([true, false]);
    });

    it("is never called at all when there is nothing to export (no nodes)", async () => {
      const calls: boolean[] = [];
      await exportCanvasAsPng(fakeRf([]), "--gl-surface-window", (value) => calls.push(value));
      expect(calls).toEqual([]);
    });

    it("defaults to a no-op when the caller omits it - every pre-11.2 call site keeps working unchanged", async () => {
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
      await expect(exportCanvasAsPng(fakeRf([fakeNode("n1", 0, 0)]), "--gl-surface-window")).resolves.toBeUndefined();
    });
  });
});
