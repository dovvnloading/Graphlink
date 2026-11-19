import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import type { WsTransport } from "../../lib/ws/transport";
import { SceneStore } from "./sceneStore";
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
  const view = render(draw(raw), { wrapper: ReactFlowProvider });
  return { request, fireIntent, generateChart, remount: (value: string) => { view.unmount(); render(draw(value), { wrapper: ReactFlowProvider }); }, refresh: (value: string, currentParents = parents, currentCharts = charts) => view.rerender(draw(value, currentParents, currentCharts)) };
}

function expandResult(label: string | RegExp) {
  const latest = screen.getByRole("region", { name: "Latest result" });
  fireEvent.click(within(latest).getByText(label));
}

describe("scientific workbench card", () => {
  it("shows one meaningful headline and keeps full data and actions on demand", () => {
    const latestResult: ScienceResult = {
      ...result, summary: "Described 3 observations.", values: { count: 3, mean: 12, spread: [8, 10, 18] },
      provenance: { ...result.provenance, inputUnits: { samples: "mm" } },
    };
    mount(JSON.stringify({ ...state, latest: latestResult }));
    const latest = screen.getByRole("region", { name: "Latest result" });
    const headline = latest.querySelector(".science-result-highlight")!;
    expect(headline).toHaveTextContent("mean");
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
    expect(within(latest).getByText(/"mm"/)).toBeVisible();
    expandResult("Inputs and provenance");
    expect(within(latest).getByText(/"resultHash": "abc"/)).toBeVisible();
    expandResult("Actions & exports");
    expect(within(latest).getByRole("button", { name: "Export JSON" })).toBeVisible();
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
    expect(within(latest).getByText(long)).toBeVisible();
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
    expect(Number.isFinite(Number(down.getAttribute("cy")))).toBe(true);
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
    expect(generateChart).toHaveBeenCalledWith("target", "scatter");
    fireEvent.change(screen.getByRole("combobox", { name: "Chart type" }), { target: { value: "heatmap" } });
    fireEvent.click(screen.getByRole("button", { name: "Chart this" }));
    expect(generateChart).toHaveBeenCalledWith("target", "heatmap");
    expect(screen.getByText("Source changed · refresh to update")).toBeVisible();
    expect(screen.getByText(/Receipt bbbbbbbbbbbb/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh chart" }));
    expect(generateChart).toHaveBeenCalledWith("child-chart", "scatter");
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
      fireEvent.click(screen.getByText("Export CSV"));
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
    expandResult("Tables · 1 rows");
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

  it("exports every result row, escapes CSV cells and retains small signed values beyond the preview", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const rows = Array.from({ length: 42 }, (_, i) => [i === 41 ? -Number.MIN_VALUE : i, i === 41 ? 'a,"b"' : "=formula"]);
      mount(JSON.stringify({ ...state, latest: { ...result, table: { columns: ["Measurement", "Label"], rows } } }));
      expandResult("Tables · 42 rows");
      expect(screen.getAllByRole("row")).toHaveLength(31);
      expandResult("Actions & exports");
      fireEvent.click(screen.getByText("Export CSV"));
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

  it("cancels an in-flight scientific worker without disabling the Cancel control", async () => {
    const { request } = mount();
    let finishRun: (value: unknown) => void = () => {};
    request.mockReturnValueOnce(new Promise((resolve) => { finishRun = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    expect(screen.getByRole("button", { name: "Run calculation" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Running calculation…");
    const cancel = screen.getByRole("button", { name: "Cancel calculation" });
    expect(cancel).toBeEnabled();
    fireEvent.click(cancel);
    expect(request).toHaveBeenNthCalledWith(2, "app-plugins", "invokePluginIntent", [
      "science_mathematics", "cancel", { node_id: "target", operation_id: "integral" },
    ]);
    finishRun({ ok: false, errorType: "cancelled", elapsedMs: 42,
      error: "Scientific calculation cancelled after 42 ms." });
    expect(await screen.findByRole("alert")).toHaveTextContent("cancelled after 42 ms");
    await waitFor(() => expect(screen.getByRole("button", { name: "Run calculation" })).toBeEnabled());
    expect(screen.queryByRole("button", { name: "Cancel calculation" })).toBeNull();
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
    expect(within(large).getByRole("cell", { name: "P24: 5 RFU" })).toBeInTheDocument();
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

  it("shows a recovery error rather than crashing on a damaged saved table preview", () => {
    mount(JSON.stringify({ ...state, table: { ...meta, preview: [null] } }));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
  });

  it("enforces the UTF-8 byte cap on pasted tables before sending an intent", async () => {
    const { request } = mount(JSON.stringify({ ...state, limits: {
      points: 1024, columns: 32, cells: 4096, text: 65536, tableSourceBytes: 5,
    } }));
    fireEvent.change(screen.getByLabelText("Pasted table text"), { target: { value: "x\n😀" } });
    fireEvent.click(screen.getByRole("button", { name: "Import typed table" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("exceeds the import size limit");
    expect(request).not.toHaveBeenCalled();
  });

  it("imports pasted TSV with explicit missing markers and type overrides", async () => {
    const { request } = mount();
    fireEvent.change(screen.getByLabelText("Table format"), { target: { value: "tsv" } });
    fireEvent.change(screen.getByLabelText("Pasted table text"), { target: { value: "X\tFlag\n1\ttrue" } });
    fireEvent.change(screen.getByLabelText("Missing-value markers"), { target: { value: "NA,missing" } });
    fireEvent.change(screen.getByLabelText("Column type overrides"), { target: { value: '{"Flag":"boolean"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Import typed table" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
      "science_mathematics", "import_table", {
        node_id: "target", format: "tsv", text: "X\tFlag\n1\ttrue",
        missing_values: ["", "NA", "missing"], column_types: { Flag: "boolean" },
      },
    ]));
  });

  it("imports file bytes rather than decoding and repasting CSV into the field", async () => {
    const { request } = mount();
    fireEvent.change(screen.getByLabelText("Table data source"), { target: { value: "file" } });
    const file = new File(["Time (s)\tValue\n0\t1"], "samples.tsv", { type: "text/tab-separated-values" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new TextEncoder().encode("Time (s)\tValue\n0\t1").buffer });
    fireEvent.change(screen.getByLabelText("CSV or TSV file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Import typed table" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const args = request.mock.lastCall?.[2]?.[2] as Record<string, unknown>;
    expect(request.mock.lastCall?.[2]?.[1]).toBe("import_table");
    expect(args.format).toBe("tsv");
    expect(args.filename).toBe("samples.tsv");
    expect(atob(args.encoded_file as string)).toBe("Time (s)\tValue\n0\t1");
    expect(args.text).toBeUndefined();
  });

  it("renders typed previews and selects a numeric column only after an explicit missing-value choice", async () => {
    const parentTable = { nodeId: "parent", title: "Samples", raw: JSON.stringify({
      ...state, nodeKey: "d".repeat(32), latest: null, table: meta,
    }) };
    const { request } = mount(JSON.stringify({ ...state, table: meta }), [parentTable]);
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
