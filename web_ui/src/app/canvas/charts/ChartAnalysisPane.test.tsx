import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChartRenderer from "./ChartRenderer";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import type { ChartAnalysis } from "../chartSpec";

// Same measured-size contract as ChartRenderer.test: react-svg draws nothing
// until the ResizeObserver reports a real content rectangle.
const originalResizeObserver = globalThis.ResizeObserver;
beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: 600, height: 400, top: 0, left: 0, right: 600, bottom: 400,
    x: 0, y: 0, toJSON: () => ({}),
  });
  class Observer {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry],
        this as unknown as ResizeObserver);
    }
    disconnect() {}
    unobserve() {}
  }
  globalThis.ResizeObserver = Observer as unknown as typeof ResizeObserver;
});
afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver;
  vi.restoreAllMocks();
});

const scatter: ChartDataRow = {
  type: "scatter", title: "Calibration", xAxis: "Concentration", yAxis: "Response",
  xValues: [1, 10, 100], values: [2, 4, 8],
  xScale: "log10", yScale: "linear", xUnit: "mg/L", yUnit: "mV",
};
const plotData: Record<string, ChartDataRow> = {
  line: { type: "line", title: "Trend", labels: ["a", "b", "c"], values: [2, 3, 4] },
  scatter,
  histogram: { type: "histogram", title: "Histogram", values: [1, 2, 2, 3, 5], bins: 3 },
  boxplot: { type: "boxplot", title: "Boxplot", groups: [
    { label: "Control", values: [1, 2, 3, 4] },
    { label: "Drug", values: [2, 3, 4, 5] },
  ] },
};

function svgFor(container: HTMLElement): SVGSVGElement {
  const svg = container.querySelector(".chart-canvas-svg");
  expect(svg).not.toBeNull();
  return svg as SVGSVGElement;
}
function openTools() {
  fireEvent.click(screen.getByRole("button", { name: "Chart tools" }));
}

describe("analysis interactions on numerical charts", () => {
  it.each(["line", "scatter", "histogram", "boxplot"])(
    "%s starts with unchanged marks and offers keyboard/mouse tools", (type) => {
      const original = JSON.stringify(plotData[type]);
      const { container } = render(<ChartRenderer chartType={type} chartData={plotData[type]} />);
      expect(svgFor(container).querySelector("g[transform*='scale(']")).toBeNull();
      expect(svgFor(container).querySelectorAll("rect, path, circle").length).toBeGreaterThan(0);
      expect(screen.getByRole("button", { name: "Chart tools" })).toBeInTheDocument();
      expect(JSON.stringify(plotData[type])).toBe(original);
    },
  );

  it("reads out mouse cursor x/y with unrounded numbers and physical units", () => {
    const { container } = render(<ChartRenderer chartType="scatter" chartData={scatter} />);
    fireEvent.pointerMove(svgFor(container), { pointerId: 1, clientX: 260, clientY: 200 });
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("X:");
    expect(status).toHaveTextContent("Y:");
    expect(status).toHaveTextContent("mg/L");
    expect(status).toHaveTextContent("mV");
    expect(status).not.toHaveTextContent("NaN");
    expect(container.querySelectorAll(".chart-analysis-crosshair")).toHaveLength(2);
  });

  it("box zoom changes data-space marks (not SVG scale), pans, and resets with one click", () => {
    const { container } = render(<ChartRenderer chartType="scatter" chartData={scatter} />);
    const svg = svgFor(container);
    const initialX = svg.querySelector(".chart-scientific-point circle")?.getAttribute("cx");
    openTools();
    fireEvent.click(screen.getByRole("button", { name: "Box zoom" }));
    fireEvent.pointerDown(svg, { button: 0, pointerId: 1, clientX: 100, clientY: 80 });
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 450, clientY: 300 });
    expect(svg.querySelector(".chart-analysis-annotations rect")).not.toBeNull();
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 450, clientY: 300 });
    expect(svg.querySelector(".chart-analysis-annotations rect")).toBeNull();
    expect(svg.querySelector(".chart-scientific-point circle")?.getAttribute("cx")).not.toBe(initialX);
    expect(screen.getByRole("button", { name: "Reset view" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(svg.querySelector(".chart-scientific-point circle")?.getAttribute("cx")).toBe(initialX);
    expect(screen.queryByRole("button", { name: "Reset view" })).toBeNull();
  });

  it("supports keyboard pan, zoom, reset, and cursor movement without a mouse", () => {
    const { container } = render(<ChartRenderer chartType="scatter" chartData={scatter} />);
    const svg = svgFor(container);
    fireEvent.keyDown(svg, { key: "=" });
    expect(screen.getByRole("button", { name: "Reset view" })).toBeInTheDocument();
    fireEvent.keyDown(svg, { key: "ArrowRight" });
    fireEvent.keyDown(svg, { key: "ArrowUp", shiftKey: true });
    expect(screen.getByRole("status")).toHaveTextContent("mg/L");
    fireEvent.keyDown(svg, { key: "Home" });
    expect(screen.queryByRole("button", { name: "Reset view" })).toBeNull();
  });

  it("saves a label, an arrow and shaded x/y regions using keyboard-accessible forms", async () => {
    const saved: ChartAnalysis[] = [];
    const onAnalysisChange = vi.fn(async (analysis: ChartAnalysis) => { saved.push(analysis); });
    const { container, rerender } = render(<ChartRenderer chartType="scatter" chartData={scatter}
      onAnalysisChange={onAnalysisChange} />);
    openTools();
    fireEvent.click(screen.getByRole("button", { name: "Text label" }));
    fireEvent.change(screen.getByLabelText("Annotation text"), { target: { value: "Exact threshold" } });
    fireEvent.click(screen.getByRole("button", { name: "Add annotation" }));
    await waitFor(() => expect(onAnalysisChange).toHaveBeenCalledTimes(1));
    expect(saved[0].annotations[0]).toMatchObject({ kind: "label", text: "Exact threshold" });
    expect(container.querySelector(".chart-analysis-label")).toHaveTextContent("Exact threshold");

    fireEvent.click(screen.getByRole("button", { name: "Arrow" }));
    fireEvent.click(screen.getByRole("button", { name: "Add annotation" }));
    fireEvent.click(screen.getByRole("button", { name: "Shade X" }));
    fireEvent.click(screen.getByRole("button", { name: "Add annotation" }));
    fireEvent.click(screen.getByRole("button", { name: "Shade Y" }));
    fireEvent.click(screen.getByRole("button", { name: "Add annotation" }));
    await waitFor(() => expect(onAnalysisChange).toHaveBeenCalledTimes(4));
    expect(saved[3].annotations.map((item) => item.kind)).toEqual(["label", "arrow", "region", "region"]);
    expect(saved[3].annotations.slice(2).map((item) => item.kind === "region" && item.axis))
      .toEqual(["x", "y"]);

    // A server scene snapshot/reload can rehydrate the saved metadata; undo
    // publishes an older snapshot with no overlay, which removes the artists.
    const edited = { ...scatter, analysis: saved[3] };
    rerender(<ChartRenderer chartType="scatter" chartData={edited} onAnalysisChange={onAnalysisChange} />);
    expect(container.querySelectorAll(".chart-analysis-region")).toHaveLength(2);
    rerender(<ChartRenderer chartType="scatter" chartData={{ ...scatter }}
      onAnalysisChange={onAnalysisChange} />);
    expect(container.querySelectorAll(".chart-analysis-region")).toHaveLength(0);
  });

  it("locks the exact current axes and permits precise ranges by keyboard", async () => {
    const onAnalysisChange = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ChartRenderer chartType="scatter" chartData={scatter}
      onAnalysisChange={onAnalysisChange} />);
    openTools();
    fireEvent.change(screen.getByLabelText("X minimum"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("X maximum"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Y minimum"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Y maximum"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply ranges" }));
    fireEvent.click(screen.getByRole("button", { name: "Lock X range" }));
    await waitFor(() => expect(onAnalysisChange).toHaveBeenCalledTimes(1));
    const locked = onAnalysisChange.mock.calls[0][0] as ChartAnalysis;
    expect(locked.lockX).toBe(true);
    expect(locked.xRange).toEqual([1, 10]);
    const before = svgFor(container).querySelector(".chart-scientific-point circle")?.getAttribute("cx");
    fireEvent.wheel(svgFor(container), { deltaY: -100, clientX: 270, clientY: 150 });
    expect(svgFor(container).querySelector(".chart-scientific-point circle")?.getAttribute("cx")).toBe(before);
  });
});
