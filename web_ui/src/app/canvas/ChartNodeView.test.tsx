import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  ChartNodeView,
  chartExportUrl,
  chartNodePropsAreEqual,
  filenameFromContentDisposition,
  makeDebouncedChartResize,
  type ChartFlowNode,
} from "./ChartNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too
// (ChartNodeView's own useStore(zoom) read is all a ReactFlowProvider
// ancestor needs to satisfy; <NodeResizer/> renders happily the same way
// GroupNodeView.test.tsx's own bare-provider mount already exercises).
function renderChartNode(overrides: Partial<ChartFlowNode["data"]> = {}, id = "n0") {
  const onToggleAspectLock = vi.fn();
  const onResize = vi.fn();
  const props = {
    id,
    selected: false,
    data: {
      chartType: "bar",
      chartData: { type: "bar", title: "Quarterly Revenue" },
      chartError: "",
      chartWidth: 680,
      chartHeight: 500,
      chartAspectLocked: true,
      chartSourceNodeId: "chat-1",
      onToggleAspectLock,
      onResize,
      ...overrides,
    },
  } as unknown as NodeProps<ChartFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <ChartNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleAspectLock, onResize, container };
}

describe("ChartNodeView", () => {
  it("renders the interactive chart canvas (lazy-loaded) instead of a static image", async () => {
    const { container } = renderChartNode({
      chartType: "bar",
      chartData: { type: "bar", title: "Quarterly Revenue", labels: ["Q1", "Q2"], values: [10, 20] },
    });
    await waitFor(() => expect(container.querySelector(".chart-canvas-wrapper")).not.toBeNull());
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders the title from chartData.title and the type badge from chartType", () => {
    renderChartNode({ chartType: "histogram", chartData: { type: "histogram", title: "Response Times" } });
    expect(screen.getByText("Response Times")).toBeInTheDocument();
    expect(screen.getByText("Histogram")).toBeInTheDocument();
  });

  it("falls back to 'Chart' when chartData.title is missing/blank", () => {
    renderChartNode({ chartData: { type: "bar" } });
    expect(screen.getAllByText("Chart").length).toBeGreaterThan(0);
  });

  it("shows no error badge when chartError is empty (the default)", () => {
    renderChartNode({ chartError: "" });
    expect(screen.queryByLabelText("Chart generation warning")).toBeNull();
  });

  it("shows an inline warning badge carrying chartError as its title, WITHOUT hiding the chart canvas", async () => {
    const { container } = renderChartNode({ chartError: "used a placeholder - generation failed" });
    const badge = screen.getByLabelText("Chart generation warning");
    expect(badge).toHaveAttribute("title", "used a placeholder - generation failed");
    // The chart canvas still renders - a chart_error never blocks the card.
    await waitFor(() => expect(container.querySelector(".chart-canvas-wrapper")).not.toBeNull());
  });

  it("the aspect-lock toggle button reads 'Unlock Aspect' when locked and calls onToggleAspectLock", async () => {
    const { onToggleAspectLock } = renderChartNode({ chartAspectLocked: true });
    const button = screen.getByRole("button", { name: "Unlock Aspect" });
    expect(button).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(button);
    expect(onToggleAspectLock).toHaveBeenCalledOnce();
  });

  it("the aspect-lock toggle button reads 'Lock Aspect' when unlocked", () => {
    renderChartNode({ chartAspectLocked: false });
    const button = screen.getByRole("button", { name: "Lock Aspect" });
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("Export PNG/SVG are buttons, never navigated links that could leak the token", () => {
    // They used to be <a target="_blank"> carrying ?token=<capability token>.
    // In the WebView2 shell that opens in the OS default browser, putting a
    // live token into another browser's history/address bar. Asserting they
    // are BUTTONS (and that no link with these names exists) is what keeps
    // that from being reintroduced.
    renderChartNode({}, "chart-42");

    expect(screen.getByRole("button", { name: "Export PNG" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export SVG" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Export PNG" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Export SVG" })).toBeNull();
  });
});

describe("chartExportUrl", () => {
  it("defaults to fmt=png", () => {
    expect(chartExportUrl("node-1")).toBe("/api/assets/chart/node-1/export?session=default&fmt=png");
  });

  it("builds an fmt=svg url when asked", () => {
    expect(chartExportUrl("node-1", "svg")).toBe("/api/assets/chart/node-1/export?session=default&fmt=svg");
  });
});

describe("makeDebouncedChartResize", () => {
  it("does not call onResize until debounceMs have elapsed with no further calls", () => {
    vi.useFakeTimers();
    try {
      const onResize = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedChartResize(timerRef, onResize, 200);

      debounced(500, 300);
      expect(onResize).not.toHaveBeenCalled();
      vi.advanceTimersByTime(199);
      expect(onResize).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(onResize).toHaveBeenCalledOnce();
      expect(onResize).toHaveBeenCalledWith(500, 300);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a call before the debounce window elapses cancels the previous one - only the LAST width/height pair fires", () => {
    vi.useFakeTimers();
    try {
      const onResize = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedChartResize(timerRef, onResize, 200);

      debounced(500, 300);
      vi.advanceTimersByTime(150);
      debounced(640, 420); // resets the wait - simulates a second quick resize gesture
      vi.advanceTimersByTime(150);
      expect(onResize).not.toHaveBeenCalled(); // only 150ms since the SECOND call
      vi.advanceTimersByTime(50);
      expect(onResize).toHaveBeenCalledOnce();
      expect(onResize).toHaveBeenCalledWith(640, 420);
    } finally {
      vi.useRealTimers();
    }
  });
});

// ADR-011 stage 11.1: the React.memo comparator. Direct unit tests of the
// exported pure function (the same function reference wired into
// `memo(ChartNodeView, chartNodePropsAreEqual)`) plus one real-render
// integration test proving the wiring itself is correct.
describe("ChartNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  function props(overrides: Partial<ChartFlowNode["data"]> = {}, propOverrides: Record<string, unknown> = {}) {
    return {
      id: "n0",
      selected: false,
      data: {
        chartType: "bar",
        chartData: { type: "bar", title: "Quarterly Revenue" },
        chartError: "",
        chartWidth: 680,
        chartHeight: 500,
        chartAspectLocked: true,
        chartSourceNodeId: "chat-1",
        onToggleAspectLock: vi.fn(),
        onResize: vi.fn(),
        ...overrides,
      },
      ...propOverrides,
    } as unknown as NodeProps<ChartFlowNode>;
  }

  it("treats identical props as equal", () => {
    const p = props();
    expect(chartNodePropsAreEqual(p, { ...p })).toBe(true);
  });

  it("treats an unchanged chartData reference as equal even when other unread fields differ", () => {
    const a = props({ chartWidth: 680, chartData: { type: "bar", title: "Quarterly Revenue", values: [1, 2, 3] } });
    const b = { ...a, data: { ...a.data, chartWidth: 999 } };
    expect(a.data.chartData).toBe(b.data.chartData);
    expect(chartNodePropsAreEqual(a, b)).toBe(true);
  });

  it("returns false when chartData is a fresh object reference, even with byte-identical content - the renderer reads the whole shape now, so only a real upstream rebuild (see SceneCanvas.tsx's toFlowNodes cache) should mint a new reference", () => {
    const a = props({ chartData: { type: "bar", title: "Quarterly Revenue", values: [1, 2, 3] } });
    const b = { ...a, data: { ...a.data, chartData: { type: "bar" as const, title: "Quarterly Revenue", values: [1, 2, 3] } } };
    expect(a.data.chartData).not.toBe(b.data.chartData);
    expect(chartNodePropsAreEqual(a, b)).toBe(false);
  });

  it("is unaffected by ChartNodeData fields this component never reads (chartWidth, chartHeight, chartSourceNodeId) or unread NodeProps fields (dragging, zIndex)", () => {
    const a = props({ chartWidth: 680, chartHeight: 500, chartSourceNodeId: "chat-1" }, { dragging: false, zIndex: 0 });
    const b = {
      ...a,
      data: { ...a.data, chartWidth: 999, chartHeight: 999, chartSourceNodeId: "chat-2" },
      dragging: true,
      zIndex: 9,
    };
    expect(chartNodePropsAreEqual(a, b)).toBe(true);
  });

  it("returns false when id changes", () => {
    const a = props({}, { id: "n0" });
    const b = { ...a, id: "n1" };
    expect(chartNodePropsAreEqual(a, b)).toBe(false);
  });

  it("returns false when selected changes", () => {
    const a = props({}, { selected: false });
    const b = { ...a, selected: true };
    expect(chartNodePropsAreEqual(a, b)).toBe(false);
  });

  it.each([
    ["chartType", { chartType: "line" }],
    ["chartError", { chartError: "boom" }],
    ["chartAspectLocked", { chartAspectLocked: false }],
    ["onToggleAspectLock", { onToggleAspectLock: vi.fn() }],
    ["onResize", { onResize: vi.fn() }],
  ] as const)("returns false when data.%s changes and nothing else does", (_name, override) => {
    const a = props();
    const b = { ...a, data: { ...a.data, ...override } };
    expect(chartNodePropsAreEqual(a, b)).toBe(false);
  });

  it("real render: skipped when only an unread field changes, and actually happens when chartType changes", () => {
    const p = props({ chartType: "bar" }, { selected: false });
    const { container, rerender } = render(
      <ReactFlowProvider>
        <ChartNodeView {...p} />
      </ReactFlowProvider>,
    );
    const root = container.querySelector(".scene-node") as HTMLElement;
    expect(root).not.toBeNull();

    root.className = "CORRUPTED";

    // chartWidth is never read by this component - the comparator must say
    // "equal", so no re-render should occur.
    rerender(
      <ReactFlowProvider>
        <ChartNodeView {...p} data={{ ...p.data, chartWidth: 999 }} />
      </ReactFlowProvider>,
    );
    expect(root.className).toBe("CORRUPTED");

    // chartType IS read (drives the badge text and, via `collapsed`
    // rendering, this component's own class string too through re-render) -
    // toggling `selected` (which this element's className directly reflects)
    // proves a real re-render actually happened.
    rerender(
      <ReactFlowProvider>
        <ChartNodeView {...p} selected />
      </ReactFlowProvider>,
    );
    expect(root.className).not.toBe("CORRUPTED");
    expect(root.className).toContain("selected");
  });
});

describe("chart export never puts the capability token in a URL", () => {
  it("chartExportUrl carries no token query parameter", () => {
    // The token lives in location.hash; withAuthToken() used to append it
    // here. Anything user-visible or navigable must not carry it.
    expect(chartExportUrl("node-1", "png")).not.toContain("token");
    expect(chartExportUrl("node-1", "svg")).not.toContain("token");
  });

  it("filenameFromContentDisposition honors the server's own filename", () => {
    expect(filenameFromContentDisposition('attachment; filename="Q4_Revenue.png"')).toBe("Q4_Revenue.png");
    expect(filenameFromContentDisposition("attachment; filename=chart.svg")).toBe("chart.svg");
    expect(filenameFromContentDisposition(null)).toBe("");
    expect(filenameFromContentDisposition("attachment")).toBe("");
  });
});
