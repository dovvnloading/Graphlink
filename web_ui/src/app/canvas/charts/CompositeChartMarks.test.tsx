import { describe, expect, it } from "vitest";
import { render, fireEvent, screen, waitFor } from "@testing-library/react";
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

  it("respects the figure-wide mark budget when faceting is dense", () => {
    const spec = canonicalizeChartSpec(SOURCE).composite!;
    const sample = compositeSample(spec, spec.layers[1], [0, 1, 2, 3, 4, 5], 3);
    expect(sample.length).toBeLessThanOrEqual(3);
    expect(sample).toContain(0);
    expect(sample).toContain(5);
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

it("uses distinct, complete legend slots for multiple categorical fields and repeated layers", () => {
  const data = canonicalizeChartSpec({ type: "composite", version: 2, composite: {
    numbers: { x: [0, 1, 2, 3], y: [1, 2, 3, 4] },
    categories: { treatment: ["A", "B", "C", "D"], batch: ["A", "B", "C", "D"] },
    layers: [
      { mark: "point", x: "x", y: "y", colorBy: "treatment" },
      { mark: "point", x: "x", y: "y", colorBy: "batch" },
      { mark: "line", x: "x", y: "y", colorBy: "treatment" },
    ],
  } });
  const { container } = render(<ChartRenderer chartType="composite" chartData={data} />);
  const legend = [...container.querySelectorAll(".chart-composite-legend > g")];
  expect(legend).toHaveLength(8);
  expect(legend.map(entry => entry.querySelector("text")?.textContent)).toEqual([
    "treatment: A", "treatment: B", "treatment: C", "treatment: D",
    "batch: A", "batch: B", "batch: C", "batch: D",
  ]);
  const fills = legend.map(entry => entry.querySelector("circle")?.getAttribute("fill"));
  expect(new Set(fills).size).toBe(8);
  const treatment = container.querySelector('[data-color-field="treatment"][data-group="A"] .chart-composite-point');
  const batch = container.querySelector('[data-color-field="batch"][data-group="A"] .chart-composite-point');
  expect(treatment?.getAttribute("fill")).toBe(fills[0]);
  expect(batch?.getAttribute("fill")).toBe(fills[4]);
  expect(container.querySelector('[data-color-field="treatment"][data-group="A"] .chart-composite-line')?.getAttribute("stroke")).toBe(fills[0]);
  // An ungrouped layer also needs a unique color slot; a ninth entry is
  // rejected rather than silently omitting a legend item.
  expect(() => canonicalizeChartSpec({ ...data, composite: { ...data.composite!,
    layers: [...data.composite!.layers, { mark: "area", x: "x", y: "y" }],
  } })).toThrow(/eight color legend entries/);
});

it("rejects too many groups across independently valid categorical fields", () => {
  const groups = ["A", "B", "C", "D", "E"];
  expect(() => canonicalizeChartSpec({ type: "composite", composite: {
    numbers: { x: [0, 1, 2, 3, 4], y: [1, 2, 3, 4, 5] },
    categories: { treatment: groups, batch: groups },
    layers: [
      { mark: "point", x: "x", y: "y", colorBy: "treatment" },
      { mark: "point", x: "x", y: "y", colorBy: "batch" },
    ],
  } })).toThrow(/eight color legend entries/);
});

describe("composite source-row linked brushing", () => {
  it("selects unsampled source rows across every layer and creates an explicit connected branch", async () => {
    const original = JSON.stringify(SOURCE);
    const branch = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ChartRenderer chartType="composite"
      chartData={canonicalizeChartSpec(SOURCE)} onBranchSelection={branch} />);
    const branchButton = screen.getByRole("button", { name: "Branch selected rows" });
    expect(branchButton).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Brush source rows" }));
    const panel = container.querySelector('[data-facet="A"] .chart-composite-brush-surface')!;
    expect(panel).not.toBeNull();
    // Keyboard users can select every record in the focused facet.
    fireEvent.keyDown(panel, { key: "Enter" });
    expect(screen.getByRole("status")).toHaveTextContent("3 of 6 source rows selected");
    expect(branchButton).toBeEnabled();
    const selected = container.querySelectorAll('.chart-composite-point[data-selected="true"]');
    const unselected = container.querySelectorAll('.chart-composite-point[data-selected="false"]');
    expect(selected).toHaveLength(3);
    expect(unselected).toHaveLength(3);
    // The same original row indices are highlighted in the line and
    // uncertainty-band layers, not a position sampled from their SVG paths.
    const linked = [...container.querySelectorAll(".chart-composite-linked-highlight")];
    expect(linked).toHaveLength(6);
    expect(linked.map(el => el.getAttribute("data-index"))).toEqual([
      "0", "1", "2", "0", "1", "2",
    ]);
    fireEvent.click(branchButton);
    await waitFor(() => expect(branch).toHaveBeenCalledOnce());
    expect(branch.mock.calls[0][0]).toEqual([0, 1, 2]);
    expect(branch.mock.calls[0][1]).toEqual(canonicalizeChartSpec(SOURCE).composite);
    await waitFor(() => expect(screen.getByRole("status")).not.toHaveTextContent("3 of 6"));
    expect(JSON.stringify(SOURCE)).toBe(original);
  });

  it("supports rectangular drag selection and clearing without changing source arrays", () => {
    const spec = canonicalizeChartSpec(SOURCE);
    const { container } = render(<ChartRenderer chartType="composite" chartData={spec} />);
    fireEvent.click(screen.getByRole("button", { name: "Brush source rows" }));
    const surface = container.querySelector('[data-facet="A"] .chart-composite-brush-surface')!;
    fireEvent.pointerDown(surface, { pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(surface, { pointerId: 1, clientX: 700, clientY: 470 });
    expect(container.querySelector(".chart-composite-brush-rectangle")).not.toBeNull();
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 700, clientY: 470 });
    expect(screen.getByRole("status")).toHaveTextContent("3 of 6 source rows selected");
    expect(screen.getByRole("button", { name: "Branch selected rows" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.getByRole("status")).not.toHaveTextContent("3 of 6");
    expect(spec.composite?.numbers).toEqual(SOURCE.composite.numbers);
  });

  it("surfaces stale dataset errors without losing the selection or falsely creating a chart", async () => {
    const error = vi.fn().mockRejectedValue(new Error("Composite source data changed"));
    const { container } = render(<ChartRenderer chartType="composite"
      chartData={canonicalizeChartSpec(SOURCE)} onBranchSelection={error} />);
    fireEvent.click(screen.getByRole("button", { name: "Brush source rows" }));
    fireEvent.keyDown(container.querySelector('[data-facet="A"] .chart-composite-brush-surface')!, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Branch selected rows" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("source data changed"));
    expect(screen.getByRole("status")).toHaveTextContent("3 of 6 source rows selected");
    expect(error).toHaveBeenCalledOnce();
  });

  it("highlights brushed point rows that the display sampler dropped (#1079)", () => {
    const values = Array.from({ length: 2000 }, (_, i) => i);
    const spec = canonicalizeChartSpec({
      type: "composite", version: 2, title: "Dense",
      composite: { numbers: { x: values, y: values }, layers: [{ mark: "point", x: "x", y: "y" }] },
    });
    const sampled = compositeSample(spec.composite!, spec.composite!.layers[0], values, 1200);
    expect(sampled).not.toContain(1);
    expect(sampled).not.toContain(2);
    const { container } = render(<ChartRenderer chartType="composite" chartData={spec} />);
    fireEvent.click(screen.getByRole("button", { name: "Brush source rows" }));
    const surface = container.querySelector(".chart-composite-brush-surface")!;
    // Domain padding puts x=y=0 slightly inside the panel; brush a box
    // that covers only the observations at 1 and 2 in data space.
    const box = surface.getBoundingClientRect();
    const scale = compositeDomains(spec);
    const px = (v: number) => (v - scale.x[0]) / (scale.x[1] - scale.x[0]) * box.width;
    const py = (v: number) => (1 - (v - scale.y[0]) / (scale.y[1] - scale.y[0])) * box.height;
    fireEvent.pointerDown(surface, { pointerId: 1, clientX: px(0.5), clientY: py(2.5) - 3 });
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: px(2.5), clientY: py(0.5) + 3 });
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2000 source rows selected");
    expect(screen.getByRole("status")).not.toHaveTextContent("sampled");
    const visible = [...container.querySelectorAll('[data-selected="true"]')]
      .map(el => el.getAttribute("data-index"));
    expect(visible.sort()).toEqual(["1", "2"]);
  });

  it("does not duplicate selected point glyphs and discloses sampled highlights", () => {
    const values = Array.from({ length: 2000 }, (_, i) => i);
    const spec = canonicalizeChartSpec({
      type: "composite", version: 2, title: "Dense",
      composite: { numbers: { x: values, y: values }, layers: [{ mark: "point", x: "x", y: "y" }] },
    });
    const { container } = render(<ChartRenderer chartType="composite" chartData={spec} />);
    fireEvent.click(screen.getByRole("button", { name: "Brush source rows" }));
    fireEvent.keyDown(container.querySelector(".chart-composite-brush-surface")!, { key: "Enter" });
    expect(screen.getByRole("status")).toHaveTextContent(
      "2000 of 2000 source rows selected (highlights sampled for display)");
    const indices = [...container.querySelectorAll('[data-selected="true"]')]
      .map(el => el.getAttribute("data-index"));
    expect(new Set(indices).size).toBe(indices.length);
    expect(container.querySelectorAll(".chart-composite-linked-highlight").length).toBeLessThanOrEqual(250);
  });

  it("drops transient brushing state on a changed source snapshot", () => {
    const spec = canonicalizeChartSpec(SOURCE);
    const { container, rerender } = render(<ChartRenderer chartType="composite"
      chartData={spec} onBranchSelection={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Brush source rows" }));
    fireEvent.keyDown(container.querySelector('[data-facet="A"] .chart-composite-brush-surface')!, { key: "Enter" });
    expect(screen.getByRole("status")).toHaveTextContent("3 of 6");
    const updated = canonicalizeChartSpec({
      ...spec, composite: {
        ...spec.composite!, numbers: {
          ...spec.composite!.numbers, response: [2, 3, 4, 5, 6, 7],
        },
      },
    });
    rerender(<ChartRenderer chartType="composite" chartData={updated} onBranchSelection={vi.fn()} />);
    expect(screen.getByRole("status")).not.toHaveTextContent("3 of 6");
    expect(container.querySelectorAll(".chart-composite-linked-highlight")).toHaveLength(0);
  });
});
