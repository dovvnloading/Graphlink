import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChartRenderer from "./ChartRenderer";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";

// ChartRenderer gates its own SVG on a real measured size (useElementSize,
// see chartHooks.ts). vitest.setup.ts's global ResizeObserver stub is a
// deliberate no-op (firing it for real breaks @xyflow/react's own internal
// ResizeObserver usage under jsdom - see that file's own comment) so this
// file installs its own local, firing stub for the duration of these tests
// only: real ResizeObserver always delivers one synchronous-ish initial
// observation, which is all useElementSize needs to pick up a size.
const originalResizeObserver = globalThis.ResizeObserver;

beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: 600,
    height: 400,
    top: 0,
    left: 0,
    right: 600,
    bottom: 400,
    x: 0,
    y: 0,
    toJSON() {
      return {};
    },
  });

  class FiringResizeObserverStub {
    #callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
      this.#callback = callback;
    }
    observe(target: Element) {
      const entry = { target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry;
      this.#callback([entry], this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = FiringResizeObserverStub as unknown as typeof ResizeObserver;
});

afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver;
  vi.restoreAllMocks();
});

const SAMPLE_DATA: Record<string, ChartDataRow> = {
  bar: { type: "bar", title: "Sales", labels: ["Q1", "Q2", "Q3"], values: [10, -4, 20] },
  line: { type: "line", title: "Trend", labels: ["Mon", "Tue", "Wed"], values: [1, 5, 3] },
  pie: { type: "pie", title: "Share", labels: ["A", "B", "C"], values: [30, 20, 50] },
  histogram: { type: "histogram", title: "Distribution", values: [1, 2, 2, 3, 4, 5, 5, 5, 6], bins: 4 },
  scatter: {
    type: "scatter", title: "Calibration", xValues: [1, 10, 100],
    xScale: "log10", yScale: "linear", xUnit: "mg/L", yUnit: "mV",
    series: [{ name: "Control", values: [1, 2, 3],
      xError: [[0.05, 0.05], [1, 2], [10, 10]],
      yError: [[0.1, 0.1], [0.1, 0.3], [0.2, 0.2]] }],
  },
  boxplot: {
    type: "boxplot", title: "Measurements",
    groups: [
      { label: "Control", values: [1, 2, 2, 3, 3, 4, 100] },
      { label: "Treatment", values: [2, 3, 4, 5] },
    ],
  },
  heatmap: {
    type: "heatmap", title: "Assay", rowLabels: ["A", "B"],
    columnLabels: ["X", "Y"], matrix: [[-2, 0], [1, 5]], valueUnit: "AU",
  },
  sankey: {
    type: "sankey",
    title: "Flow",
    flows: [
      { source: "A", target: "B", value: 3 },
      { source: "A", target: "C", value: 7 },
    ],
  },
};

describe("ChartRenderer", () => {
  it.each(Object.keys(SAMPLE_DATA))("renders %s chart data as SVG marks without crashing", (chartType) => {
    const { container } = render(<ChartRenderer chartType={chartType} chartData={SAMPLE_DATA[chartType]} />);
    const svg = container.querySelector(".chart-canvas-svg");
    expect(svg).not.toBeNull();
    const marks = svg!.querySelectorAll("rect, path, circle");
    expect(marks.length).toBeGreaterThan(0);
  });

  it.each(Object.keys(SAMPLE_DATA))("renders a saved %s chart with no nested type using the same SVG geometry", (chartType) => {
    const typed = SAMPLE_DATA[chartType];
    const signature = (container: HTMLElement) => {
      const svg = container.querySelector(".chart-canvas-svg");
      expect(svg).not.toBeNull();
      return Array.from(svg!.querySelectorAll("rect, path, circle, line, polyline")).map((mark) =>
        ["class", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "width", "height", "d", "points", "fill", "stroke"]
          .map((attribute) => {
            const value = mark.getAttribute(attribute) ?? "";
            return attribute === "fill" && value.startsWith("url(#") ? "gradient" : value;
          }),
      );
    };
    const current = render(<ChartRenderer chartType={chartType} chartData={typed} />);
    const expected = signature(current.container);
    expect(expected.length).toBeGreaterThan(0);
    current.unmount();
    const saved = { ...typed };
    delete saved.type;
    const legacy = render(<ChartRenderer chartType={chartType} chartData={saved} />);
    expect(legacy.container.querySelector(".chart-canvas-placeholder")).toBeNull();
    expect(signature(legacy.container)).toEqual(expected);
  });

  it("shows the inline placeholder (not a crash) for chart data that fails re-validation", () => {
    const { container } = render(<ChartRenderer chartType="bar" chartData={{ type: "bar" }} />);
    expect(container.querySelector(".chart-canvas-placeholder")).not.toBeNull();
    expect(container.querySelector(".chart-canvas-svg")).toBeNull();
    expect(screen.getByText(/both be lists/)).toBeInTheDocument();
  });

  it("renders equal enormous pie values with accurate shares and a bounded total label", () => {
    const { container } = render(<ChartRenderer chartType="pie"
      chartData={{ type: "pie", labels: ["A", "B"], values: [1e308, 1e308] }} />);
    const svg = container.querySelector(".chart-canvas-svg")!;
    expect(svg).not.toBeNull();
    expect(svg.textContent?.match(/50\.0%/g)).toHaveLength(2);
    expect(svg.textContent).toContain(">1.79e308");
    const paths = Array.from(svg.querySelectorAll("path"));
    expect(paths).toHaveLength(2);
    expect(paths[0].getAttribute("d")).not.toBe(paths[1].getAttribute("d"));
    expect(paths.every(path => !/NaN|Infinity/.test(path.getAttribute("d") ?? ""))).toBe(true);
    fireEvent.pointerMove(paths[0], { clientX: 50, clientY: 50 });
    expect(screen.getByRole("tooltip")).toHaveTextContent("1.00e+308");
    expect(screen.getByRole("tooltip")).toHaveTextContent("50.0%");
  });

  it("renders an overflowing Other bucket with a correct tooltip share", () => {
    const { container } = render(<ChartRenderer chartType="pie" chartData={{
      type: "pie", labels: Array.from({ length: 12 }, (_, index) => "L" + index),
      values: Array<number>(12).fill(1e308),
    }} />);
    const svg = container.querySelector(".chart-canvas-svg")!;
    expect(svg.textContent).toContain("Other  25.0%");
    const paths = Array.from(svg.querySelectorAll("path"));
    expect(paths).toHaveLength(10);
    fireEvent.pointerMove(paths[9], { clientX: 50, clientY: 50 });
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Other");
    expect(tooltip).toHaveTextContent(">1.79e308");
    expect(tooltip).toHaveTextContent("25.0%");
  });

  it("shows a tooltip on hovering a bar and hides it again on pointer leave", () => {
    const { container } = render(<ChartRenderer chartType="bar" chartData={SAMPLE_DATA.bar} />);
    const bar = container.querySelector(".chart-canvas-svg rect")!;

    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.pointerMove(bar, { clientX: 50, clientY: 50 });
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    fireEvent.pointerLeave(bar);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows full Tukey statistics and outliers on boxplot hover", () => {
    const { container } = render(<ChartRenderer chartType="boxplot" chartData={SAMPLE_DATA.boxplot} />);
    expect(container.querySelectorAll(".chart-boxplot-group")).toHaveLength(2);
    expect(container.querySelectorAll(".chart-boxplot-outlier")).toHaveLength(1);
    const hit = container.querySelector(".chart-boxplot-hit-target")!;
    fireEvent.pointerMove(hit, { clientX: 100, clientY: 100 });
    expect(screen.getByRole("tooltip")).toHaveTextContent("Median");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Outliers");
    fireEvent.pointerLeave(hit);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows original matrix values and labels on heatmap hover", () => {
    const { container } = render(<ChartRenderer chartType="heatmap" chartData={SAMPLE_DATA.heatmap} />);
    const cells = container.querySelectorAll(".chart-heatmap-cell");
    expect(cells).toHaveLength(4);
    expect(cells[0]).toHaveAttribute("fill", "#2166ac");
    const last = cells[3];
    fireEvent.pointerMove(last, { clientX: 100, clientY: 100 });
    expect(screen.getByRole("tooltip")).toHaveTextContent("5 AU");
    expect(screen.getByRole("tooltip")).toHaveTextContent("B");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Y");
    fireEvent.pointerLeave(last);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("zooms in on wheel-up and shows a reset control, which restores the original transform", () => {
    const { container } = render(<ChartRenderer chartType="bar" chartData={SAMPLE_DATA.bar} />);
    const svg = container.querySelector(".chart-canvas-svg")!;
    const zoomGroup = svg.querySelector("g")!;
    expect(zoomGroup.getAttribute("transform")).toBe("translate(0 0) scale(1)");

    const resetButton = screen.getByRole("button", { name: "Reset zoom" });
    expect(resetButton).toBeDisabled();
    fireEvent.wheel(svg, { deltaY: -100, clientX: 100, clientY: 100 });
    expect(zoomGroup.getAttribute("transform")).toBe("translate(0 0) scale(1)");

    fireEvent.focus(svg);
    expect(container.querySelector(".chart-canvas-wrapper")).toHaveAttribute("data-interactive", "true");
    fireEvent.wheel(svg, { deltaY: -100, clientX: 100, clientY: 100 });
    expect(zoomGroup.getAttribute("transform")).not.toBe("translate(0 0) scale(1)");
    expect(resetButton).toBeEnabled();
    fireEvent.click(resetButton);
    expect(zoomGroup.getAttribute("transform")).toBe("translate(0 0) scale(1)");
    expect(resetButton).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(container.querySelector(".chart-canvas-wrapper")).toHaveAttribute("data-interactive", "false");
    fireEvent.wheel(svg, { deltaY: -100, clientX: 100, clientY: 100, ctrlKey: true });
    expect(container.querySelector(".chart-canvas-wrapper")).toHaveAttribute("data-interactive", "true");
  });
});

describe("legacy chart numeric ranges", () => {
  it.each(["bar", "line", "histogram"] as const)("%s renders tiny values with finite SVG geometry", (kind) => {
    for (const values of [[1e-120, 2e-120], [1e-120, 1e-120], [-1e-120, 1e-120]]) {
      const chartData: ChartDataRow = { type: kind, labels: ["A", "B"], values, bins: 2 };
      const { container, unmount } = render(<ChartRenderer chartType={kind} chartData={chartData} />);
      const svg = container.querySelector(".chart-canvas-svg");
      expect(svg).not.toBeNull();
      expect(svg!.querySelectorAll("rect, circle, path").length).toBeGreaterThan(0);
      expect(svg!.outerHTML).not.toMatch(/NaN|Infinity/);
      expect(svg!.textContent).toMatch(/e-12[0-9]/);
      unmount();
    }
  });

  it.each(["bar", "line"] as const)("%s moves its axis title outside longer scientific ticks", (kind) => {
    const chartData: ChartDataRow = { type: kind, labels: ["A", "B"], values: [1e-120, 2e-120], yAxis: "Measurement" };
    const { container } = render(<ChartRenderer chartType={kind} chartData={chartData} />);
    const title = Array.from(container.querySelectorAll(".chart-axes text")).find((element) => element.textContent === "Measurement");
    expect(title).toBeDefined();
    expect(Number(title!.getAttribute("x"))).toBeLessThan(-44);
  });

  it("anchors exact histogram endpoint labels inside the plot", () => {
    const chartData: ChartDataRow = { type: "histogram", values: [1e-120, 2e-120], bins: 2 };
    const { container } = render(<ChartRenderer chartType="histogram" chartData={chartData} />);
    const ticks = Array.from(container.querySelectorAll(".chart-axes text")).filter((element) => element.querySelector("tspan"));
    expect(ticks.length).toBeGreaterThan(1);
    expect(ticks[0]).toHaveTextContent("1e-120");
    expect(ticks[0]).toHaveAttribute("text-anchor", "start");
    expect(ticks[ticks.length - 1]).toHaveTextContent("2e-120");
    expect(ticks[ticks.length - 1]).toHaveAttribute("text-anchor", "end");
  });

  it.each(["bar", "line", "histogram"] as const)("%s rejects unsupported extreme data with a useful error", (kind) => {
    for (const values of [[Number.MIN_VALUE, 2 * Number.MIN_VALUE],
      [1e308, 1.0000000000000002e308], [-1e308, 1e308]]) {
      const chartData: ChartDataRow = { type: kind, labels: ["A", "B"], values, bins: 2 };
      const { container, unmount } = render(<ChartRenderer chartType={kind} chartData={chartData} />);
      expect(container.querySelector(".chart-canvas-svg")).toBeNull();
      expect(container.querySelector(".chart-canvas-placeholder")?.textContent).toMatch(/rescale the data/);
      unmount();
    }
  });
});
