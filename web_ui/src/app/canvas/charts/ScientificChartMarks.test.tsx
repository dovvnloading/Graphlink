import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canonicalizeChartSpec, scientificFields } from "../chartSpec";
import { createLog10Scale, formatLogTick, log10Ticks, scientificDomain } from "./chartScales";
import ChartRenderer from "./ChartRenderer";

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

const SCATTER = {
  type: "scatter",
  title: "Calibration",
  xValues: [1, 10, 100],
  xScale: "log10",
  yScale: "linear",
  xAxis: "Concentration",
  yAxis: "Response",
  xUnit: "mg/L",
  yUnit: "mV",
  series: [
    { name: "Control", values: [0.9, 1.8, 3.2],
      xError: [0.05, [1, 2], 10], yError: [0.1, [0.1, 0.3], 0.2] },
    { name: "Treatment", values: [1.1, 2.1, 3.5] },
  ],
};

describe("scientific chart contract", () => {
  it("normalizes symmetric and asymmetric errors and keeps optional wire type absent", () => {
    const result = scientificFields(canonicalizeChartSpec(SCATTER, "scatter"));
    expect(result.type).toBeUndefined();
    expect(result.series?.[0].xError).toEqual([[0.05, 0.05], [1, 2], [10, 10]]);
    expect(result.series?.[0].yError).toEqual([[0.1, 0.1], [0.1, 0.3], [0.2, 0.2]]);
    expect(canonicalizeChartSpec(result, "scatter")).toEqual(result);
  });

  it.each([
    [{ ...SCATTER, xValues: [1, 10] }, "length must match"],
    [{ ...SCATTER, series: [{ name: "A", values: [1, 2, 3] }, { name: "B", values: [1, 2] }] }, "length must match"],
    [{ ...SCATTER, series: [{ name: "A", values: [1, 2, 3], yError: [-1, 0, 0] }] }, "nonnegative"],
    [{ ...SCATTER, xValues: [1, Infinity, 100] }, "finite"],
    [{ ...SCATTER, xValues: [1, NaN, 100] }, "finite"],
    [{ ...SCATTER, xValues: [0, 10, 100] }, "log10 axis"],
    [{ ...SCATTER, xValues: [1, 10, 100], series: [{ name: "A", values: [1, 2, 3], xError: [1, 0, 0] }] }, "lower error endpoint"],
    [{ ...SCATTER, yScale: "log10", series: [{ name: "A", values: [1, 2, 3], yError: [1, 0, 0] }] }, "lower error endpoint"],
    [{ ...SCATTER, yScale: "log10", series: [{ name: "A", values: [1, 0, 3] }] }, "log10 axis"],
    [{ ...SCATTER, xScale: "linear", xValues: [1, 2, 3], series: [{ name: "A", values: [0, 0, 0], yError: [1e308, 1e308, 1e308] }] }, "range including error endpoints"],
    [{ ...SCATTER, series: [
      { name: "Lower", values: [0, 0, 0], yError: [[1.5e308, 0], [1.5e308, 0], [1.5e308, 0]] },
      { name: "Upper", values: [0, 0, 0], yError: [[0, 1.5e308], [0, 1.5e308], [0, 1.5e308]] },
    ] }, "range including all series errors"],
  ])("rejects invalid scientific values without skipping a point", (input, message) => {
    expect(() => canonicalizeChartSpec(input, "scatter")).toThrow(message);
  });
});

describe("log10 scientific scales", () => {
  it("maps each power of ten to equally spaced positions", () => {
    const scale = createLog10Scale([1, 100], [0, 400]);
    expect(scale(1)).toBeCloseTo(0);
    expect(scale(10)).toBeCloseTo(200);
    expect(scale(100)).toBeCloseTo(400);
    expect(log10Ticks(0.1, 1000)).toEqual([0.1, 1, 10, 100, 1000]);
    expect(formatLogTick(0.0001)).toBe("1e-4");
    expect(scientificDomain(1, 100, "log10")[0]).toBeGreaterThan(0);
  });

  it("contains representable extreme values without clipping them", () => {
    const tiny = Number.MIN_VALUE;
    const huge = Number.MAX_VALUE;
    const [logMin, logMax] = scientificDomain(tiny, huge, "log10");
    expect(logMin).toBeGreaterThan(0);
    expect(logMin).toBeLessThanOrEqual(tiny);
    expect(logMax).toBeGreaterThanOrEqual(huge);
    const log = createLog10Scale([logMin, logMax], [0, 100]);
    expect(log(tiny)).toBeGreaterThanOrEqual(0);
    expect(log(huge)).toBeLessThanOrEqual(100);
    const [tinyMin, tinyMax] = scientificDomain(1e-200, 2e-200, "linear");
    expect(tinyMin).toBeGreaterThan(0);
    expect(tinyMax).toBeLessThan(3e-200);
    expect(createLog10Scale([1e-200, 2e-200], [0, 100])(2e-200)).toBeCloseTo(100);
    const [linMin, linMax] = scientificDomain(-9e307, 8e307, "linear");
    expect(Number.isFinite(linMax - linMin)).toBe(true);
    expect(linMin).toBeLessThanOrEqual(-9e307);
    expect(linMax).toBeGreaterThanOrEqual(8e307);
  });

  it("keeps wide log tick collections bounded", () => {
    expect(log10Ticks(1e-250, 1e250).length).toBeLessThanOrEqual(7);
  });
});

describe("scientific React rendering", () => {
  it("draws scatter point markers, both orientations of errors, a named legend, and axis units", () => {
    const { container } = render(<ChartRenderer chartType="scatter" chartData={SCATTER as never} />);
    expect(container.querySelector(".chart-canvas-svg")).not.toBeNull();
    expect(container.querySelectorAll(".chart-scientific-point")).toHaveLength(6);
    expect(container.querySelectorAll(".chart-series-line")).toHaveLength(0);
    expect(container.querySelectorAll(".chart-x-error")).toHaveLength(3);
    expect(container.querySelectorAll(".chart-y-error")).toHaveLength(3);
    expect(screen.getByText("Control")).toBeInTheDocument();
    expect(screen.getByText("Treatment")).toBeInTheDocument();
    expect(screen.getByText("Concentration (mg/L)")).toBeInTheDocument();
    expect(screen.getByText("Response (mV)")).toBeInTheDocument();

    const firstSeries = container.querySelector(".chart-scientific-series")!;
    const points = firstSeries.querySelectorAll(".chart-series-marker");
    const positions = [...points].map((node) => Number(node.getAttribute("cx")));
    expect(positions[1] - positions[0]).toBeCloseTo(positions[2] - positions[1], 5);

    fireEvent.pointerMove(points[1], { clientX: 100, clientY: 50 });
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("10 mg/L");
    expect(tip).toHaveTextContent("1.8 mV");
    expect(tip).toHaveTextContent("-1/+2 mg/L");
    expect(tip).toHaveTextContent("-0.1/+0.3 mV");
    fireEvent.pointerLeave(points[1]);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("maps asymmetric error-bar endpoints to the same numeric log positions as its points", () => {
    const { container } = render(<ChartRenderer chartType="scatter" chartData={SCATTER as never} />);
    const first = container.querySelector(".chart-scientific-series")!;
    const marker = first.querySelector(".chart-series-marker")!;
    const capLine = first.querySelector(".chart-x-error line")!;
    const xDomain = scientificDomain(0.95, 110, "log10");
    const scale = createLog10Scale(xDomain, [0, 640 - 74 - 22]);
    expect(Number(marker.getAttribute("cx"))).toBeCloseTo(scale(1), 7);
    expect(Number(capLine.getAttribute("x1"))).toBeCloseTo(scale(0.95), 7);
    expect(Number(capLine.getAttribute("x2"))).toBeCloseTo(scale(1.05), 7);
  });

  it("renders a log10 y-axis with the original positive measurements and named lines", () => {
    const input = {
      type: "line", xValues: [1, 10, 100], xScale: "log10", yScale: "log10",
      xUnit: "s", yUnit: "mV",
      series: [{ name: "Detector", values: [0.001, 0.1, 10], yError: [0.0001, 0.01, 1] }],
    };
    const { container } = render(<ChartRenderer chartType="line" chartData={input as never} />);
    expect(container.querySelectorAll(".chart-series-marker")).toHaveLength(3);
    expect(container.querySelectorAll(".chart-series-line")).toHaveLength(1);
    const marks = container.querySelectorAll(".chart-series-marker");
    expect(Number(marks[1].getAttribute("cy"))).toBeLessThan(Number(marks[0].getAttribute("cy")));
    fireEvent.pointerMove(marks[0], { clientX: 50, clientY: 50 });
    expect(screen.getByRole("tooltip")).toHaveTextContent("0.001 mV");
    expect(screen.getByRole("tooltip")).toHaveTextContent("0.0001");
  });

  it("connects named lines, not scatters, and does not change legacy line geometry", () => {
    const multiseries = {
      type: "line", labels: ["A", "B", "C"],
      series: [
        { name: "First", values: [1, 3, 2] },
        { name: "Second", values: [3, 2, 1] },
      ],
    };
    const { container, rerender } = render(<ChartRenderer chartType="line" chartData={multiseries as never} />);
    expect(container.querySelectorAll(".chart-series-line")).toHaveLength(2);
    expect(container.querySelectorAll(".chart-scientific-series")).toHaveLength(2);
    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    rerender(<ChartRenderer chartType="line" chartData={{
      type: "line", labels: ["A", "B", "C"], values: [1, 3, 2],
    } as never} />);
    expect(container.querySelector(".chart-scientific-marks")).toBeNull();
    expect(container.querySelector("path[fill='none']")).not.toBeNull();
  });

  it("shows a specific error placeholder for invalid log intervals", () => {
    const invalid = { ...SCATTER, series: [{
      name: "A", values: [1, 2, 3], yError: [1, 0, 0],
    }], yScale: "log10" };
    const { container } = render(<ChartRenderer chartType="scatter" chartData={invalid as never} />);
    expect(container.querySelector(".chart-canvas-placeholder")).not.toBeNull();
    expect(screen.getByText(/lower error endpoint/)).toBeInTheDocument();
  });
});
