import { describe, expect, it } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { beforeEach, afterEach, vi } from "vitest";
import ChartRenderer from "./ChartRenderer";
import ChartOverview from "./ChartOverview";
import { canonicalizeChartSpec } from "../chartSpec";
import { compositeFacets, compositeDomains, compositeGroups, compositeSample } from "./compositeGeometry";

const SOURCE = {
  type: "composite", version: 2, title: "Experiment A",
  xAxis: "Time", yAxis: "Response",
  composite: {
    numbers: {
      time: [0, 1, 2, 3, 4, 5],
      response: [1.2, 1.8, 2.1, 2.5, 3, 3.1],
      low: [0.9, 1.5, 1.8, 2.2, 2.7, 2.8],
      high: [1.5, 2.1, 2.4, 2.8, 3.3, 3.4],
    },
    categories: { treatment: ["A", "A", "A", "B", "B", "B"] },
    facetBy: "treatment",
    layers: [
      { mark: "band", x: "time", y: "low", y2: "high", colorBy: "treatment", label: "Uncertainty" },
      { mark: "line", x: "time", y: "response", colorBy: "treatment", label: "Trend" },
      { mark: "point", x: "time", y: "response", colorBy: "treatment" },
    ],
  },
};

const originalResizeObserver = globalThis.ResizeObserver;
beforeEach(() => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    width: 700, height: 470, top: 0, left: 0, right: 700, bottom: 470,
    x: 0, y: 0, toJSON: () => ({}),
  });
  class ResizeStub {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry],
        this as unknown as ResizeObserver);
    }
    disconnect() {}
    unobserve() {}
  }
  globalThis.ResizeObserver = ResizeStub as unknown as typeof ResizeObserver;
});
afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver;
  vi.restoreAllMocks();
});

describe("ChartSpec V2 composite figures", () => {
  it("preserves every data column and composes facets and grouped marks", () => {
    const before = JSON.stringify(SOURCE);
    const spec = canonicalizeChartSpec(SOURCE);
    expect(spec.version).toBe(2);
    expect(spec.composite?.numbers.response).toEqual(SOURCE.composite.numbers.response);
    expect(compositeFacets(spec.composite!)).toEqual(["A", "B"]);
    expect(compositeDomains(spec).y[1]).toBeGreaterThan(3.4);
    expect(compositeGroups(spec.composite!, spec.composite!.layers[1], "A")).toMatchObject([
      { group: "A", indices: [0, 1, 2] },
    ]);
    expect(canonicalizeChartSpec(spec)).toEqual(spec);
    expect(JSON.stringify(SOURCE)).toBe(before);
  });

  it("draws points, connected lines, uncertainty bands and two facet panels", () => {
    const spec = canonicalizeChartSpec(SOURCE);
    const { container } = render(<ChartRenderer chartType="composite" chartData={spec} />);
    expect(container.querySelectorAll(".chart-composite-marks [data-facet]")).toHaveLength(2);
    expect(container.querySelectorAll(".chart-composite-band")).toHaveLength(2);
    expect(container.querySelectorAll(".chart-composite-line")).toHaveLength(2);
    expect(container.querySelectorAll(".chart-composite-point")).toHaveLength(6);
    fireEvent.pointerMove(container.querySelector(".chart-composite-point")!, { clientX: 200, clientY: 200 });
    expect(container.querySelector('[role="tooltip"]')).not.toBeNull();
  });

  it("shows the same facet and data structure in overview", () => {
    const { container } = render(<ChartOverview chartType="composite" chartData={canonicalizeChartSpec(SOURCE)} />);
    expect(container.querySelectorAll(".chart-composite-overview [data-facet]")).toHaveLength(2);
    expect(container.querySelectorAll(".chart-composite-band")).toHaveLength(2);
  });

  it("samples dense marks without ever mutating or dropping underlying values", () => {
    const input = canonicalizeChartSpec({ type: "composite", version: 2, composite: {
      numbers: {
        time: Array.from({ length: 2000 }, (_, i) => i),
        signal: Array.from({ length: 2000 }, (_, i) => i === 1137 ? 999 : Math.sin(i / 5)),
      },
      layers: [{ mark: "line", x: "time", y: "signal" }],
    } });
    const indices = Array.from({ length: 2000 }, (_, i) => i);
    const sample = compositeSample(input.composite!, input.composite!.layers[0], indices, 120);
    expect(sample.length).toBeLessThanOrEqual(120);
    expect(sample).toContain(1137);
    expect(input.composite?.numbers.signal).toHaveLength(2000);
    expect(() => canonicalizeChartSpec({ type: "composite", version: 2, composite: {
      numbers: { x: Array(2001).fill(0), y: Array(2001).fill(1) },
      layers: [{ mark: "point", x: "x", y: "y" }],
    } })).toThrow(/2000/);
  });

  it("rejects invalid, unsafe, ambiguous or scientifically misleading encodings", () => {
    const base = SOURCE.composite;
    const cases: unknown[] = [
      { ...SOURCE, version: 1 },
      { ...SOURCE, composite: { ...base, facetBy: "missing" } },
      { ...SOURCE, composite: { ...base, layers: [{ mark: "band", x: "time", y: "response" }] } },
      { ...SOURCE, composite: { ...base, layers: [{ mark: "line", x: "time", y: "missing" }] } },
      { ...SOURCE, composite: { ...base, layers: [{ mark: "script", x: "time", y: "response" }] } },
      { ...SOURCE, composite: { ...base, layers: [{ mark: "point", x: "time", y: "response",
        onClick: "location.href='evil'" }] } },
      { ...SOURCE, composite: { ...base, numbers: { ...base.numbers, high: [0, 0, 0, 0, 0, 0] } } },
      { ...SOURCE, yScale: "log10", composite: { ...base, layers: [
        { mark: "area", x: "time", y: "response" },
      ] } },
      { ...SOURCE, xScale: "log10" },
    ];
    for (const bad of cases) expect(() => canonicalizeChartSpec(bad)).toThrow();
  });
});
