import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadPublicationChartExport } from "./charts/publicationExport";

vi.mock("./charts/publicationExport", () => ({
  downloadPublicationChartExport: vi.fn().mockResolvedValue(undefined),
}));
import { chartExportUrl, downloadChartExport, filenameFromContentDisposition } from "./charts/legacyExport";
import { ChartNodeView, chartNodePropsAreEqual, makeDebouncedChartResize, type ChartFlowNode } from "./ChartNodeView";

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

  it("uses one authenticated export button instead of token-bearing links", () => {
    renderChartNode({}, "chart-42");
    expect(screen.getByRole("button", { name: "Publication export…" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Export (PNG|SVG)/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Export PNG" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Export SVG" })).toBeNull();

  });
});

describe("chart publication export controls", () => {
  afterEach(() => vi.mocked(downloadPublicationChartExport).mockClear());

  it("offers vector PDF/SVG, physical journal widths, both DPIs and accessible monochrome options", () => {
    renderChartNode({}, "chart-export");
    fireEvent.click(screen.getByRole("button", { name: "Publication export…" }));
    expect(screen.getByRole("group", { name: "Publication export settings" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "PDF (vector)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "SVG (vector)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Single column (85 mm)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Double column (180 mm)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "600" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Colorblind-safe palette/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Grayscale-safe/ })).toBeInTheDocument();
  });

  it("submits settings, custom physical size and uses the saved chart aspect for presets", async () => {
    renderChartNode({ chartWidth: 680, chartHeight: 340 }, "chart-export");
    fireEvent.click(screen.getByRole("button", { name: "Publication export…" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Publication format" }), { target: { value: "pdf" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Publication size" }), { target: { value: "custom" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Custom width in millimeters" }), { target: { value: "120" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Custom height in millimeters" }), { target: { value: "95" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Publication DPI" }), { target: { value: "600" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Colorblind-safe palette/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Grayscale-safe/ }));
    fireEvent.click(screen.getByRole("button", { name: "Download PDF" }));
    await waitFor(() => expect(downloadPublicationChartExport).toHaveBeenCalledWith(
      "chart-export",
      {
        format: "pdf", size: "custom", widthMm: 120, heightMm: 95,
        dpi: 600, colorblindSafe: true, grayscaleSafe: true,
      },
      0.5,
    ));
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

  it("treats an unchanged chartData reference as equal even when unrelated source ID differs", () => {
    const a = props({ chartWidth: 680, chartData: { type: "bar", title: "Quarterly Revenue", values: [1, 2, 3] } });
    const b = { ...a, data: { ...a.data, chartSourceNodeId: "other-source" } };
    expect(a.data.chartData).toBe(b.data.chartData);
    expect(chartNodePropsAreEqual(a, b)).toBe(true);
  });

  it("returns false when chartData is a fresh object reference, even with byte-identical content - the renderer reads the whole shape now, so only a real upstream rebuild (see SceneCanvas.tsx's toFlowNodes cache) should mint a new reference", () => {
    const a = props({ chartData: { type: "bar", title: "Quarterly Revenue", values: [1, 2, 3] } });
    const b = { ...a, data: { ...a.data, chartData: { type: "bar" as const, title: "Quarterly Revenue", values: [1, 2, 3] } } };
    expect(a.data.chartData).not.toBe(b.data.chartData);
    expect(chartNodePropsAreEqual(a, b)).toBe(false);
  });

  it("is unaffected by the unread source ID and NodeProps fields (dragging, zIndex)", () => {
    const a = props({ chartWidth: 680, chartHeight: 500, chartSourceNodeId: "chat-1" }, { dragging: false, zIndex: 0 });
    const b = {
      ...a,
      data: { ...a.data, chartSourceNodeId: "chat-2" },
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
    ["chartWidth", { chartWidth: 999 }],
    ["chartHeight", { chartHeight: 999 }],
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

    // The source ID is still unread and should not trigger a re-render.
    rerender(
      <ReactFlowProvider>
        <ChartNodeView {...p} data={{ ...p.data, chartSourceNodeId: "other-source" }} />
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

describe("downloadChartExport failure handling", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // REVIEW-FIX: both toolbar buttons call this fire-and-forget
  // (`void downloadChartExport(...)`), so a rejected promise used to
  // escape as an unhandled rejection instead of anything the user or a
  // test could observe cleanly. It must now resolve (not reject) and log,
  // mirroring ImageNodeView's "does not throw when the clipboard write
  // fails" test for the identical export-button failure mode.
  it("does not throw when the export fetch responds non-OK", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 } as Response));
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(downloadChartExport("node-1", "png")).resolves.toBeUndefined();

    expect(consoleError).toHaveBeenCalled();
  });

  it("does not throw when the export fetch itself rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(downloadChartExport("node-1", "svg")).resolves.toBeUndefined();

    expect(consoleError).toHaveBeenCalled();
  });
});
