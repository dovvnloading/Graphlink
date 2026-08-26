import { Position, ReactFlowProvider, useStoreApi } from "@xyflow/react";
import { act, render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionCanvas, type ConnectionSpec } from "./ConnectionCanvas";
import { anchorPoint, connectionPath, type ConnectionPath, type HandleGeometry } from "./connectionGeometry";

/**
 * ConnectionCanvas draws by reading @xyflow/react's OWN internal store
 * (nodeLookup/width/height/transform) every animation frame - there is no
 * `nodes` prop to pass in. Writing directly into that store via useStoreApi()
 * inside an effect, from a sibling rendered under the same <ReactFlowProvider>,
 * is the established way this codebase drives it in tests (see the identical
 * ZoomSetter idiom in ConversationNodeView.test.tsx / GitlinkNodeView.test.tsx
 * / WebResearchNodeView.test.tsx / CodeSandboxNodeView.test.tsx) - real
 * per-node measurement (ResizeObserver -> handleBounds) never happens under
 * jsdom regardless (confirmed by SceneCanvas.virtualization.test.tsx's own
 * recon: xyflow's updateNodeInternals hits `window.DOMMatrixReadOnly is not a
 * constructor`), so this is also the only way to get handleBounds populated
 * at all here.
 */
interface FakeInternalNode {
  internals: {
    positionAbsolute: { x: number; y: number };
    handleBounds?: {
      source?: Array<{ x: number; y: number; width: number; height: number; position: Position }> | null;
      target?: Array<{ x: number; y: number; width: number; height: number; position: Position }> | null;
    };
  };
}

function StoreSetter({
  nodes,
  width = 800,
  height = 600,
  transform = [0, 0, 1],
}: {
  nodes: Record<string, FakeInternalNode>;
  width?: number;
  height?: number;
  transform?: [number, number, number];
}) {
  const store = useStoreApi();
  useEffect(() => {
    store.setState({
      width,
      height,
      transform,
      nodeLookup: new Map(Object.entries(nodes)) as unknown as ReturnType<typeof store.getState>["nodeLookup"],
    });
  }, [store, nodes, width, height, transform]);
  return null;
}

type ConnectionCanvasProps = React.ComponentProps<typeof ConnectionCanvas>;

function renderConnectionCanvas(
  props: Partial<ConnectionCanvasProps> = {},
  storeProps: React.ComponentProps<typeof StoreSetter> = { nodes: {} },
) {
  const defaultProps: ConnectionCanvasProps = {
    connections: [],
    hoveredId: null,
    selectedId: null,
    fadeEnabled: false,
    stroke: "#888888",
    selectedStroke: "#0088ff",
    fadedOpacity: 0.2,
    ...props,
  };
  const utils = render(
    <ReactFlowProvider>
      <StoreSetter {...storeProps} />
      <ConnectionCanvas {...defaultProps} />
    </ReactFlowProvider>,
  );
  return { ...utils, canvas: utils.container.querySelector("canvas") as HTMLCanvasElement };
}

/** A minimal call-recording stand-in for CanvasRenderingContext2D - only the
 * subset ConnectionCanvas actually calls needs a spy. `stroke()` snapshots
 * the style properties in force at the moment it's called, since the
 * component sets them as plain mutable properties right before tracing each
 * connection (there's no other way to observe "what style applied to which
 * connection" from outside). */
function fakeCtx2D() {
  const strokeCalls: Array<{ strokeStyle: unknown; lineWidth: number; globalAlpha: number }> = [];
  const ctx = {
    lineCap: "",
    globalAlpha: 1,
    strokeStyle: "",
    lineWidth: 1,
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    bezierCurveTo: vi.fn(),
    stroke: vi.fn(() => {
      strokeCalls.push({ strokeStyle: ctx.strokeStyle, lineWidth: ctx.lineWidth, globalAlpha: ctx.globalAlpha });
    }),
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, strokeCalls };
}

/** Advances exactly one animation frame under fake timers. */
function tickFrame() {
  act(() => {
    vi.advanceTimersByTime(16);
  });
}

const NODE_A: FakeInternalNode = {
  internals: {
    positionAbsolute: { x: 100, y: 100 },
    handleBounds: { source: [{ x: 0, y: 40, width: 8, height: 8, position: Position.Bottom }], target: null },
  },
};
const NODE_B: FakeInternalNode = {
  internals: {
    positionAbsolute: { x: 300, y: 300 },
    handleBounds: { source: null, target: [{ x: 0, y: -8, width: 8, height: 8, position: Position.Top }] },
  },
};

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ConnectionCanvas rendering", () => {
  it("renders a canvas that is purely presentational: not hit-testable, drawn behind everything, hidden from a11y", () => {
    const { canvas } = renderConnectionCanvas();
    expect(canvas).not.toBeNull();
    expect(canvas.className).toBe("scene-connection-canvas");
    expect(canvas.style.pointerEvents).toBe("none");
    expect(canvas.style.zIndex).toBe("-1");
    expect(canvas.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("ConnectionCanvas animation-frame loop", () => {
  it("schedules exactly one frame and never reschedules once it finds no 2D context (jsdom's own default)", () => {
    // No getContext mock here on purpose: jsdom's real HTMLCanvasElement
    // returns null from getContext("2d") (confirmed empirically - jsdom's own
    // virtual console also logs a harmless "Not implemented" notice for it,
    // independent of any console.error mock), which is exactly the case this
    // component's own module doc calls out by name.
    const rafSpy = vi.spyOn(window, "requestAnimationFrame");

    renderConnectionCanvas();
    expect(rafSpy).toHaveBeenCalledTimes(1);

    tickFrame(); // fires the first frame; draw() finds ctx===null and returns false
    expect(rafSpy).toHaveBeenCalledTimes(1); // not rescheduled
  });

  it("keeps rescheduling every frame while a 2D context is available, and cancels the pending frame on unmount", () => {
    const { ctx } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const rafSpy = vi.spyOn(window, "requestAnimationFrame");
    const cafSpy = vi.spyOn(window, "cancelAnimationFrame");

    const { unmount } = renderConnectionCanvas();
    expect(rafSpy).toHaveBeenCalledTimes(1);

    tickFrame();
    expect(rafSpy).toHaveBeenCalledTimes(2); // draw() succeeded -> loop reschedules
    tickFrame();
    expect(rafSpy).toHaveBeenCalledTimes(3);

    const pendingHandle = rafSpy.mock.results[2]!.value;
    unmount();
    expect(cafSpy).toHaveBeenCalledWith(pendingHandle);
  });
});

describe("ConnectionCanvas backing-store sizing", () => {
  it("sizes the backing store to the store's width/height times devicePixelRatio, and sets CSS size in px", () => {
    const { ctx } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    vi.spyOn(window, "devicePixelRatio", "get").mockReturnValue(2);

    const { canvas } = renderConnectionCanvas({}, { nodes: {}, width: 400, height: 300 });
    tickFrame();

    expect(canvas.width).toBe(800); // 400 * 2
    expect(canvas.height).toBe(600); // 300 * 2
    expect(canvas.style.width).toBe("400px");
    expect(canvas.style.height).toBe("300px");
  });
});

describe("ConnectionCanvas geometry resolution", () => {
  it("resolves each connection's geometry from the store's live node positions and handle bounds, and reports it via onGeometry", () => {
    const { ctx } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onGeometry = vi.fn();

    const curved: ConnectionSpec = { id: "curved", source: "a", target: "b", orthogonal: false };
    const stepped: ConnectionSpec = { id: "stepped", source: "a", target: "b", orthogonal: true };

    renderConnectionCanvas(
      { connections: [curved, stepped], onGeometry },
      { nodes: { a: NODE_A, b: NODE_B } },
    );
    tickFrame();

    // Computed via the SAME real, independently-tested pure functions
    // (connectionGeometry.test.ts), not hand-derived - this test is only
    // pinning that ConnectionCanvas wires the store's raw node/handle data
    // into them correctly, not re-deriving the geometry math itself.
    const sourceHandle: HandleGeometry = { x: 0, y: 40, width: 8, height: 8, side: "bottom" };
    const targetHandle: HandleGeometry = { x: 0, y: -8, width: 8, height: 8, side: "top" };
    const from = anchorPoint(100, 100, sourceHandle);
    const to = anchorPoint(300, 300, targetHandle);

    expect(onGeometry).toHaveBeenCalledTimes(1);
    const geometry = onGeometry.mock.calls[0]![0] as Map<string, ConnectionPath>;
    expect(geometry.size).toBe(2);
    expect(geometry.get("curved")).toEqual(connectionPath(from, "bottom", to, "top", false));
    // Same endpoints, but orthogonal:true must reach connectionPath as true too.
    expect(geometry.get("stepped")).toEqual(connectionPath(from, "bottom", to, "top", true));
  });

  it("falls back to 'bottom' when a handle's own position is missing, same default sideOf itself falls back to", () => {
    const { ctx } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onGeometry = vi.fn();
    const undocumentedPosition = undefined as unknown as Position;
    const weird: FakeInternalNode = {
      internals: {
        positionAbsolute: { x: 0, y: 0 },
        handleBounds: {
          source: [{ x: 0, y: 0, width: 8, height: 8, position: undocumentedPosition }],
          target: null,
        },
      },
    };

    renderConnectionCanvas(
      { connections: [{ id: "conn1", source: "weird", target: "b", orthogonal: false }], onGeometry },
      { nodes: { weird, b: NODE_B } },
    );
    tickFrame();

    const geometry = onGeometry.mock.calls[0]![0] as Map<string, ConnectionPath>;
    // bottom-side anchor: x = nodeX + handle.x + handle.width/2 = 0+0+4 = 4;
    // y = nodeY + handle.y + handle.height = 0+0+8 = 8.
    expect(geometry.get("conn1")!.source).toEqual({ x: 4, y: 8 });
  });

  it("skips a connection whose source or target id isn't in the store's nodeLookup, without throwing", () => {
    const { ctx } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onGeometry = vi.fn();

    renderConnectionCanvas(
      {
        connections: [
          { id: "missing-target", source: "a", target: "ghost", orthogonal: false },
          { id: "missing-source", source: "ghost", target: "b", orthogonal: false },
        ],
        onGeometry,
      },
      { nodes: { a: NODE_A, b: NODE_B } },
    );
    tickFrame();

    expect(onGeometry.mock.calls[0]![0].size).toBe(0);
  });

  it("skips a connection whose endpoint node has no measured handle bounds yet", () => {
    const { ctx } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
    const onGeometry = vi.fn();
    const unmeasured: FakeInternalNode = { internals: { positionAbsolute: { x: 0, y: 0 } } }; // no handleBounds

    renderConnectionCanvas(
      { connections: [{ id: "conn1", source: "a", target: "unmeasured", orthogonal: false }], onGeometry },
      { nodes: { a: NODE_A, unmeasured } },
    );
    tickFrame();

    expect(onGeometry.mock.calls[0]![0].size).toBe(0);
  });
});

describe("ConnectionCanvas hover/selection/fade styling", () => {
  it("draws selected heavier and in the selected colour, exempts only the hovered connection from fade-dimming, and resets alpha after", () => {
    // Real, slightly non-obvious behaviour pinned here: the code's fade check
    // is `spec.id !== hovered`, with no exemption for `selected` - a selected
    // connection that ISN'T also the hovered one IS dimmed like any other
    // when fading is on. This locks that actual behaviour down rather than
    // the possibly-more-intuitive "selected is always exempt" one.
    const { ctx, strokeCalls } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);

    renderConnectionCanvas(
      {
        connections: [
          { id: "selected", source: "a", target: "b", orthogonal: false },
          { id: "hovered", source: "a", target: "b", orthogonal: false },
          { id: "plain", source: "a", target: "b", orthogonal: false },
        ],
        selectedId: "selected",
        hoveredId: "hovered",
        fadeEnabled: true,
        stroke: "#888888",
        selectedStroke: "#0088ff",
        fadedOpacity: 0.15,
      },
      { nodes: { a: NODE_A, b: NODE_B }, transform: [0, 0, 2] },
    );
    tickFrame();

    expect(strokeCalls).toEqual([
      { strokeStyle: "#0088ff", lineWidth: 1.25, globalAlpha: 0.15 }, // selected: 2.5/zoom(2), still dimmed
      { strokeStyle: "#888888", lineWidth: 0.75, globalAlpha: 1 }, // hovered: 1.5/zoom(2), exempt from dimming
      { strokeStyle: "#888888", lineWidth: 0.75, globalAlpha: 0.15 }, // plain: dimmed
    ]);
    expect(ctx.globalAlpha).toBe(1); // reset after the loop, so it never leaks to the next consumer of ctx
  });

  it("draws every connection at full opacity, undimmed, when fading is disabled", () => {
    const { ctx, strokeCalls } = fakeCtx2D();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);

    renderConnectionCanvas(
      {
        connections: [{ id: "conn1", source: "a", target: "b", orthogonal: false }],
        hoveredId: null,
        fadeEnabled: false,
        fadedOpacity: 0.15,
      },
      { nodes: { a: NODE_A, b: NODE_B } },
    );
    tickFrame();

    expect(strokeCalls).toEqual([{ strokeStyle: "#888888", lineWidth: 1.5, globalAlpha: 1 }]);
  });
});
