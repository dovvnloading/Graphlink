import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { WsTimeoutError, type WsTransport } from "../../lib/ws/transport";
import { SceneStore, initialSceneState } from "./sceneStore";
import { ScienceTableSource } from "./ScienceTableSource";
import { validateSceneState } from "../../lib/bridge-core/generated/scene-state";
import { ScientificDataTable } from "./ScientificDataTable";
import { ScientificNodeView, type ScientificFlowNode } from "./ScientificNodeView";
import { readScienceState, type ScienceChartLink, type ScienceState, type ScienceResult } from "./scientificWorkbench";
import * as download from "./downloadTextFile";

const operation = {
  id: "integral", title: "Integrate samples", description: "Sum trapezoids between consecutive samples.",
  fields: [{ key: "samples", label: "Samples", kind: "vector" as const, unit: "m", description: "Ordered observations." }],
  example: { samples: [1, 2, 3] },
};
const operation2 = {
  id: "scale", title: "Scale length", description: "Scale a measured length.",
  fields: [{ key: "length", label: "Length", kind: "number" as const, unit: "m", description: "" }],
  example: { length: 3 },
};
const state: ScienceState = { scienceVersion: 1, packId: "mathematics", operations: [operation, operation2], latest: null };
const result: ScienceResult = {
  operationId: "integral", inputs: { samples: [10, 20, 30] }, summary: "Computed 3 samples.", values: { distance: 0.125 },
  table: { columns: ["x", "y"], rows: [[0, 1], [2, 8], [10, 3]] },
  plot: { x: [0, 2, 10], y: [1, 8, 3], title: "Distance", xLabel: "Time (s)", yLabel: "Length (m)" },
  provenance: { packId: "mathematics", packVersion: "1.0.0", resultHash: "abc", createdAt: "2026-11-16T16:00:00Z" },
};
const parent = { title: "Samples", nodeId: "parent", raw: JSON.stringify({ ...state, latest: result }) };

function mount(raw = JSON.stringify(state), parents = [parent], charts: ScienceChartLink[] = []) {
  const request = vi.fn<WsTransport["request"]>().mockResolvedValue({ ok: true });
  const fireIntent = vi.fn<WsTransport["fireIntent"]>();
  const store = new SceneStore({ request, fireIntent } as unknown as WsTransport);
  const generateChart = vi.spyOn(store, "generateChart").mockImplementation(() => {});
  function draw(value: string, currentParents = parents, currentCharts = charts) {
    const props = { id: "target", data: { title: "Mathematics", raw: value, pluginId: "science_mathematics", parents: currentParents, charts: currentCharts, store } } as NodeProps<ScientificFlowNode>;
    return <ScientificNodeView {...props} />;
  }
  let view = render(draw(raw), { wrapper: ReactFlowProvider });
  return { request, fireIntent, generateChart, remount: (value: string) => { view.unmount(); view = render(draw(value), { wrapper: ReactFlowProvider }); }, refresh: (value: string, currentParents = parents, currentCharts = charts) => view.rerender(draw(value, currentParents, currentCharts)) };
}

function openImporter() {
  fireEvent.click(screen.getByText(/^(Import a data table|Replace data table)$/));
}

function expandResult(label: string | RegExp) {
  const latest = screen.getByRole("region", { name: "Latest result" });
  fireEvent.click(within(latest).getByText(label));
}

describe("scientific workbench card", () => {
  it("moves one live editor between surfaces and restores keyboard focus and unsaved import text", async () => {
    const user = userEvent.setup();
    const { request } = mount();
    const value = screen.getByRole("textbox", { name: "Samples (m)" });
    fireEvent.change(value, { target: { value: "[2,6]" } });
    openImporter();
    const pasted = screen.getByRole("textbox", { name: "Pasted table text" });
    fireEvent.change(pasted, { target: { value: "Time,Reading\n1,3" } });
    const opener = screen.getByRole("button", { name: "Open focused workbench for Mathematics" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Mathematics workbench" });
    expect(within(dialog).getByRole("textbox", { name: "Samples (m)" })).toBe(value);
    expect(within(dialog).getByRole("textbox", { name: "Pasted table text" })).toBe(pasted);
    expect(screen.getAllByRole("button", { name: "Run calculation" })).toHaveLength(1);
    expect(within(dialog).getByRole("combobox", { name: "Operation" })).toHaveFocus();
    expect(within(dialog).getByRole("button", { name: "Close Mathematics workbench" })).not.toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
    expect(pasted).toBeVisible();
    expect(pasted).toHaveValue("Time,Reading\n1,3");
    expect(value).toHaveValue("[2,6]");
    expect(request).not.toHaveBeenCalled();
  });

  it("puts recorded warnings next to the saved result and distinguishes changed inputs and operation", () => {
    const latest = { ...result, values: { distance: 0.1234567890123456, warning: "Inspect the fitted residuals." } };
    mount(JSON.stringify({ ...state, latest }));
    const region = screen.getByRole("region", { name: "Latest result" });
    expect(within(region).getByRole("region", { name: "Result warnings" })).toHaveTextContent("Inspect the fitted residuals.");
    expandResult("All values · 2");
    fireEvent.click(within(region).getByRole("checkbox", { name: "Show stored precision" }));
    expect(within(region).getByText("0.1234567890123456")).toBeVisible();
    fireEvent.change(screen.getByRole("textbox", { name: "Samples (m)" }), { target: { value: "[1,4]" } });
    expect(within(region).getByRole("note")).toHaveTextContent("Inputs have been edited");
    fireEvent.change(screen.getByRole("combobox", { name: "Operation" }), { target: { value: "scale" } });
    expect(within(region).getByRole("note")).toHaveTextContent("different operation");
    expect(within(region).getByText("0.1234567890123456")).toBeVisible();
  });

  it("renders declared residuals as independent observations and preserves their exact submitted order", () => {
    const plot = { x: [2, 1, 3], y: [.1234567890123456, -.5, .75], title: "Residuals", xLabel: "X", yLabel: "Residual" };
    mount(JSON.stringify({ ...state, operations: [{ ...operation, plotMark: "scatter" }],
      latest: { ...result, plot } }));
    expandResult("Plots & views");
    const svg = screen.getByRole("img", { name: /Residuals/ });
    expect(svg.querySelector("polyline")).toBeNull();
    expect(svg.querySelectorAll("circle")).toHaveLength(3);
    fireEvent.click(screen.getByText("Exact plot coordinates"));
    const rows = within(screen.getByRole("table", { name: "Exact plot coordinates" })).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("120.1234567890123456");
    expect(rows[2]).toHaveTextContent("21-0.5");
  });

  it("paginates exact data and keeps rows readable when a new result is shorter", () => {
    const rows = Array.from({ length: 41 }, (_, i) => [String(i + 1)]);
    const table = (count: number) => <ScientificDataTable title="Sample values" columns={["Sample"]} rows={() => rows.slice(0, count)} />;
    const view = render(table(41));
    fireEvent.click(screen.getByText("Sample values"));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(41);
    fireEvent.click(screen.getByRole("button", { name: "Next rows" }));
    expect(screen.getByRole("status")).toHaveTextContent("Rows 41–41 of 41");
    expect(screen.getByRole("rowheader", { name: "41" })).toBeVisible();
    view.rerender(table(3));
    expect(screen.getByRole("rowheader", { name: "1" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Next rows" })).toBeNull();
  });

  it("shows one meaningful headline and keeps full data and actions on demand", () => {
    const latestResult: ScienceResult = {
      ...result, summary: "Described 3 observations.", values: { count: 3, mean: 12, spread: [8, 10, 18] },
      provenance: { ...result.provenance, inputUnits: { samples: "mm" } },
    };
    mount(JSON.stringify({ ...state, latest: latestResult }));
    const latest = screen.getByRole("region", { name: "Latest result" });
    const headline = latest.querySelector(".science-result-highlight")!;
    expect(headline).toHaveTextContent("Mean");
    expect(headline).toHaveTextContent("12");
    expect(within(latest).getByRole("status")).toHaveTextContent("Described 3 observations.");
    expect(within(latest).queryByText("spread")).not.toBeInTheDocument();
    expect(within(latest).queryByRole("table")).not.toBeInTheDocument();
    expect(within(latest).queryByRole("img")).not.toBeInTheDocument();
    expect(within(latest).queryByRole("button", { name: "Export JSON" })).not.toBeInTheDocument();

    expandResult(/All values · 3/);
    expect(within(latest).getByText("spread")).toBeVisible();
    expect(within(latest).getByText("[8, 10, 18]")).toBeVisible();
    expandResult(/All values · 3/);
    expect(within(latest).queryByText("spread")).not.toBeInTheDocument();
    expandResult("Tables · 3 rows");
    expect(within(latest).getByRole("table", { name: "Result table" })).toBeVisible();
    expandResult("Plots & views");
    expect(within(latest).getByRole("img")).toBeVisible();
    expandResult("Units used");
    const unitDetails = within(latest).getByText("Units used").closest("details")!;
    expect(within(unitDetails).getByText(/"mm"/)).toBeVisible();
    expandResult("Inputs and provenance");
    expect(within(latest).getByText(/"resultHash": "abc"/)).toBeVisible();
    expandResult("Actions & exports");
    expect(within(latest).getByRole("button", { name: "Export JSON" })).toBeVisible();
  });

  it("leads with the operation's declared headline and falls back when that value is missing", () => {
    const fit: ScienceResult = { ...result, values: { a: 0.05, degrees_of_freedom: 5, r_squared: 0.9998 } };
    const declared = { ...state, operations: [{ ...operation, headline: "r_squared" }, operation2] };
    mount(JSON.stringify({ ...declared, latest: fit }));
    const headline = () => screen.getByRole("region", { name: "Latest result" }).querySelector(".science-result-highlight")!;
    expect(headline()).toHaveTextContent("R squared");
    expect(headline()).toHaveTextContent("0.9998");
    document.body.innerHTML = "";
    // An older result without the declared key keeps the generic choice.
    mount(JSON.stringify({ ...declared, latest: { ...fit, values: { a: 0.05 } } }));
    expect(headline()).toHaveTextContent("A");
    expect(readScienceState(JSON.stringify({ ...state, operations: [{ ...operation, headline: 3 }] }))).toBeNull();
  });

  it("lets long scientific summaries expand and shows table-only results without guessing a metric", () => {
    const long = "Fit diagnostics and model assumptions. ".repeat(9);
    mount(JSON.stringify({ ...state, latest: {
      ...result, values: {}, plot: undefined, summary: long,
    } }));
    const latest = screen.getByRole("region", { name: "Latest result" });
    expect(latest.querySelector(".science-result-highlight")).toHaveTextContent("3 rows");
    expect(latest.querySelector(".science-result-summary")).toHaveClass("science-result-summary--clamped");
    expandResult("Read full summary");
    const fullSummary = within(latest).getByText("Read full summary").closest("details");
    expect(fullSummary?.querySelector("p")?.textContent).toBe(long);
    expect(within(latest).queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders a volcano scatter with BH thresholds, categories and clipped zero q-values", () => {
    const volcano = { type: "volcano_plot", title: "Volcano: drug vs control",
      xLabel: "log2 fold change (drug / control)", fdrThreshold: 0.05, log2FoldChangeThreshold: 1,
      points: [
        { gene: "GeneUp", log2FoldChange: 2.5, qValue: 0.001 },
        { gene: "GeneDown", log2FoldChange: -2.2, qValue: 0 },
        { gene: "GeneOther", log2FoldChange: 0.5, qValue: 0.3 },
      ] };
    mount(JSON.stringify({ ...state, latest: { ...result, plot: undefined, views: [volcano] } }));
    expandResult("Plots & views");
    const svg = screen.getByRole("img", { name: /volcano scatter of 3 genes/ });
    expect(svg.querySelectorAll(".science-volcano-point")).toHaveLength(3);
    expect(svg.querySelector(".science-volcano-up title")?.textContent).toContain("GeneUp");
    expect(svg.querySelector(".science-volcano-down title")?.textContent).toContain("GeneDown");
    expect(svg.querySelectorAll(".science-volcano-fold-threshold")).toHaveLength(2);
    expect(svg.querySelector(".science-volcano-fdr-threshold")).toHaveAttribute("stroke-dasharray", "4 4");
    expect(svg.querySelector("polyline")).toBeNull();
    const down = svg.querySelector(".science-volcano-down")!;
    expect(down.tagName.toLowerCase()).toBe("path");
    expect(down.getAttribute("d")).not.toMatch(/NaN|Infinity/);
    expect(svg.querySelector(".science-volcano-other")?.tagName.toLowerCase()).toBe("circle");
    fireEvent.click(screen.getByText("Volcano feature data"));
    const data = screen.getByRole("table", { name: "Volcano feature data" });
    expect(within(data).getByRole("row", { name: /GeneDown/ })).toHaveTextContent("GeneDown-2.20Down in B");
    expect(screen.getByText(/Up in B: 1/)).toBeVisible();
    expect(screen.getByText(/Down in B: 1/)).toBeVisible();
    expect(screen.getByText(/Other tested: 1/)).toBeVisible();
    expect(screen.getByText(/Underflowed q=0 plotted at/)).toBeVisible();
  });

  it("discusses only a saved result and sends its receipt, not unverified browser data", () => {
    const mounted = mount();
    expect(screen.queryByRole("button", { name: "Discuss in chat" })).not.toBeInTheDocument();

    mounted.refresh(JSON.stringify({
      ...state, latest: { ...result, provenance: { ...result.provenance, receiptHash: "receipt-123" } },
    }));
    expandResult("Actions & exports");
    fireEvent.click(screen.getByRole("button", { name: "Discuss in chat" }));
    expect(mounted.fireIntent).toHaveBeenCalledWith(
      "scene", "discussScientificResult", ["target", "abc", "receipt-123"],
      undefined, false, undefined, expect.any(Function),
    );
    mounted.refresh(JSON.stringify({ ...state, latest: null }));
    expect(screen.queryByRole("button", { name: "Discuss in chat" })).not.toBeInTheDocument();
  });

  it("renders categorical choices as a dropdown with linked methods and citations", async () => {
    const choiceOp = {
      id: "potency", title: "Potency fit", description: "Fit a response curve.",
      fields: [{ key: "metric", label: "Potency metric", kind: "choice" as const,
        unit: "category", description: "", choices: ["EC50", "IC50"] }],
      example: { metric: "EC50" },
      methods: "Four-parameter logistic fit, with relative potency at the response midpoint.",
      citations: [{ label: "Sebaugh (2011)", url: "https://doi.org/10.1002/pst.426" }],
    };
    const { request } = mount(JSON.stringify({ ...state, operations: [choiceOp] }));
    const metric = screen.getByRole("combobox", { name: "Potency metric" });
    expect(metric).toHaveValue("EC50");
    expect(within(metric).getAllByRole("option")).toHaveLength(2);
    const methods = screen.getByText("About & methods").closest("details");
    expect(methods).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("About & methods"));
    expect(methods).toHaveAttribute("open");
    expect(screen.getByText(/Four-parameter logistic fit/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Sebaugh (2011)" }))
      .toHaveAttribute("href", "https://doi.org/10.1002/pst.426");
    fireEvent.change(metric, { target: { value: "IC50" } });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      operation_id: "potency", inputs: { metric: "IC50" },
    });
  });

  it("charts a saved XY trace and exposes receipt-aware refresh for its linked chart", () => {
    const charts: ScienceChartLink[] = [{
      id: "child-chart", title: "Distance chart", chartType: "scatter",
      receiptHash: "b".repeat(64), stale: true,
    }];
    const { generateChart } = mount(JSON.stringify({ ...state, latest: result }), [parent], charts);
    expandResult("Actions & exports");
    expect(screen.getByRole("button", { name: "Chart this" })).toBeEnabled();
    expect(screen.getByRole("combobox", { name: "Chart type" })).toHaveValue("scatter");
    fireEvent.click(screen.getByRole("button", { name: "Chart this" }));
    expect(generateChart).toHaveBeenCalledWith("target", "scatter", "abc");
    fireEvent.change(screen.getByRole("combobox", { name: "Chart type" }), { target: { value: "heatmap" } });
    fireEvent.click(screen.getByRole("button", { name: "Chart this" }));
    expect(generateChart).toHaveBeenCalledWith("target", "heatmap", "abc");
    expect(screen.getByText("Source changed · refresh to update")).toBeVisible();
    expect(screen.getByText(/Receipt bbbbbbbbbbbb/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh chart" }));
    expect(generateChart).toHaveBeenCalledWith("child-chart", "scatter", "abc");
  });

  it("only runs on submit, validates numeric arrays, and preserves the draft on a backend error", async () => {
    const { request } = mount();
    const input = screen.getByLabelText("Samples (m)");
    fireEvent.change(input, { target: { value: "[123, null]" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Samples must be an array of numbers");
    expect(request).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "[3,2,1]" } });
    request.mockResolvedValueOnce({ ok: false, error: "Times must increase." });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Times must increase.");
    expect(input).toHaveValue("[3,2,1]");
    fireEvent.click(screen.getByText("Load example"));
    expect(input).toHaveValue("[1,2,3]");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("imports a connected column with its provenance, clears the citation when edited, and switches operations", async () => {
    const { request } = mount();
    const source = screen.getByRole("combobox", { name: "Import Samples from a connected result" });
    const index = Array.from((source as HTMLSelectElement).options).find((o) => o.text === "Samples · y")!.value;
    fireEvent.change(source, { target: { value: index } });
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[1,8,3]");
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", ["science_mathematics", "run", {
      node_id: "target", operation_id: "integral", inputs: { samples: [1, 8, 3] },
      sources: { samples: { nodeId: "parent", resultHash: "abc", selector: ["column", 1] } },
    }], 615_000);
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Samples (m)"), { target: { value: "[2]" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({ inputs: { samples: [2] }, sources: {} });
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Operation"), { target: { value: "scale" } });
    expect(screen.getByLabelText("Length (m)")).toHaveValue("3");
    fireEvent.change(screen.getByLabelText("Length (m)"), { target: { value: ".5e1" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({ operation_id: "scale", inputs: { length: 5 } });
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
  });

  it("restores edited source bindings after loading an example, including across remount", async () => {
    const { request, remount } = mount();
    const source = screen.getByRole("combobox", { name: "Import Samples from a connected result" }) as HTMLSelectElement;
    const option = Array.from(source.options).find((item) => item.text === "Samples · y")!;
    fireEvent.change(source, { target: { value: option.value } });
    fireEvent.click(screen.getByRole("button", { name: "Load example" }));
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[1,2,3]");
    remount(JSON.stringify(state));
    fireEvent.click(screen.getByRole("button", { name: "Undo load example" }));
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[1,8,3]");
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      sources: { samples: { selector: ["column", 1], resultHash: "abc" } },
    }));
  });

  it("renders persisted runs, plots irregular x coordinates, and exports the committed artifacts", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const { request } = mount(JSON.stringify({ ...state, latest: result, runToken: "current" }));
      const canonical = JSON.stringify(result).replace('"distance":0.125', '"distance":0.1250');
      request.mockResolvedValueOnce({ ok: true, json: canonical });
      expect(screen.getByLabelText("Samples (m)")).toHaveValue("[10,20,30]");
      expect(screen.getByRole("status")).toHaveTextContent("Computed 3 samples.");
      expandResult("Plots & views");
      expect(screen.getByRole("img").querySelectorAll("circle")[1].getAttribute("cx")).toBe("70");
      expandResult("Tables · 3 rows");
      expect(screen.getByRole("table", { name: "Result table" })).toBeInTheDocument();
      fireEvent.click(screen.getByText("Inputs and provenance"));
      expect(screen.getByText(/"resultHash": "abc"/)).toBeVisible();
      expandResult("Actions & exports");
      fireEvent.click(screen.getByText("Export JSON"));
      await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
      request.mockResolvedValueOnce({ ok: true, json: canonical });
      fireEvent.click(screen.getByText("Export CSV"));
      await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
      expect(save.mock.calls.map(([, name]) => name)).toEqual(["mathematics-integral.json", "mathematics-integral.csv"]);
      expect(await save.mock.calls[0][0].text()).toBe(canonical);
      expect(await save.mock.calls[1][0].text()).toBe("x,y\r\n0,1\r\n2,8\r\n10,3");
      expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
        "science_mathematics", "export_result", { node_id: "target", expected_run: "current" },
      ]);
    } finally { save.mockRestore(); }
  });

  it("copies methods returned from the verified export intent, not from the browser result", async () => {
    const oldClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    try {
      const { request } = mount(JSON.stringify({ ...state, latest: result, runToken: "current" }));
      request.mockResolvedValueOnce({ ok: true, markdown: "## Verified methods\nReceipt: abc\n" });
      expandResult("Actions & exports");
      fireEvent.click(screen.getByRole("button", { name: "Copy methods" }));
      await waitFor(() => expect(writeText).toHaveBeenCalledWith("## Verified methods\nReceipt: abc\n"));
      expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
        "science_mathematics", "export_methods", { node_id: "target", expected_run: "current" },
      ]);
      expect(screen.getByRole("button", { name: "Copied methods" })).toBeInTheDocument();
    } finally {
      if (oldClipboard) Object.defineProperty(navigator, "clipboard", oldClipboard);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
  });

  it("copies nothing when the saved result changed before methods verification", async () => {
    const oldClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    try {
      const { request } = mount(JSON.stringify({ ...state, latest: result, runToken: "before" }));
      request.mockResolvedValueOnce({ ok: false, conflict: true, error: "The result changed." });
      expandResult("Actions & exports");
      fireEvent.click(screen.getByRole("button", { name: "Copy methods" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("The result changed.");
      expect(writeText).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Copy methods" })).toBeEnabled();
    } finally {
      if (oldClipboard) Object.defineProperty(navigator, "clipboard", oldClipboard);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
  });

  it("formats scientific summaries and plot captions without modifying stored values", () => {
    const scientific = { ...result,
      summary: "2e+06 viable cells/mL; kcat/Km=1e+06 M⁻¹ s⁻¹.",
      plot: { ...result.plot!, x: [0, 88.81371402318987], y: [5.895064056600376, 88.81371402318987] },
    };
    mount(JSON.stringify({ ...state, latest: scientific }));
    expect(screen.getByRole("status"))
      .toHaveTextContent("2,000,000 viable cells/mL; kcat/Km=1,000,000 M⁻¹ s⁻¹.");
    expandResult("Plots & views");
    expect(screen.getByText("Length (m) · 5.89506 to 88.8137")).toBeVisible();
    expect(screen.getByText("Time (s) · 0 to 88.8137")).toBeVisible();
    expect(screen.getByRole("img").querySelector("circle title"))
      .toHaveTextContent("0, 5.895064056600376");
    expect(readScienceState(JSON.stringify({ ...state, latest: scientific }))?.latest?.summary)
      .toBe(scientific.summary);
  });

  it("groups plain counts and displays Fick-scale values consistently without touching results", () => {
    const scientific = { ...result,
      summary: "500000 CFU/mL; 100000 person-years; Fick flux 0.000002 mol/(m²·s); net 4e-08 mol.",
      values: { voltage: 58.3279090977, population: 500000, samples: [500000, 4e-8] },
      table: { columns: ["Time (ms)", "Membrane potential (mV)"],
        rows: [[0, 58.3279090977], [1, 500000]] },
    };
    mount(JSON.stringify({ ...state, latest: scientific }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "500,000 CFU/mL; 100,000 person-years; Fick flux 2 × 10⁻⁶ mol/(m²·s); net 4 × 10⁻⁸ mol.");
    expandResult(/All values · 3/);
    expect(screen.getByText("[500,000, 4 × 10⁻⁸]")).toBeVisible();
    expandResult("Tables · 2 rows");
    const table = screen.getByRole("table", { name: "Result table" });
    expect(within(table).getByText("58.3279")).toBeVisible();
    expect(within(table).getByRole("columnheader", { name: "Time (ms)" })).toBeVisible();
    expect(table.closest(".science-table")).toBeInTheDocument();
    expect(readScienceState(JSON.stringify({ ...state, latest: scientific }))?.latest?.summary)
      .toBe(scientific.summary);
  });

  it("keeps a wide Hodgkin–Huxley table inside the scrollable preview", () => {
    const columns = ["Time (ms)", "Membrane potential (mV)", "Sodium activation (m)",
      "Sodium inactivation (h)", "Potassium activation (n)", "Sodium current (µA/cm²)",
      "Potassium current (µA/cm²)", "Leak current (µA/cm²)"];
    mount(JSON.stringify({ ...state, latest: { ...result,
      table: { columns, rows: [columns.map((_name, i) => i + .123456789)] },
    } }));
    expandResult("Tables · 1 row");
    const table = screen.getByRole("table", { name: "Result table" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(table.closest(".science-table")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Time (ms)" })).toBeVisible();
  });

  it("retains the connected source label when a reopened parent has a new session id", () => {
    const reference = { nodeId: "prior-session-parent", sourceKey: "persistent-key", resultHash: "abc", selector: ["column", 1] };
    mount(JSON.stringify({ ...state, latest: { ...result, provenance: {
      ...result.provenance, sources: { samples: reference },
    } } }), [{ ...parent, raw: JSON.stringify({ ...state, nodeKey: "persistent-key", latest: result }) }]);
    expect(screen.getByText("Source · Samples · y")).toBeVisible();
  });

  it("shows the server's recovery instructions when a saved result asset is missing", () => {
    mount(JSON.stringify({ ...state, operations: [], error: "Restore the missing result asset." }));
    expect(screen.getByRole("alert")).toHaveTextContent("Restore the missing result asset.");
    expect(screen.queryByText("Run calculation")).toBeNull();
  });

  it("focuses the invalid field and links its description, unit, and inline error", async () => {
    const { request } = mount();
    const field = screen.getByRole("textbox", { name: "Samples (m)" });
    fireEvent.change(field, { target: { value: "[null]" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(field).toHaveFocus();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).not.toHaveAccessibleDescription(/Ordered observations.*array of numbers/);
    fireEvent.click(screen.getByRole("button", { name: "Input and unit help for Samples" }));
    expect(field).toHaveAccessibleDescription(/Ordered observations.*array of numbers/);
    expect(request).not.toHaveBeenCalled();
  });

  it("keeps typed inputs when a remote run arrives and sends its new token after review", async () => {
    const { request, refresh } = mount(JSON.stringify({ ...state, runToken: "a" }));
    const field = screen.getByRole("textbox", { name: "Samples (m)" });
    fireEvent.change(field, { target: { value: "[4,5]" } });
    refresh(JSON.stringify({ ...state, latest: result, runToken: "b" }));
    expect(field).toHaveValue("[4,5]");
    expect(screen.getByText("Run calculation")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Keep my inputs" }));
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({ expected_run: "b", inputs: { samples: [4, 5] } });
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
    fireEvent.change(field, { target: { value: "[7]" } });
    refresh(JSON.stringify({ ...state, latest: result, runToken: "c" }));
    fireEvent.click(screen.getByRole("button", { name: "Load saved inputs" }));
    expect(field).toHaveValue("[10,20,30]");
  });

  it("rebases a clean editor on undo when equal numerical results had different sources", async () => {
    const sources = { samples: { nodeId: "parent", resultHash: "abc", receiptHash: "receipt-1", selector: ["column", 1] } };
    const parentWithReceipt = { ...parent, raw: JSON.stringify({ ...state, latest: {
      ...result, provenance: { ...result.provenance, receiptHash: "receipt-1" },
    } }) };
    const { request, refresh } = mount(JSON.stringify({ ...state, latest: {
      ...result, inputs: { samples: [1, 8, 3] }, provenance: { ...result.provenance, sources },
    }, runToken: "sourced" }), [parentWithReceipt]);
    expect(screen.getByText("Source · Samples · y")).toBeVisible();
    refresh(JSON.stringify({ ...state, latest: {
      ...result, inputs: { samples: [1, 8, 3] },
    }, runToken: "manual" }), [parentWithReceipt]);
    expect(screen.queryByText("Source · Samples · y")).toBeNull();
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({ expected_run: "manual", sources: {} });
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
  });

  it("recognizes stale receipt hashes even for identical numerical output and allows a manual input", () => {
    const sources = { samples: { nodeId: "parent", resultHash: "abc", receiptHash: "older", selector: ["column", 1] } };
    const { request, refresh } = mount(JSON.stringify({ ...state, latest: {
      ...result, provenance: { ...result.provenance, sources },
    } }), [parent]);
    expect(screen.getByText("Run calculation")).toBeDisabled();
    const field = screen.getByRole("textbox", { name: "Samples (m)" });
    expect(field).toHaveAccessibleDescription(/Result changed or disconnected/);
    fireEvent.change(field, { target: { value: "[4,5]" } });
    expect(screen.getByText("Run calculation")).toBeEnabled();
    refresh("");
    expect(screen.queryByText("Run calculation")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Enable Mathematics");
    expect(request).not.toHaveBeenCalled();
  });

  it("downloads no bytes when the export receipt changed before the server answered", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const { request } = mount(JSON.stringify({ ...state, latest: result, runToken: "before" }));
      request.mockResolvedValueOnce({ ok: false, error: "The run changed." });
      expandResult("Actions & exports");
      fireEvent.click(screen.getByText("Export JSON"));
      expect(await screen.findByRole("alert")).toHaveTextContent("The run changed.");
      expect(save).not.toHaveBeenCalled();
    } finally { save.mockRestore(); }
  });

  it("renders finite true endpoints near binary64 extremes and distinguishes adjacent coordinates", () => {
    const latest = { ...result, plot: { ...result.plot!, x: [-Number.MAX_VALUE, 0, Number.MAX_VALUE],
      y: [1, 1.0000000000000002, 1] } };
    mount(JSON.stringify({ ...state, latest }));
    expandResult("Plots & views");
    expect(screen.getByRole("img").querySelector("polyline")).toHaveAttribute("points", "10,98 160,8 310,98");
    expect(screen.getByText("Length (m) · 1 to 1.0000000000000002")).toBeVisible();
    expect(screen.getByText("Time (s) · -1.79769 × 10³⁰⁸ to 1.79769 × 10³⁰⁸")).toBeVisible();
  });

  it("refuses inline CSV when the server has advanced past the visible receipt", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const { request } = mount(JSON.stringify({ ...state, latest: result }));
      request.mockResolvedValueOnce({ ok: true, json: JSON.stringify({ ...result, provenance: { ...result.provenance, receiptHash: "new" } }) });
      expandResult("Actions & exports");
      fireEvent.click(screen.getByText("Export CSV"));
      expect(await screen.findByRole("alert")).toHaveTextContent("result changed");
      expect(save).not.toHaveBeenCalled();
    } finally { save.mockRestore(); }
  });

  it("exports every result row, escapes CSV cells and retains small signed values beyond the preview", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const rows = Array.from({ length: 42 }, (_, i) => [i === 41 ? -Number.MIN_VALUE : i, i === 41 ? 'a,"b"' : "=formula"]);
      const saved = { ...result, table: { columns: ["Measurement", "Label"], rows } };
      const { request } = mount(JSON.stringify({ ...state, latest: saved }));
      request.mockResolvedValueOnce({ ok: true, json: JSON.stringify(saved) });
      expandResult("Tables · 42 rows");
      expect(screen.getAllByRole("row")).toHaveLength(31);
      expandResult("Actions & exports");
      fireEvent.click(screen.getByText("Export CSV"));
      await waitFor(() => expect(save).toHaveBeenCalledOnce());
      const csv = await save.mock.calls[0][0].text();
      expect(csv.split("\r\n")).toHaveLength(43);
      expect(csv).toContain("0,'=formula");
      expect(csv).toContain('-5e-324,"a,""b"""');
    } finally { save.mockRestore(); }
  });

  it("keeps each operation’s draft and restores it after the canvas unmounts the card", () => {
    const current = JSON.stringify({ ...state, runToken: "a", nodeKey: "saved-node" });
    const { remount } = mount(current);
    fireEvent.change(screen.getByLabelText("Samples (m)"), { target: { value: "[4,5]" } });
    fireEvent.change(screen.getByLabelText("Operation"), { target: { value: "scale" } });
    fireEvent.change(screen.getByLabelText("Length (m)"), { target: { value: "7" } });
    remount(current);
    expect(screen.getByLabelText("Operation")).toHaveValue("scale");
    expect(screen.getByLabelText("Length (m)")).toHaveValue("7");
    fireEvent.change(screen.getByLabelText("Operation"), { target: { value: "integral" } });
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[4,5]");
  });

  it("never applies draft text to another node reusing the same session id", () => {
    const { refresh } = mount(JSON.stringify({ ...state, nodeKey: "first" }));
    fireEvent.change(screen.getByLabelText("Samples (m)"), { target: { value: "[4,5]" } });
    refresh(JSON.stringify({ ...state, nodeKey: "second" }));
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[1,2,3]");
  });

  it("reconciles a lost start acknowledgement after remount and cancels only that attempt", async () => {
    const base = { ...state, runToken: "last", nodeKey: "same-node", attemptProtocol: 1 };
    const { request, remount } = mount(JSON.stringify(base));
    const seen: string[] = [];
    let id = "", stopped = false;
    const view = (status: string) => ({ attemptId: id, operationId: "integral", status, startedAtMs: Date.now(),
      ...(status === "cancelled" && { error: "Stopped before commit.", errorType: "cancelled", elapsedMs: 42 }) });
    request.mockImplementation(async (_topic, _intent, args) => {
      const [, action, input] = args as [string, string, { attempt_id: string }];
      seen.push(action);
      if (action === "start_attempt") {
        id = input.attempt_id;
        throw new WsTimeoutError("Lost acknowledgement");
      }
      expect(input.attempt_id).toBe(id);
      if (action === "cancel_attempt") stopped = true;
      return { ok: true, attempt: view(stopped ? "cancelled" : "running") };
    });
    fireEvent.change(screen.getByLabelText("Samples (m)"), { target: { value: "[7,8]" } });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    expect(await screen.findByText("Checking run status…")).toBeVisible();
    remount(JSON.stringify({ ...base, attempt: view("running") }));
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[7,8]");
    expect(screen.getByRole("button", { name: "Run calculation" })).toBeDisabled();
    await waitFor(() => expect(seen).toContain("status_attempt"));
    expect(await screen.findByText("Running Integrate samples…")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Cancel calculation" }));
    await waitFor(() => expect(seen).toContain("cancel_attempt"));
    remount(JSON.stringify({ ...base, attempt: view("cancelled") }));
    expect(await screen.findByText("Stopped before commit.")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("button", { name: "Run calculation" })).toBeEnabled(), { timeout: 3000 });
    expect(seen.filter((name) => name === "start_attempt")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Cancel calculation" })).toBeNull();
  });

  it("keeps older synchronous hosts usable while limiting Stop to hosts with attempt controls", async () => {
    const { request } = mount();
    let finish: (value: unknown) => void = () => {};
    request.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    expect(screen.getByRole("button", { name: "Run calculation" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Cancel calculation" })).toBeNull();
    finish({ ok: true });
    await waitFor(() => expect(screen.getByRole("button", { name: "Run calculation" })).toBeEnabled());
  });

  it("shows a typed timed-out run without replacing the last successful result", async () => {
    const { request } = mount(JSON.stringify({ ...state, latest: result }));
    request.mockResolvedValueOnce({
      ok: false, errorType: "timeout", elapsedMs: 500,
      error: "Scientific calculation exceeded its 0.5 s budget (500 ms elapsed).",
    });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("500 ms elapsed");
    expect(screen.getByText("Computed 3 samples.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run calculation" })).toBeEnabled();
  });

  it("rejects damaged or unsupported state without rendering a run button", () => {
    mount(JSON.stringify({ ...state, scienceVersion: 2 }));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("Run calculation")).toBeNull();
    expect(readScienceState(JSON.stringify({ ...state, latest: { ...result, plot: { ...result.plot, y: [] } } }))).toBeNull();
  });

  it("renders 96- and 384-well typed plate maps with accessible empty, zero and categorical wells", () => {
    const plate = { type: "plate_map", title: "Plate readings", format: 96, unit: "RFU",
      wells: [{ well: "A1", value: "control" }, { well: "A2", value: 0 }, { well: "H12", value: 12.4 }] };
    const mounted = mount(JSON.stringify({ ...state, latest: { ...result, views: [plate] } }));
    expandResult("Plots & views");
    const map = screen.getByRole("table", { name: "96-well plate map" });
    expect(within(map).getAllByRole("row")).toHaveLength(9);
    expect(within(map).getByRole("cell", { name: "A1: control" })).toHaveTextContent("co");
    expect(within(map).getByRole("cell", { name: "A2: 0 RFU" })).toHaveTextContent("●");
    expect(within(map).getByRole("cell", { name: "A3: empty" })).toHaveTextContent("·");
    expect(within(map).getByRole("cell", { name: "H12: 12.4 RFU" })).toBeInTheDocument();
    expect(screen.getByText(/Numeric scale: 0 – 12.4 RFU/)).toBeVisible();

    mounted.remount(JSON.stringify({ ...state, latest: { ...result, views: [
      { ...plate, format: 384, wells: [{ well: "P24", value: 5 }] },
    ] } }));
    expandResult("Plots & views");
    const large = screen.getByRole("table", { name: "384-well plate map" });
    expect(within(large).getAllByRole("row")).toHaveLength(17);
    const first = within(large).getByRole("button", { name: "A1: empty" });
    const last = within(large).getByRole("button", { name: "P24: 5 RFU" });
    expect(large.querySelectorAll('button[tabindex="0"]')).toHaveLength(1);
    fireEvent.keyDown(first, { key: "End", ctrlKey: true });
    expect(last).toHaveFocus();
    expect(last).toHaveAttribute("tabindex", "0");
    expect(screen.getByText("P24", { selector: ".science-well-readout strong" }).parentElement).toHaveTextContent("P24 · 5 RFU");
    fireEvent.keyDown(last, { key: "ArrowLeft" });
    expect(within(large).getByRole("button", { name: "P23: empty" })).toHaveFocus();
    fireEvent.click(first);
    expect(first).toHaveAttribute("tabindex", "0");
  });

  it("renders all survival groups as step curves with censor ticks and a legend", () => {
    const survival = { type: "survival_curve", title: "Kaplan–Meier by arm",
      xLabel: "Follow-up (days)", groups: [
        { label: "A", times: [0, 2, 4], survival: [1, .75, .5], censored: [4] },
        { label: "B", times: [0, 1, 4], survival: [1, .9, .9], censored: [4] },
        { label: "C", times: [0, 3], survival: [1, .8] },
      ] };
    mount(JSON.stringify({ ...state, latest: { ...result, views: [survival] } }));
    expandResult("Plots & views");
    expandResult("Tables · 3 rows");
    const svg = screen.getByRole("img", { name: /Kaplan–Meier by arm: survival curves for A, B, C/ });
    expect(svg.querySelectorAll("path")).toHaveLength(4); // Axes + one step path for each group.
    expect([...svg.querySelectorAll("path")].slice(1).every((path) =>
      /H.*V/.test(path.getAttribute("d") ?? ""))).toBe(true);
    expect(svg.querySelectorAll("line[stroke-width='2']")).toHaveLength(2);
    expect(screen.getByText("C · 0 censor times")).toBeVisible();
    const keys = [...document.querySelectorAll(".science-survival-key line")];
    expect(keys.map(key => key.getAttribute("stroke-dasharray")))
      .toEqual([...svg.querySelectorAll("g path")].map(curve => curve.getAttribute("stroke-dasharray")));
    fireEvent.click(screen.getByText("Survival curve data"));
    expect(within(screen.getByRole("table", { name: "Survival curve data" })).getByRole("row", { name: "A 4 0.5 Yes" })).toBeVisible();
    // The legacy single-series plot is retained for connections, not displayed twice.
    expect(screen.queryByRole("img", { name: /Distance/ })).toBeNull();
    expect(screen.getByRole("table", { name: "Result table" })).toBeInTheDocument();
  });

  it("renders forest estimates and whiskers, a pooled diamond and linear or log reference axes", () => {
    const forest = { type: "forest_plot", title: "Treatment effects", xLabel: "Mean difference",
      scale: "linear", reference: 0, studies: [
        { label: "Study Alpha", estimate: -.2, lower: -.5, upper: .1, weight: 4 },
        { label: "Study Beta", estimate: .4, lower: .2, upper: .8, weight: 1 },
      ],
      pooled: { label: "Pooled effect", estimate: .1, lower: -.1, upper: .3 },
    };
    const mounted = mount(JSON.stringify({ ...state, latest: { ...result, views: [forest] } }));
    expandResult("Plots & views");
    const svg = screen.getByRole("img", { name: /Treatment effects: forest plot with 2 studies.*linear/ });
    expect(svg.querySelectorAll(".science-forest-study")).toHaveLength(2);
    expect(svg.querySelectorAll(".science-forest-study rect")).toHaveLength(2);
    expect(svg.querySelector(".science-forest-diamond")).toHaveAttribute("points", expect.stringContaining(","));
    expect(svg.querySelector(".science-forest-reference")).toHaveAttribute("stroke-dasharray", "4 4");
    expect(within(svg).getByText("Pooled effect")).toBeVisible();
    fireEvent.click(screen.getByText("Exact estimates and confidence intervals"));
    const data = screen.getByRole("table", { name: "Treatment effects estimate data" });
    expect(within(data).getByRole("row", { name: /Study Alpha/ })).toHaveTextContent("-0.5");
    expect(within(data).getByRole("row", { name: /Pooled effect/ })).toHaveTextContent("0.3");

    mounted.remount(JSON.stringify({ ...state, latest: { ...result, views: [{
      ...forest, scale: "log", reference: 1, xLabel: "Risk ratio",
      studies: [
        { label: "Study Alpha", estimate: .8, lower: .6, upper: 1.1 },
        { label: "Study Beta", estimate: 1.4, lower: 1.1, upper: 1.8 },
      ],
      pooled: { estimate: 1.05, lower: .9, upper: 1.2 },
    }] } }));
    expandResult("Plots & views");
    const logPlot = screen.getByRole("img", { name: /forest plot with 2 studies.*log axis/ });
    const squares = [...logPlot.querySelectorAll(".science-forest-study rect")];
    expect(squares[0].getAttribute("x")).not.toBe(squares[1].getAttribute("x"));
    expect(screen.getByText(/Risk ratio \(log\)/)).toBeVisible();
  });

  it("renders rectangular phylograms and circular cladograms with branch lengths and support", () => {
    const phylogeny = { type: "phylogenetic_tree", title: "Three-taxon tree",
      layout: "rectangular", mode: "phylogram",
      tree: { children: [
        { length: .3, support: 95, children: [
          { name: "Species A", length: .2 }, { name: "Species B", length: .1 },
        ] },
        { name: "Species C", length: .8, support: 85 },
      ] },
    };
    const mounted = mount(JSON.stringify({ ...state, latest: { ...result, views: [phylogeny] } }));
    expandResult("Plots & views");
    const rectangular = screen.getByRole("img", { name: /Three-taxon tree: rectangular phylogram of 3 taxa/ });
    expect(rectangular.querySelectorAll("line.science-tree-branch")).toHaveLength(6);
    expect(rectangular.textContent).toContain("Species A");
    expect(rectangular.textContent).toContain("S 95");
    fireEvent.click(screen.getByText(/Branch lengths and support labels/));
    const table = screen.getByRole("table", { name: "Three-taxon tree branch data" });
    expect(within(table).getByRole("row", { name: /Species C/ })).toHaveTextContent("0.8");
    expect(within(table).getByRole("row", { name: /Species C/ })).toHaveTextContent("85");

    mounted.remount(JSON.stringify({ ...state, latest: { ...result, views: [{
      ...phylogeny, layout: "circular", mode: "cladogram",
    }] } }));
    expandResult("Plots & views");
    const circular = screen.getByRole("img", { name: /Three-taxon tree: circular cladogram of 3 taxa/ });
    expect(circular.querySelectorAll("path.science-tree-branch").length).toBeGreaterThan(0);
    expect(circular.querySelectorAll("line.science-tree-branch")).toHaveLength(4);
    expect(circular.textContent).toContain("Species B");
    expect(screen.getByText(/lengths do not determine geometry/)).toBeVisible();
  });

});


describe("typed table import and numeric selections", () => {
  const meta = {
    version: 1, asset_ref: "a".repeat(64), mime_type: "application/json", sourceName: "samples.tsv",
    rowCount: 3, columnCount: 2, previewColumns: 2,
    columns: [
      { name: "Temperature", label: "Temperature (degC)", unit: "degC", type: "numeric", missingCount: 1 },
      { name: "Station", label: "Station", unit: "", type: "text", missingCount: 0 },
    ],
    preview: [[10, "A"], [null, "B"], [20, "C"]],
    provenance: { fileHash: "b".repeat(64), tableHash: "a".repeat(64),
      receiptHash: "c".repeat(64), parseOptions: { format: "tsv", missingValues: ["", "NA"] } },
  };

  it("connects an existing dataset upstream and retains the selection after a rejected connection", async () => {
    const request = vi.fn<WsTransport["request"]>().mockResolvedValueOnce(null).mockResolvedValueOnce("edge");
    const store = new SceneStore({ request } as unknown as WsTransport);
    const source = { title: "Measurement source", kind: "science_mathematics.workbench", x: 0, y: 0,
      pluginState: { science: JSON.stringify({ ...state, table: meta }) } };
    const snapshot = validateSceneState({ ...initialSceneState, nodes: [
      { ...source, id: "source" }, { ...source, id: "target" },
      { ...source, id: "disabled", kind: "placeholder" },
    ] });
    if (!snapshot.ok) throw new Error(snapshot.errors.join(", "));
    vi.spyOn(store, "getScene").mockReturnValue(snapshot.value);
    render(<ScienceTableSource store={store} packTitle="Mathematics" targetId="target" renderImporter={() => null} />);
    expect(screen.getByLabelText("Source workbench")).toHaveValue("source");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Connect selected source" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("connection was not confirmed");
    expect(screen.getByLabelText("Source workbench")).toHaveValue("source");
    fireEvent.click(screen.getByRole("button", { name: "Connect selected source" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(request).toHaveBeenCalledTimes(2);
    expect(request).toHaveBeenNthCalledWith(1, "scene", "connectNodes", ["source", "target"]);
    expect(request).toHaveBeenNthCalledWith(2, "scene", "connectNodes", ["source", "target"]);
  });

  it("shows a recovery error rather than crashing on a damaged saved table preview", () => {
    mount(JSON.stringify({ ...state, table: { ...meta, preview: [null] } }));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
  });

  it("enforces the UTF-8 byte cap on pasted tables before sending an intent", async () => {
    const { request } = mount(JSON.stringify({ ...state, limits: {
      points: 1024, columns: 32, cells: 4096, text: 65536, tableSourceBytes: 5,
    } }));
    openImporter();
    fireEvent.change(screen.getByLabelText("Pasted table text"), { target: { value: "x\n😀" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("exceeds the import size limit");
    expect(request).not.toHaveBeenCalled();
  });

  it("imports pasted TSV with explicit missing markers and type overrides", async () => {
    const { request } = mount();
    openImporter();
    fireEvent.change(screen.getByLabelText("Table format"), { target: { value: "tsv" } });
    fireEvent.change(screen.getByLabelText("Pasted table text"), { target: { value: "X\tFlag\n1\ttrue" } });
    fireEvent.change(screen.getByLabelText("Missing-value markers"), { target: { value: "NA,missing" } });
    fireEvent.click(screen.getByText("Advanced column overrides"));
    fireEvent.change(screen.getByLabelText("Column type overrides"), { target: { value: '{"Flag":"boolean"}' } });
    request.mockResolvedValueOnce({ ok: true, table: meta, runToken: "reviewed-token" });
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
      "science_mathematics", "preview_table", {
        node_id: "target", format: "tsv", text: "X\tFlag\n1\ttrue",
        missing_values: ["", "NA", "missing"], column_types: { Flag: "boolean" },
      },
    ], 120_000));
    expect(await screen.findByRole("table", { name: "Pending import preview" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Apply reviewed table" }));
    await waitFor(() => expect(request).toHaveBeenLastCalledWith("app-plugins", "invokePluginIntent", [
      "science_mathematics", "import_table", expect.objectContaining({
        expected_run: "reviewed-token", expected_preview: meta.provenance.receiptHash,
        format: "tsv", text: "X\tFlag\n1\ttrue", column_types: { Flag: "boolean" },
      }),
    ], 120_000));
  });

  it("requires another preview after editing a reviewed column type", async () => {
    const { request } = mount();
    openImporter();
    request.mockResolvedValueOnce({ ok: true, table: meta, runToken: "reviewed-token" });
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    await screen.findByRole("button", { name: "Apply reviewed table" });
    fireEvent.change(screen.getByRole("combobox", { name: "Type for Station" }), { target: { value: "categorical" } });
    expect(screen.queryByRole("button", { name: "Apply reviewed table" })).toBeNull();
    expect(screen.getByRole("button", { name: "Preview table" })).toBeEnabled();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("imports file bytes rather than decoding and repasting CSV into the field", async () => {
    const { request } = mount();
    openImporter();
    fireEvent.change(screen.getByLabelText("Table data source"), { target: { value: "file" } });
    const file = new File(["Time (s)\tValue\n0\t1"], "samples.tsv", { type: "text/tab-separated-values" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new TextEncoder().encode("Time (s)\tValue\n0\t1").buffer });
    fireEvent.change(screen.getByLabelText("CSV or TSV file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const args = request.mock.lastCall?.[2]?.[2] as Record<string, unknown>;
    expect(request.mock.lastCall?.[2]?.[1]).toBe("preview_table");
    expect(args.format).toBe("tsv");
    expect(args.filename).toBe("samples.tsv");
    expect(atob(args.encoded_file as string)).toBe("Time (s)\tValue\n0\t1");
    expect(args.text).toBeUndefined();
  });

  it("clears a selected file when the importer closes and requires a new visible selection", async () => {
    const { request } = mount();
    openImporter();
    fireEvent.change(screen.getByLabelText("Table data source"), { target: { value: "file" } });
    const oldFile = new File(["X\n1"], "old.csv", { type: "text/csv" });
    const oldInput = screen.getByLabelText("CSV or TSV file") as HTMLInputElement;
    fireEvent.change(oldInput, { target: { files: [oldFile] } });
    expect(oldInput.files?.[0]).toBe(oldFile);

    openImporter();
    expect(screen.queryByLabelText("CSV or TSV file")).not.toBeInTheDocument();
    openImporter();
    const newInput = screen.getByLabelText("CSV or TSV file") as HTMLInputElement;
    expect(newInput).not.toBe(oldInput);
    expect(newInput.files).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a CSV or TSV file.");
    expect(request).not.toHaveBeenCalled();

    const freshFile = new File(["X\n2"], "fresh.csv", { type: "text/csv" });
    Object.defineProperty(freshFile, "arrayBuffer", { value: async () => new TextEncoder().encode("X\n2").buffer });
    fireEvent.change(newInput, { target: { files: [freshFile] } });
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    await waitFor(() => expect(request).toHaveBeenCalledOnce());
    const args = request.mock.lastCall?.[2]?.[2] as Record<string, unknown>;
    expect(args.filename).toBe("fresh.csv");
    expect(atob(args.encoded_file as string)).toBe("X\n2");
  });

  it("clears a file selection when switching between file and paste sources", async () => {
    const { request } = mount();
    openImporter();
    fireEvent.change(screen.getByLabelText("Table data source"), { target: { value: "file" } });
    fireEvent.change(screen.getByLabelText("CSV or TSV file"), {
      target: { files: [new File(["X\n1"], "hidden.csv", { type: "text/csv" })] },
    });
    fireEvent.change(screen.getByLabelText("Table data source"), { target: { value: "paste" } });
    fireEvent.change(screen.getByLabelText("Table data source"), { target: { value: "file" } });
    expect((screen.getByLabelText("CSV or TSV file") as HTMLInputElement).files).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Preview table" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a CSV or TSV file.");
    expect(request).not.toHaveBeenCalled();
  });

  it("renders typed previews and selects a numeric column only after an explicit missing-value choice", async () => {
    const parentTable = { nodeId: "parent", title: "Samples", raw: JSON.stringify({
      ...state, nodeKey: "d".repeat(32), latest: null, table: meta,
    }) };
    const { request } = mount(JSON.stringify({ ...state, table: meta }), [parentTable]);
    fireEvent.click(screen.getByText("Table preview and export"));
    expect(screen.getByRole("table", { name: "Imported typed table preview" })).toHaveTextContent("Temperature (degC)");
    expect(screen.getByText("numeric · 1 missing")).toBeVisible();
    expect(screen.getByText(/samples.tsv · 3 rows · 2 columns/)).toBeVisible();

    const select = screen.getByRole("combobox", { name: "Import Samples from a connected result" }) as HTMLSelectElement;
    const opts = Array.from(select.options);
    const reject = opts.find((op) => op.text.includes("Reject missing"))!;
    const drop = opts.find((op) => op.text.includes("Drop missing (explicit)"))!;
    expect(reject).toBeDefined();
    expect(drop).toBeDefined();
    request.mockResolvedValueOnce({ ok: false, error: "This column contains missing values." });
    fireEvent.change(select, { target: { value: reject.value } });
    expect(await screen.findByRole("alert")).toHaveTextContent("missing values");
    const reference = {
      nodeId: "parent", sourceKey: "d".repeat(32), resultHash: meta.asset_ref,
      receiptHash: meta.provenance.receiptHash, selector: ["table_column", 0, "drop", 0, 3],
    };
    let finishColumn: (value: unknown) => void = () => {};
    request.mockReturnValueOnce(new Promise((resolve) => { finishColumn = resolve; }));
    fireEvent.change(select, { target: { value: drop.value } });
    expect(screen.getByRole("button", { name: "Run calculation" })).toBeDisabled();
    expect(screen.getByText("Loading selected table column…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel calculation" })).toBeNull();
    finishColumn({ ok: true, values: [10, 20], reference });
    await waitFor(() => expect(screen.getByLabelText("Samples (m)")).toHaveValue("[10,20]"));
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request.mock.lastCall?.[2]?.[1]).toBe("run"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      inputs: { samples: [10, 20] }, sources: { samples: reference },
    });
  });

  it("reviews paired columns in an editable window before atomically applying verified inputs", async () => {
    const paired = {
      ...operation, fields: [operation.fields[0], { ...operation.fields[0], key: "other", label: "Other" }],
      example: { samples: [1, 2, 3], other: [4, 5, 6] }, pairedFields: [["samples", "other"]],
    };
    const table = { ...meta, columns: [
      { ...meta.columns[0], label: "A (mm)", unit: "mm" }, { ...meta.columns[0], label: "B", unit: "" },
    ] };
    const upstream = { nodeId: "parent", title: "Observations", raw: JSON.stringify({
      ...state, nodeKey: "d".repeat(32), table, latest: null,
    }) };
    const raw = JSON.stringify({ ...state, operations: [paired] });
    const { request, remount } = mount(raw, [upstream]);
    fireEvent.click(screen.getByText("Bind table columns"));
    fireEvent.change(await screen.findByLabelText("Column for Samples"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Column for Other"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Source unit assumption for Other"), { target: { value: "mm" } });
    fireEvent.change(screen.getByLabelText("First data row"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Missing observations"), { target: { value: "complete" } });
    const choices = [0, 1].map((idx) => ({
      nodeId: "parent", sourceKey: "d".repeat(32), resultHash: table.asset_ref,
      receiptHash: table.provenance.receiptHash, selector: ["table_column", idx, "drop", 1, 3],
      binding: { sourceUnit: "mm", targetUnit: "m", retained: 1, omitted: 1, rowDigest: "b".repeat(64),
        columnLabel: table.columns[idx].label, completeColumns: [0, 1], ...(idx === 1 && { assumedUnit: "mm" }) },
    }));
    for (const reference of choices) request.mockResolvedValueOnce({ ok: true, values: [12.5],
      inputs: ["12.5 mm"], normalizedPreview: [.0125], reference });
    fireEvent.click(screen.getByRole("button", { name: "Preview bindings" }));
    const apply = await screen.findByRole("button", { name: "Apply reviewed bindings" });
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[1,2,3]");
    expect(screen.getByRole("region", { name: "Verified binding preview" })).toHaveTextContent("0.0125");
    expect(request.mock.calls[1][2]?.[2]).toMatchObject({ start: 1, end: 3, policy: "drop", complete_columns: [0, 1], assumed_unit: "mm" });
    fireEvent.click(apply);
    remount(raw);
    expect(screen.getByLabelText("Samples (m)")).toHaveValue('["12.5 mm"]');
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      inputs: { samples: ["12.5 mm"], other: ["12.5 mm"] }, sources: { samples: choices[0], other: choices[1] },
    }));
  });

  it("binds a complete typed table and selects text and numeric group fields in the form", async () => {
    const tableOp = {
      id: "grouped_table_summary", title: "Summarize a typed table by group", description: "",
      fields: [
        { key: "table", label: "Typed table", kind: "table" as const, unit: "", description: "" },
        { key: "value_column", label: "Measurement", kind: "column" as const, table_field: "table",
          column_types: ["numeric" as const], unit: "", description: "" },
        { key: "group_column", label: "Group", kind: "column" as const, table_field: "table",
          unit: "", description: "" },
      ], example: { table: null, value_column: null, group_column: null },
    };
    const parentTable = { nodeId: "parent", title: "Samples", raw: JSON.stringify({
      ...state, nodeKey: "d".repeat(32), latest: null, table: meta,
    }) };
    const { request } = mount(JSON.stringify({ ...state, operations: [tableOp] }), [parentTable]);
    const input = screen.getByRole("combobox", { name: "Import Typed table from a connected result" });
    const complete = Array.from((input as HTMLSelectElement).options).find((option) =>
      option.text.includes("Whole typed table"))!;
    expect(complete).toBeDefined();
    fireEvent.change(input, { target: { value: complete.value } });
    expect(screen.getByRole("textbox", { name: "Typed table" }))
      .toHaveValue("Connected typed table selected");
    const measurement = screen.getByRole("combobox", { name: "Measurement" });
    expect(within(measurement).getByRole("option", { name: "Temperature (degC) · numeric" })).toBeVisible();
    expect(within(measurement).queryByRole("option", { name: /Station/ })).toBeNull();
    const group = screen.getByRole("combobox", { name: "Group" });
    expect(within(group).getByRole("option", { name: "Station · text" })).toBeVisible();
    fireEvent.change(measurement, { target: { value: "0" } });
    fireEvent.change(group, { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request.mock.lastCall?.[2]?.[1]).toBe("run"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      inputs: { table: meta, value_column: 0, group_column: 1 },
      sources: { table: { selector: ["typed_table"], resultHash: meta.asset_ref,
        receiptHash: meta.provenance.receiptHash } },
    });
  });
});

describe("unit-aware scientific node inputs and receipts", () => {
  it("labels an absolute temperature distinctly from an interval and keeps mixed columns", () => {
    const temperature = { ...operation2, fields: [{ ...operation2.fields[0],
      label: "Temperature change", key: "temperature_change_k", unit: "K",
      default_unit: "delta_K", dimension: "temperature (K)",
    }], example: { temperature_change_k: 50 } };
    mount(JSON.stringify({ ...state, operations: [temperature] }), []);
    expect(screen.getByRole("textbox", { name: "Temperature change (delta_K)" })).toHaveValue("50");
    expect(screen.queryByText(/Default unit: delta_K/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Input and unit help for Temperature change" }));
    expect(screen.getByText(/Default unit: delta_K/)).toBeVisible();
  });

  it("renders the expected dimension and sends unit-suffixed values for backend conversion", async () => {
    const typed = { ...operation2, fields: [{
      ...operation2.fields[0], default_unit: "m", dimension: "length (m)",
    }] };
    const { request } = mount(JSON.stringify({ ...state, operations: [typed] }), []);
    const input = screen.getByRole("textbox", { name: "Length (m)" });
    expect(input).not.toHaveAccessibleDescription(/Default unit: m.*length \(m\)/);
    fireEvent.click(screen.getByRole("button", { name: "Input and unit help for Length" }));
    expect(input).toHaveAccessibleDescription(/Default unit: m.*length \(m\)/);
    fireEvent.change(input, { target: { value: "25 mm" } });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
      "science_mathematics", "run", {
        node_id: "target", operation_id: "scale", inputs: { length: "25 mm" }, sources: {},
      },
    ], 615_000));
  });

  it("accepts quoted array entries with units and shows the units saved with a result", async () => {
    const { request } = mount(JSON.stringify({ ...state, latest: {
      ...result, provenance: {
        ...result.provenance, inputUnits: { samples: ["mm", "cm", "m"] },
      },
    } }), []);
    fireEvent.click(screen.getByText("Units used"));
    const unitsUsed = screen.getByText("Units used").closest("details");
    expect(within(unitsUsed!).getByText(/"mm"/)).toBeVisible();
    const input = screen.getByRole("textbox", { name: "Samples (m)" });
    fireEvent.change(input, { target: { value: '["25 mm", "2 cm", 3]' } });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      inputs: { samples: ["25 mm", "2 cm", 3] },
    }));
  });
});

describe("explicit Scientific Graph Runner configuration", () => {
  it("saves unit-aware inputs without executing and restores the saved operation", async () => {
    const { request, refresh } = mount(JSON.stringify({ ...state, runToken: "token" }), []);
    const input = screen.getByRole("textbox", { name: "Samples (m)" });
    fireEvent.change(input, { target: { value: '["25 mm",2]' } });
    fireEvent.click(screen.getByRole("button", { name: "Save for Graph Runner" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
      "science_mathematics", "configure_runner", {
        node_id: "target", operation_id: "integral", inputs: { samples: ["25 mm", 2] },
        sources: {}, expected_run: "token",
      },
    ]));
    expect(request.mock.calls.some((args) => args[2]?.[1] === "run")).toBe(false);
    await waitFor(() => expect(screen.getByRole("button", { name: "Save for Graph Runner" })).toBeEnabled());
    refresh(JSON.stringify({ ...state, runToken: "token", runner: {
      version: 1, operationId: "integral", inputs: { samples: ["25 mm", 2] }, sources: {},
    } }));
    expect(screen.getByRole("textbox", { name: "Samples (m)" })).toHaveValue('["25 mm",2]');
    expect(screen.getByText("Saved as an explicit Graph Runner step.")).toBeVisible();
    // Undo drops the persisted configuration; the node view must stop
    // advertising that the operation is still approved for Graph Runner.
    refresh(JSON.stringify({ ...state, runToken: "restored-after-undo", runner: null }));
    expect(screen.queryByText("Saved as an explicit Graph Runner step.")).toBeNull();
  });
});

it("restores a saved scalar unit literal and indicates when the recipe diverges", async () => {
  const { request } = mount(JSON.stringify({ ...state, runner: {
    version: 2, operationId: "scale", inputs: { length: "25 mm" }, sources: {},
    uncertaintyMethod: "monte_carlo",
  } }), []);
  const field = screen.getByRole("textbox", { name: "Length (m)" });
  expect(field).toHaveValue("25 mm");
  expect(screen.getByText("Saved as an explicit Graph Runner step.")).toBeVisible();
  fireEvent.change(field, { target: { value: "30 mm" } });
  expect(screen.getByText("Changes not saved to Graph Runner.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Save for Graph Runner" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
    "science_mathematics", "configure_runner", { node_id: "target", operation_id: "scale",
      inputs: { length: "30 mm" }, sources: {}, uncertainty_method: "monte_carlo" },
  ]));
});
