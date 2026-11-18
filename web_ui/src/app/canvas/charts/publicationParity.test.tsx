/** Cross-renderer publication contract.
 *
 * backend/tests/test_chart_publication_export.py loads the *same* fixture
 * and inspects Matplotlib's SVG/PDF/PNG geometry + text. Assertions here pin
 * the interactive DOM to the identical raw points, error intervals, Tukey
 * outliers, matrix cells, axes, units and legends.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChartRenderer from "./ChartRenderer";
import specs from "./publicationParityFixtures.json";

const originalResizeObserver = globalThis.ResizeObserver;

beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: 640, height: 440, top: 0, left: 0, right: 640, bottom: 440,
    x: 0, y: 0, toJSON: () => ({}),
  });
  class ResizeStub {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry],
        this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeStub as unknown as typeof ResizeObserver;
});

afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver;
  vi.restoreAllMocks();
});

function svgText(node: Element): string {
  return [...node.querySelectorAll("text")].map((text) => text.textContent ?? "").join(" ");
}

describe("the same publication geometry and labels as server exports", () => {
  it.each(Object.entries(specs))("%s draws the shared fixture as interactive vector marks", (kind, data) => {
    const { container } = render(<ChartRenderer chartType={kind} chartData={data as never} />);
    const svg = container.querySelector(".chart-canvas-svg") as SVGSVGElement;
    expect(svg).not.toBeNull();
    expect(svg.querySelectorAll("path, circle, rect").length).toBeGreaterThan(0);

    const spec = data as { xAxis?: string; yAxis?: string };
    if (spec.xAxis) expect(svgText(svg)).toContain(spec.xAxis);
    if (spec.yAxis) expect(svgText(svg)).toContain(spec.yAxis);
  });

  it("matches scatter series centers, asymmetric error intervals, units and legend", () => {
    const { container } = render(<ChartRenderer chartType="scatter" chartData={specs.scatter as never} />);
    const svg = container.querySelector(".chart-canvas-svg")!;
    const series = svg.querySelectorAll(".chart-scientific-series");
    expect(series).toHaveLength(2);
    expect(series[0].querySelectorAll(".chart-series-marker")).toHaveLength(3);
    expect(series[0].querySelectorAll(".chart-x-error")).toHaveLength(3);
    expect(series[0].querySelectorAll(".chart-y-error")).toHaveLength(3);
    expect(svgText(svg)).toContain("Concentration (mg/L)");
    expect(svgText(svg)).toContain("Signal (mV)");
    expect(svgText(svg)).toContain("Control");
    expect(svgText(svg)).toContain("Treatment");

    const dots = series[0].querySelectorAll(".chart-series-marker");
    const x = [...dots].map((marker) => Number(marker.getAttribute("cx")));
    // Log10 positions [1, 10, 100]: successive decades must be equidistant.
    expect(x[1] - x[0]).toBeCloseTo(x[2] - x[1], 6);
    fireEvent.pointerMove(dots[1], { clientX: 100, clientY: 100 });
    expect(screen.getByRole("tooltip")).toHaveTextContent("10 mg/L");
    expect(screen.getByRole("tooltip")).toHaveTextContent("4 mV");
    expect(screen.getByRole("tooltip")).toHaveTextContent("-1/+2 mg/L");
    expect(screen.getByRole("tooltip")).toHaveTextContent("-0.2/+0.5 mV");
  });

  it("matches the boxplot's Tukey median, outlier and group label", () => {
    const { container } = render(<ChartRenderer chartType="boxplot" chartData={specs.boxplot as never} />);
    const group = container.querySelector('.chart-boxplot-group[data-label="Control"]')!;
    expect(group).not.toBeNull();
    expect(group.querySelectorAll(".chart-boxplot-box")).toHaveLength(1);
    expect(group.querySelectorAll(".chart-boxplot-median")).toHaveLength(1);
    expect(group.querySelectorAll(".chart-boxplot-outlier")).toHaveLength(1);
    fireEvent.pointerMove(group.querySelector(".chart-boxplot-hit-target")!, { clientX: 120, clientY: 100 });
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("Median");
    expect(tooltip).toHaveTextContent("3");
    expect(tooltip).toHaveTextContent("2 / 4");
    const outlier = group.querySelector(".chart-boxplot-outlier")!;
    const median = group.querySelector(".chart-boxplot-median")!;
    expect(Number(outlier.getAttribute("cy"))).toBeLessThan(Number(median.getAttribute("y1")));
  });

  it("matches every heatmap matrix value, row/column order, units and colorbar", () => {
    const { container } = render(<ChartRenderer chartType="heatmap" chartData={specs.heatmap as never} />);
    const svg = container.querySelector(".chart-canvas-svg")!;
    const cells = svg.querySelectorAll(".chart-heatmap-cell");
    expect(cells).toHaveLength(4);
    expect([...cells].map((cell) => cell.getAttribute("aria-label"))).toEqual([
      "Control, 0h: -2", "Control, 24h: 0",
      "Treatment, 0h: 2", "Treatment, 24h: 4",
    ]);
    const x = [...cells].map((cell) => Number(cell.getAttribute("x")));
    const y = [...cells].map((cell) => Number(cell.getAttribute("y")));
    expect(x[0]).toBe(x[2]);
    expect(x[1]).toBe(x[3]);
    expect(y[0]).toBe(y[1]);
    expect(y[2]).toBe(y[3]);
    expect(x[1]).toBeGreaterThan(x[0]);
    expect(y[2]).toBeGreaterThan(y[0]);
    expect(svg.querySelector(".chart-heatmap-legend")).not.toBeNull();
    expect(svgText(svg)).toContain("AU");
  });

  it("preserves the original categorical, histogram, pie and Sankey labels", () => {
    for (const kind of ["bar", "line", "pie", "histogram", "sankey"] as const) {
      const { container, unmount } = render(
        <ChartRenderer chartType={kind} chartData={specs[kind] as never} />,
      );
      const svg = container.querySelector(".chart-canvas-svg")!;
      const text = svgText(svg as SVGSVGElement);
      if (kind === "bar" || kind === "line" || kind === "pie") {
        expect(text).toContain("A");
        expect(text).toContain("B");
      }
      if (kind === "histogram") {
        expect(text).toContain("Mean");
        expect(text).toContain("Median");
      }
      if (kind === "sankey") {
        expect(text).toContain("Input");
        expect(text).toContain("Output");
      }
      unmount();
    }
  });
});
