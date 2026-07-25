import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChartNodeView, chartAssetUrl, chartExportUrl, makeDebouncedChartResize, type ChartFlowNode } from "./ChartNodeView";

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
      chartAssetId: "asset-chart-1",
      chartAssetVersion: 1,
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
  it("renders the img with the correct cache-busting src (asset endpoint + ?v=chartAssetVersion)", () => {
    const { container } = renderChartNode({ chartAssetId: "asset-chart-1", chartAssetVersion: 3 });
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "/api/assets/asset-chart-1?v=3");
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

  it("shows an inline warning badge carrying chartError as its title, WITHOUT hiding the chart image", () => {
    const { container } = renderChartNode({ chartError: "used a placeholder - generation failed" });
    const badge = screen.getByLabelText("Chart generation warning");
    expect(badge).toHaveAttribute("title", "used a placeholder - generation failed");
    // The chart image still renders - a chart_error never blocks the card.
    expect(container.querySelector("img")).not.toBeNull();
  });

  it("onError shows the 'Chart unavailable' placeholder and hides the broken img", () => {
    const { container } = renderChartNode();
    const img = container.querySelector("img");
    expect(img).not.toBeNull();

    fireEvent.error(img!);

    expect(screen.getByText("Chart unavailable")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
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

  it("the Export link points at the dedicated export endpoint for this node's id", () => {
    renderChartNode({}, "chart-42");
    const link = screen.getByRole("link", { name: "Export" });
    expect(link).toHaveAttribute("href", "/api/assets/chart/chart-42/export?session=default");
    expect(link).toHaveAttribute("target", "_blank");
  });
});

describe("chartAssetUrl / chartExportUrl", () => {
  it("chartAssetUrl builds /api/assets/{id}?v={version}", () => {
    expect(chartAssetUrl("abc", 7)).toBe("/api/assets/abc?v=7");
  });

  it("chartExportUrl builds /api/assets/chart/{nodeId}/export?session=default", () => {
    expect(chartExportUrl("node-1")).toBe("/api/assets/chart/node-1/export?session=default");
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
