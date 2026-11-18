import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  const store = new SceneStore({ request } as unknown as WsTransport);
  const generateChart = vi.spyOn(store, "generateChart").mockImplementation(() => {});
  function draw(value: string, currentParents = parents, currentCharts = charts) {
    const props = { id: "target", data: { title: "Mathematics", raw: value, pluginId: "science_mathematics", parents: currentParents, charts: currentCharts, store } } as NodeProps<ScientificFlowNode>;
    return <ScientificNodeView {...props} />;
  }
  const view = render(draw(raw), { wrapper: ReactFlowProvider });
  return { request, generateChart, remount: (value: string) => { view.unmount(); render(draw(value), { wrapper: ReactFlowProvider }); }, refresh: (value: string, currentParents = parents, currentCharts = charts) => view.rerender(draw(value, currentParents, currentCharts)) };
}

describe("scientific workbench card", () => {
  it("charts a saved XY trace and exposes receipt-aware refresh for its linked chart", () => {
    const charts: ScienceChartLink[] = [{
      id: "child-chart", title: "Distance chart", chartType: "scatter",
      receiptHash: "b".repeat(64), stale: true,
    }];
    const { generateChart } = mount(JSON.stringify({ ...state, latest: result }), [parent], charts);
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
      expect(screen.getByRole("img").querySelectorAll("circle")[1].getAttribute("cx")).toBe("70");
      expect(screen.getByRole("table")).toBeInTheDocument();
      fireEvent.click(screen.getByText("Inputs and provenance"));
      expect(screen.getByText(/"resultHash": "abc"/)).toBeVisible();
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
    expect(screen.getByRole("img").querySelector("polyline")).toHaveAttribute("points", "10,98 160,8 310,98");
    expect(screen.getByText("Length (m) · 1 to 1.0000000000000002")).toBeVisible();
    expect(screen.getByText(/Time \(s\) · -1.7976931348623157e\+308 to 1.7976931348623157e\+308/)).toBeVisible();
  });

  it("exports every result row, escapes CSV cells and retains small signed values beyond the preview", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const rows = Array.from({ length: 42 }, (_, i) => [i === 41 ? -Number.MIN_VALUE : i, i === 41 ? 'a,"b"' : "=formula"]);
      mount(JSON.stringify({ ...state, latest: { ...result, table: { columns: ["Measurement", "Label"], rows } } }));
      expect(screen.getAllByRole("row")).toHaveLength(31);
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
    expect(screen.queryByRole("button", { name: "Cancel calculation" })).toBeNull();
    finishColumn({ ok: true, values: [10, 20], reference });
    await waitFor(() => expect(screen.getByLabelText("Samples (m)")).toHaveValue("[10,20]"));
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request.mock.lastCall?.[2]?.[1]).toBe("run"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({
      inputs: { samples: [10, 20] }, sources: { samples: reference },
    });
  });
});
