import { describe, expect, it } from "vitest";
import { chartableScienceTypes, compatibleOptions, draftInputs, fieldText, matchesSource, parseInputs, readScienceState, sourceOptions, type ScienceField, type ScienceResult, type ScienceState } from "./scientificWorkbench";

const limits = { points: 3, columns: 2, cells: 4, text: 3 };
function parse(kind: ScienceField["kind"], raw: string) {
  return parseInputs({ id: "op", title: "Test", description: "", example: {}, fields: [
    { key: "a", label: "A", unit: "", description: "", kind },
  ] }, { a: raw }, limits).a;
}

describe("scientific catalog input and parent contracts", () => {
  it("checks the published shape limits before sending requests, including Unicode code point counts", () => {
    expect(parse("vector", "[1,2,3]")).toEqual([1, 2, 3]);
    expect(() => parse("vector", "[1,2,3,4]")).toThrow(/3 entries/);
    expect(() => parse("matrix", "[[1,2,3]]")).toThrow(/2 columns/);
    expect(() => parse("matrix", "[[1,2],[3,4],[5,6]]")).toThrow(/4 cells/);
    expect(parse("text", "🌍🌍🌍")).toBe("🌍🌍🌍");
    expect(() => parse("text", "🌍🌍🌍x")).toThrow(/3 characters/);
    expect(parse("integer", "9007199254740991")).toBe(Number.MAX_SAFE_INTEGER);
    expect(() => parse("integer", "9007199254740990.5")).toThrow(/integer notation/);
    expect(() => parse("integer", "9007199254740992")).toThrow();
    expect(() => parse("number", "1e999")).toThrow();
    expect(() => parse("vector", "[1e999]")).toThrow();
  });

  it("validates numeric spellings before rounding or underflow in scalars and both array shapes", () => {
    for (const kind of ["number", "vector", "matrix"] as const) {
      const shaped = (s: string) => kind === "number" ? s : kind === "vector" ? "[" + s + "]" : "[[" + s + "]]";
      for (const token of ["9007199254740993", "-9.007199254740993e15", "1.0000000000000001e17"]) {
        expect(() => parse(kind, shaped(token))).toThrow(/integer count/);
      }
      for (const token of ["1e-999", "-4e-4000", "1e-" + "9".repeat(10000)]) {
        expect(() => parse(kind, shaped(token))).toThrow(/too small/);
      }
      for (const token of ["9.007199254740992e15", "-1e20", "0.1", "5e-324", "0e-" + "9".repeat(10000)]) {
        expect(() => parse(kind, shaped(token))).not.toThrow();
      }
    }
  });

  it("round-trips a stored large integral value whose shortest decimal spells a different integer", () => {
    const number = 1000000000000000128;
    expect(String(number)).toBe("1000000000000000100");
    const field: ScienceField = { key: "a", label: "A", kind: "matrix", unit: "", description: "" };
    const raw = fieldText(field, [[number], [-number]]);
    expect(raw).toBe("[[1000000000000000128],[-1000000000000000128]]");
    expect(parse("matrix", raw)).toEqual([[number], [-number]]);
  });

  it("distinguishes like-named parents and a table column from a same-named plot axis", () => {
    const contents: ScienceState = { scienceVersion: 1, packId: "core", operations: [
      { id: "op", title: "Samples", description: "", example: {}, fields: [] },
    ], latest: { operationId: "op", inputs: {}, summary: "", values: {}, table: { columns: ["time"], rows: [[2], [0]] },
      plot: { title: "Signal", x: [0, 2], y: [2, 1], xLabel: "time", yLabel: "value" },
      provenance: { resultHash: "n", receiptHash: "r" },
    } };
    const options = sourceOptions(["node1", "node2"].map((nodeId) => ({
      nodeId, title: "Scientific Core", raw: JSON.stringify({ ...contents, nodeKey: nodeId }),
    })));
    expect(new Set(options.map((opt) => opt.label)).size).toBe(options.length);
    expect(options.find((opt) => opt.label === "Scientific Core (node1) · time (table column 1)")?.value).toEqual([2, 0]);
    const axis = options.find((opt) => opt.label === "Scientific Core (node1) · time (plot x)")!;
    expect(axis.value).toEqual([0, 2]);
    expect(matchesSource(axis, { ...axis.reference, nodeId: "old" })).toBe(true);
    expect(matchesSource(axis, { ...axis.reference, receiptHash: "previous" })).toBe(false);
  });
});


describe("chartable scientific results", () => {
  const result: ScienceResult = {
    operationId: "plot_xy", inputs: {}, summary: "", values: {},
    plot: { title: "Measured", x: [0, 1, 2], y: [1, 2, 3], xLabel: "Time (s)", yLabel: "Value (V)" },
    table: { columns: ["X", "Y"], rows: [[0, 1], [1, 2], [2, 3]] },
    provenance: { packId: "core", resultHash: "x" },
  };
  it("offers scatter and fitting only for genuinely compatible result shapes", () => {
    expect(chartableScienceTypes(result)).toEqual(["scatter", "boxplot", "heatmap"]);
    expect(chartableScienceTypes({ ...result, operationId: "linear_regression",
      provenance: { ...result.provenance, packId: "statistics" },
      inputs: { x: [0, 1, 2], y: [1, 2, 4] } })).toEqual([
      "line", "scatter", "boxplot", "heatmap",
    ]);
    expect(chartableScienceTypes({ ...result, plot: undefined, table: undefined,
      values: { estimate: 42, combined_standard_uncertainty: 0.2 } })).toEqual(["scatter"]);
    expect(chartableScienceTypes({ ...result, plot: undefined, table: undefined,
      values: { count: 10 } })).toEqual([]);
  });
  it("respects the existing chart point and matrix limits", () => {
    const long = Array.from({ length: 301 }, (_, i) => i);
    expect(chartableScienceTypes({ ...result, plot: { ...result.plot!, x: long, y: long },
      table: undefined })).toEqual([]);
    const table = { columns: Array.from({ length: 30 }, (_, i) => String(i)),
      rows: Array.from({ length: 11 }, () => Array.from({ length: 30 }, () => 1)) };
    expect(chartableScienceTypes({ ...result, plot: undefined, table })).toEqual([]);
  });
});


describe("typed table metadata and numeric column choices", () => {
  const table = {
    version: 1 as const, asset_ref: "a".repeat(64), mime_type: "application/json",
    rowCount: 100000, columnCount: 2, previewColumns: 2, sourceName: "measurements.tsv",
    columns: [
      { name: "Temperature", label: "Temperature (degC)", unit: "degC", type: "numeric" as const, missingCount: 2 },
      { name: "Station", label: "Station", unit: "", type: "text" as const, missingCount: 0 },
    ],
    preview: [[12.5, "A"], [null, "B"]],
    provenance: { fileHash: "b".repeat(64), tableHash: "a".repeat(64),
      receiptHash: "c".repeat(64), parseOptions: { format: "tsv", missingValues: ["", "NA"] } },
  };

  it("rejects malformed saved previews before the node view can index their rows", () => {
    const base: ScienceState = { scienceVersion: 1, packId: "core",
      operations: [{ id: "numeric_csv", title: "Import", description: "", fields: [], example: {} }],
      latest: null, table };
    expect(readScienceState(JSON.stringify({ ...base, table: { ...table, preview: [null] } }))).toBeNull();
    expect(readScienceState(JSON.stringify({ ...base, table: { ...table, previewColumns: 100000 } }))).toBeNull();
  });

  it("reads the typed schema independently of any legacy result", () => {
    const state: ScienceState = { scienceVersion: 1, packId: "core",
      operations: [{ id: "numeric_csv", title: "Import numeric CSV", description: "", fields: [], example: {} }],
      latest: null, table };
    expect(readScienceState(JSON.stringify(state))?.table?.rowCount).toBe(100000);
    expect(readScienceState(JSON.stringify({ ...state, table: { ...table, columns: [{ type: "nonsense" }] } }))).toBeNull();
  });

  it("offers only numeric typed columns with explicit missing policies and a bounded row window", () => {
    const options = sourceOptions([{ nodeId: "parent", title: "Source", raw: JSON.stringify({
      scienceVersion: 1, packId: "core", nodeKey: "d".repeat(32), latest: null,
      operations: [{ id: "numeric_csv", title: "Import", description: "", fields: [], example: {} }], table,
    }) }], 1024);
    expect(options).toHaveLength(2);
    expect(options[0].label).toContain("first 1024 of 100000 rows");
    expect(options[0].label).toContain("Reject missing");
    expect(options[1].label).toContain("Drop missing (explicit)");
    expect(options[0].tableColumn).toEqual({
      nodeId: "parent", column: 0, policy: "reject", start: 0, end: 1024,
    });
    expect(options[1].reference).toMatchObject({ resultHash: table.asset_ref, receiptHash: table.provenance.receiptHash,
      selector: ["table_column", 0, "drop", 0, 1024] });
    const field = (kind: ScienceField["kind"]): ScienceField => ({ key: "a", kind, label: "A", unit: "", description: "" });
    expect(compatibleOptions(field("vector"), options)).toHaveLength(2);
    expect(compatibleOptions(field("number"), options)).toEqual([]);
  });
});


describe("unit-aware scientific drafts", () => {
  const fields: ScienceField[] = [
    { key: "distance", label: "Distance", kind: "number", unit: "m", default_unit: "m", dimension: "length (m)", description: "" },
    { key: "temperature", label: "Temperature", kind: "number", unit: "K", default_unit: "K", dimension: "temperature (K)", description: "" },
    { key: "samples", label: "Samples", kind: "vector", unit: "m", default_unit: "m", dimension: "length (m)", description: "" },
    { key: "grid", label: "Grid", kind: "matrix", unit: "Pa", default_unit: "Pa", dimension: "pressure (Pa)", description: "" },
    { key: "count", label: "Count", kind: "integer", unit: "count", default_unit: "count", dimension: "dimensionless (1)", description: "" },
  ];
  const operation = { id: "units", title: "Units", description: "", example: {}, fields };

  it("keeps explicit measurement literals for server-side exact conversion", () => {
    expect(parseInputs(operation, {
      distance: "25 mm", temperature: "80 degF", samples: '["25 mm", 2, "1 cm"]',
      grid: '[["1.2 kPa", 3], [4, "500 Pa"]]', count: "2 count",
    })).toEqual({
      distance: "25 mm", temperature: "80 degF", samples: ["25 mm", 2, "1 cm"],
      grid: [["1.2 kPa", 3], [4, "500 Pa"]], count: "2 count",
    });
  });

  it("defers suffixed decimal rounding until after exact SI scaling on the backend", () => {
    expect(parseInputs(operation, {
      distance: "1e310 qm", temperature: "32 degF",
      samples: '["9007199254740993 nm","1e310 qm",2]',
      grid: '[["1e310 qPa","2 kPa"]]', count: "2 count",
    })).toEqual({
      distance: "1e310 qm", temperature: "32 degF",
      samples: ["9007199254740993 nm", "1e310 qm", 2],
      grid: [["1e310 qPa", "2 kPa"]], count: "2 count",
    });
    expect(() => parseInputs(operation, {
      distance: "1e310", temperature: "300", samples: "[1]",
      grid: "[[1]]", count: "2",
    })).toThrow(/finite/);
  });

  it("restores normalized bare numbers from historical saved graph drafts", () => {
    const normalized = { distance: 0.025, temperature: 273.15,
      samples: [0.025, 0.02], grid: [[1200, 1]], count: 2 };
    expect(parseInputs(operation, draftInputs(operation, normalized))).toEqual(normalized);
  });

  it("retains numerical bare values and catches malformed measurements", () => {
    expect(parseInputs(operation, {
      distance: "0.025", temperature: "300", samples: "[1,2]", grid: "[[1,2]]", count: "2",
    })).toEqual({
      distance: 0.025, temperature: 300, samples: [1, 2], grid: [[1, 2]], count: 2,
    });
    expect(() => parseInputs(operation, {
      distance: "1 m", temperature: "300", samples: '["not a number"]', grid: "[[1]]", count: "2",
    })).toThrow(/array of numbers/);
    expect(() => parseInputs(operation, {
      distance: "1 m", temperature: "300", samples: "[1]", grid: "[[1]]", count: "2.0",
    })).toThrow(/integer notation/);
  });
});

describe("saved Graph Runner unit literals", () => {
  it("quotes suffixed vector and matrix entries when restoring a saved configuration", () => {
    const fields: ScienceField[] = [
      { key: "samples", label: "Samples", kind: "vector", unit: "m", description: "" },
      { key: "grid", label: "Grid", kind: "matrix", unit: "m", description: "" },
    ];
    const op = { id: "units", title: "Units", description: "", example: {}, fields };
    const inputs = { samples: ["25 mm", 2], grid: [["1 cm", 3]] };
    const draft = draftInputs(op, inputs);
    expect(draft.samples).toBe('["25 mm",2]');
    expect(draft.grid).toBe('[["1 cm",3]]');
    expect(parseInputs(op, draft)).toEqual(inputs);
  });
});

describe("saved Workbench wire safety", () => {
  const base: ScienceState = {
    scienceVersion: 1, packId: "core",
    operations: [{ id: "convert_unit", title: "Convert", description: "", fields: [], example: {} }],
    latest: null,
  };

  it("rejects incomplete legacy runner references rather than crashing on selector.length", () => {
    const damaged = { ...base, runner: {
      version: 1, operationId: "convert_unit", inputs: {}, sources: {
        measurement: { nodeId: "source", resultHash: "digest" },
      },
    } };
    expect(readScienceState(JSON.stringify(damaged))).toBeNull();
  });

  it("rejects malformed uncertainty budget arrays and non-text diagnostics", () => {
    const measurement = { value: 4, standardUncertainty: 1, coverageFactor: 2, unit: "" };
    const result: ScienceResult = {
      operationId: "convert_unit", inputs: {}, summary: "ok", values: { answer: 4 },
      provenance: { resultHash: "digest" },
      uncertainty: { method: "gum", inputs: {}, outputs: { answer: measurement } },
    };
    expect(readScienceState(JSON.stringify({
      ...base, latest: { ...result,
        uncertainty: { ...result.uncertainty, outputs: {
          answer: { ...measurement, budget: { bogus: true } },
        } },
      },
    }))).toBeNull();
    expect(readScienceState(JSON.stringify({
      ...base, latest: { ...result,
        uncertainty: { ...result.uncertainty, unsupportedOutputs: { answer: { invalid: true } } },
      },
    }))).toBeNull();
  });
});
