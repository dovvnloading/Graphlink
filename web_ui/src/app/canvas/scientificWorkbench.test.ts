import { describe, expect, it } from "vitest";
import { chartableScienceTypes, columnChoices, compatibleOptions, draftInputs, fieldText, formatMeasurement, formatScienceNumber, formatScienceRange, formatScienceSummary, matchesSource, parseInputs, readScienceState, sourceOptions, type ScienceField, type ScienceResult, type ScienceState } from "./scientificWorkbench";

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

  it("accepts only declared choice values and validates optional methods and citations", () => {
    const operation = {
      id: "potency", title: "Potency", description: "Relative potency", example: { metric: "EC50" },
      fields: [{ key: "metric", label: "Metric", kind: "choice" as const, unit: "category",
        description: "", choices: ["EC50", "IC50"] }],
      methods: "Fit a 4PL response and report the relative halfway concentration.",
      citations: [{ label: "Sebaugh (2011)", url: "https://doi.org/10.1002/pst.426" }],
    };
    expect(parseInputs(operation, { metric: "IC50" })).toEqual({ metric: "IC50" });
    expect(draftInputs(operation, { metric: "IC50" })).toEqual({ metric: "IC50" });
    expect(() => parseInputs(operation, { metric: "ec50" })).toThrow(/must be one of/);
    expect(() => parseInputs(operation, { metric: "Other" })).toThrow(/must be one of/);
    const state = { scienceVersion: 1, packId: "biology", operations: [operation], latest: null };
    expect(readScienceState(JSON.stringify(state))?.operations[0]).toEqual(operation);
    expect(readScienceState(JSON.stringify({ ...state, operations: [
      { ...operation, fields: [{ ...operation.fields[0], choices: [] }] },
    ] }))).toBeNull();
    expect(readScienceState(JSON.stringify({ ...state, operations: [
      { ...operation, fields: [{ ...operation.fields[0], choices: ["EC50", "EC50"] }] },
    ] }))).toBeNull();
    expect(readScienceState(JSON.stringify({ ...state, operations: [
      { ...operation, citations: [{ label: "Untrusted", url: "javascript:alert(1)" }] },
    ] }))).toBeNull();
    const linked = { nodeId: "previous", resultHash: "hash", selector: ["values", "metric"] };
    expect(compatibleOptions(operation.fields[0], [
      { label: "Supported", value: "IC50", reference: linked },
      { label: "Unsupported", value: "Unknown", reference: linked },
    ])).toHaveLength(1);
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


describe("typed table metadata and typed column choices", () => {
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

  it("offers a whole-table binding alongside numeric vectors with bounded row windows", () => {
    const options = sourceOptions([{ nodeId: "parent", title: "Source", raw: JSON.stringify({
      scienceVersion: 1, packId: "core", nodeKey: "d".repeat(32), latest: null,
      operations: [{ id: "numeric_csv", title: "Import", description: "", fields: [], example: {} }], table,
    }) }], 1024);
    expect(options).toHaveLength(3);
    expect(options[0].label).toContain("Whole typed table (100000 rows, 2 columns)");
    expect(options[0].reference).toMatchObject({
      resultHash: table.asset_ref, receiptHash: table.provenance.receiptHash,
      selector: ["typed_table"],
    });
    expect(options[0].value).toEqual(table);
    expect(options[1].label).toContain("first 1024 of 100000 rows");
    expect(options[1].label).toContain("Reject missing");
    expect(options[2].label).toContain("Drop missing (explicit)");
    expect(options[1].tableColumn).toEqual({
      nodeId: "parent", column: 0, policy: "reject", start: 0, end: 1024,
    });
    expect(options[2].reference).toMatchObject({ resultHash: table.asset_ref, receiptHash: table.provenance.receiptHash,
      selector: ["table_column", 0, "drop", 0, 1024] });
    const field = (kind: ScienceField["kind"]): ScienceField => ({ key: "a", kind, label: "A", unit: "", description: "" });
    expect(compatibleOptions(field("vector"), options)).toHaveLength(2);
    expect(compatibleOptions(field("table"), options)).toEqual([options[0]]);
    expect(compatibleOptions(field("number"), options)).toEqual([]);
  });

  it("selects numeric, text and categorical columns relative to the complete typed table", () => {
    const mixed = { ...table, columnCount: 3, previewColumns: 3,
      columns: [...table.columns, {
        name: "Group", label: "Group", unit: "", type: "categorical" as const, missingCount: 0,
      }],
      preview: [[12.5, "A", "control"], [null, "B", "trial"]],
    };
    const op = {
      id: "grouped_table_summary", title: "Grouped table", description: "",
      fields: [
        { key: "table", label: "Typed table", kind: "table" as const, unit: "", description: "" },
        { key: "value_column", label: "Value", kind: "column" as const, table_field: "table",
          column_types: ["numeric" as const], unit: "", description: "" },
        { key: "group_column", label: "Group", kind: "column" as const, table_field: "table",
          unit: "", description: "" },
      ], example: { table: null, value_column: null, group_column: null },
    };
    const draft = { table: JSON.stringify(mixed), value_column: "0", group_column: "2" };
    expect(columnChoices(op.fields[1], draft)).toEqual([
      { index: 0, label: "Temperature (degC)", type: "numeric" },
    ]);
    expect(columnChoices(op.fields[2], draft)).toEqual([
      { index: 0, label: "Temperature (degC)", type: "numeric" },
      { index: 1, label: "Station", type: "text" },
      { index: 2, label: "Group", type: "categorical" },
    ]);
    expect(parseInputs(op, draft)).toEqual({ table: mixed, value_column: 0, group_column: 2 });
    expect(parseInputs(op, { ...draft, group_column: "1" }).group_column).toBe(1);
    expect(parseInputs(op, { ...draft, group_column: "0" }).group_column).toBe(0);
    expect(() => parseInputs(op, { ...draft, value_column: "2" })).toThrow(/numeric/);
    expect(() => parseInputs(op, { ...draft, group_column: "4" })).toThrow(/column from the connected table/);
    expect(() => parseInputs(op, { ...draft, table: "" })).toThrow(/Enter a value/);
    expect(columnChoices(op.fields[2], { ...draft, table: "" })).toEqual([]);
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

  it("accepts bounded typed views and rejects malformed wells and survival series on the wire", () => {
    const plate = { type: "plate_map", title: "Assay", format: 384, unit: "RFU",
      wells: [{ well: "P24", value: 0 }, { well: "A1", value: "control" }] };
    const survival = { type: "survival_curve", title: "Survival", xLabel: "Days",
      groups: [
        { label: "A", times: [0, 0, 2], survival: [1, .8, .8], censored: [2] },
        { label: "B", times: [0, 1], survival: [1, .5], censored: [] },
        { label: "C", times: [0, 2], survival: [1, 1], censored: [2] },
      ] };
    const latest: ScienceResult = {
      operationId: "convert_unit", inputs: {}, summary: "ok", values: {},
      provenance: { resultHash: "digest" },
    };
    const read = (views: unknown) => readScienceState(JSON.stringify({
      ...base, latest: { ...latest, views },
    }));
    expect(read([plate, survival])?.latest?.views).toHaveLength(2);
    expect(read([{ ...plate, wells: [{ well: "P25", value: 2 }] }])).toBeNull();
    expect(read([{ ...plate, wells: [{ well: "A1", value: 1 }, { well: "A1", value: 2 }] }])).toBeNull();
    const overflowingWire = JSON.stringify({ ...base, latest: { ...latest,
      views: [{ ...plate, wells: [{ well: "A1", value: 0 }] }],
    } }).replace('"value":0', '"value":1e999');
    expect(readScienceState(overflowingWire)).toBeNull();
    expect(read([{ ...plate, wells: [{ well: "A1", value: true }] }])).toBeNull();
    expect(read([{ ...survival, groups: [survival.groups[0]] }])).toBeNull();
    expect(read([{ ...survival, groups: [{ ...survival.groups[0], survival: [1, 1.1, 1] }, ...survival.groups.slice(1)] }])).toBeNull();
    expect(read([{ ...survival, groups: [{ ...survival.groups[0], censored: [5] }, ...survival.groups.slice(1)] }])).toBeNull();
    expect(read([plate, plate, plate, plate, plate])).toBeNull();
    expect(read([{ type: "custom_html", title: "Unsafe", html: "<svg onload=alert(1)>" }])).toBeNull();
    expect(readScienceState(JSON.stringify({ ...base, latest }))?.latest?.views).toBeUndefined();
  });


  it("accepts forest plots and rooted phylogenies, rejecting tampered geometry and labels", () => {
    const forest = {
      type: "forest_plot", title: "Effect", xLabel: "Risk ratio", scale: "log", reference: 1,
      studies: [
        { label: "A", estimate: .8, lower: .6, upper: 1.1, weight: 3 },
        { label: "B", estimate: 1.2, lower: 1.05, upper: 1.4 },
      ],
      pooled: { label: "Combined", estimate: .94, lower: .8, upper: 1.1 },
    };
    const tree = {
      type: "phylogenetic_tree", title: "Relatedness", layout: "circular", mode: "phylogram",
      tree: { children: [
        { length: 1, support: 99, children: [
          { name: "A", length: .2 }, { name: "B", length: 0 },
        ] },
        { name: "C", length: .7, support: 80 },
      ] },
    };
    const latest: ScienceResult = {
      operationId: "convert_unit", inputs: {}, summary: "ok", values: {},
      provenance: { resultHash: "digest" },
    };
    const read = (views: unknown) => readScienceState(JSON.stringify({
      ...base, latest: { ...latest, views },
    }));
    expect(read([forest, tree])?.latest?.views).toHaveLength(2);
    expect(read([{ ...forest, studies: [{ ...forest.studies[0], upper: .3 }] }])).toBeNull();
    expect(read([{ ...forest, studies: [{ ...forest.studies[0], lower: 0 }] }])).toBeNull();
    expect(read([{ ...forest, reference: 0 }])).toBeNull();
    expect(read([{ ...forest, scale: "linear", reference: 0 }])?.latest?.views).toHaveLength(1);
    expect(read([{ ...forest, pooled: { ...forest.pooled, upper: .5 } }])).toBeNull();
    expect(read([{ ...forest, studies: [...forest.studies, forest.studies[0]] }])).toBeNull();
    expect(read([{ ...forest, studies: [{ ...forest.studies[0], weight: -1 }] }])).toBeNull();
    expect(read([{ ...forest, html: "<script>" }])).toBeNull();
    expect(read([{ ...tree, tree: { ...tree.tree, length: 1 } }])).toBeNull();
    expect(read([{ ...tree, tree: { children: [
      { name: "A", length: 1 }, { name: "A", length: 2 },
    ] } }])).toBeNull();
    expect(read([{ ...tree, tree: { children: [
      { name: "A", length: -1 }, { name: "B", length: 2 },
    ] } }])).toBeNull();
    expect(read([{ ...tree, tree: { children: [
      { name: "A", length: null }, { name: "B", length: 2 },
    ] } }])).toBeNull();
    expect(read([{ ...tree, tree: { children: [
      { name: "A" }, { name: "B", length: 2 },
    ] } }])).toBeNull();
    expect(read([{ ...tree, tree: { children: [
      { name: "A", length: 1, support: 101 }, { name: "B", length: 2 },
    ] } }])).toBeNull();
    expect(read([{ ...tree, mode: "cladogram", tree: { children: [
      { name: "A" }, { name: "B" },
    ] } }])?.latest?.views).toHaveLength(1);
    expect(read([{ ...tree, layout: "radial" }])).toBeNull();
    expect(read([{ ...tree, tree: { children: [] } }])).toBeNull();
  });

  it("accepts a bounded volcano scatter but rejects malformed q-values and injected fields", () => {
    const latest: ScienceResult = {
      operationId: "convert_unit", inputs: {}, summary: "ok", values: {},
      provenance: { resultHash: "digest" },
    };
    const volcano = { type: "volcano_plot", title: "Genes", xLabel: "log2 fold change",
      fdrThreshold: 0.05, log2FoldChangeThreshold: 1,
      points: [{ gene: "GeneA", log2FoldChange: 2, qValue: 0.01 },
               { gene: "GeneB", log2FoldChange: -1, qValue: 1 }] };
    const valid = readScienceState(JSON.stringify({ ...base, latest: { ...latest, views: [volcano] } }));
    expect(valid?.latest?.views).toHaveLength(1);
    const bad = (view: unknown) => readScienceState(JSON.stringify({
      ...base, latest: { ...latest, views: [view] },
    }));
    expect(bad({ ...volcano, fdrThreshold: 0 })).toBeNull();
    expect(bad({ ...volcano, fdrThreshold: 1 })).toBeNull();
    expect(bad({ ...volcano, log2FoldChangeThreshold: -1 })).toBeNull();
    expect(bad({ ...volcano, points: [{ gene: "GeneA", log2FoldChange: 2, qValue: -0.1 }] })).toBeNull();
    expect(bad({ ...volcano, points: [{ gene: "GeneA", log2FoldChange: 2, qValue: 1.1 }] })).toBeNull();
    expect(bad({ ...volcano, points: [...volcano.points, volcano.points[0]] })).toBeNull();
    expect(bad({ ...volcano, points: Array.from({ length: 1025 }, (_, i) =>
      ({ gene: `G${i}`, log2FoldChange: 0, qValue: 1 })) })).toBeNull();
    expect(bad({ ...volcano, html: "<script>" })).toBeNull();
    expect(bad({ ...volcano, points: [] })?.latest?.views).toHaveLength(1);
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

describe("formatScienceNumber", () => {
  it("drops binary floating-point noise but keeps real precision", () => {
    expect(formatScienceNumber(50.000837999999995)).toBe("50.0008");
    expect(formatScienceNumber(0.05000083799999999)).toBe("0.0500008");
    expect(formatScienceNumber(9.409000000000001e-11)).toBe("9.409 × 10⁻¹¹");
    expect(formatScienceNumber(0.6989700043360187)).toBe("0.69897");
    expect(formatScienceNumber(2.681585351992652e-8)).toBe("2.68159 × 10⁻⁸");
  });

  it("leaves integers and non-finite values exactly as they are", () => {
    expect(formatScienceNumber(5400)).toBe("5,400");
    expect(formatScienceNumber(1e21)).toBe("1 × 10²¹");
    expect(formatScienceNumber(Number.NaN)).toBe("NaN");
  });
});



describe("scientific result display formatting", () => {
  it("makes exponent-based summary measurements readable without changing units or identifiers", () => {
    expect(formatScienceSummary("2e+06 viable cells/mL after a 2-fold dilution."))
      .toBe("2,000,000 viable cells/mL after a 2-fold dilution.");
    expect(formatScienceSummary("catalytic efficiency kcat/Km=1e+06 M⁻¹ s⁻¹."))
      .toBe("catalytic efficiency kcat/Km=1,000,000 M⁻¹ s⁻¹.");
    expect(formatScienceSummary("A -1.25e-06 to 3.5e+21, 9.409e-11."))
      .toBe("A -1.25 × 10⁻⁶ to 3.5 × 10²¹, 9.409 × 10⁻¹¹.");
    expect(formatScienceSummary("Tiny 1e-400 and huge 1e+400"))
      .toBe("Tiny 1 × 10⁻⁴⁰⁰ and huge 1 × 10⁴⁰⁰");
    expect(formatScienceSummary("ID1e+06, 1e+06.2 and 2e+06mol"))
      .toBe("ID1e+06, 1e+06.2 and 2e+06mol");
  });

  it("groups ordinary counts and uses matching scientific notation in Fick units", () => {
    expect(formatScienceSummary("500000 CFU/mL from 100000 person-years."))
      .toBe("500,000 CFU/mL from 100,000 person-years.");
    expect(formatScienceSummary("Fick flux = 0.000002 mol/(m²·s); transfer = 4e-08 mol."))
      .toBe("Fick flux = 2 × 10⁻⁶ mol/(m²·s); transfer = 4 × 10⁻⁸ mol.");
    expect(formatScienceSummary("1,000,000 CFU/mL, M⁻¹ s⁻¹, ID1e+06"))
      .toBe("1,000,000 CFU/mL, M⁻¹ s⁻¹, ID1e+06");
    expect(formatScienceNumber(58.3279090977)).toBe("58.3279");
    expect(formatScienceNumber(500000)).toBe("500,000");
    expect(formatScienceNumber(2e-6)).toBe("2 × 10⁻⁶");
  });

  it("rounds nominal values and uncertainties to compatible precision", () => {
    expect(formatMeasurement({ value: 58.3279090977,
      standardUncertainty: 0.0281384359596, coverageFactor: 2, unit: "mV" }))
      .toBe("58.328 ± 0.028 mV");
  });

  it("shortens plot bounds but keeps adjacent floating-point endpoints distinct", () => {
    expect(formatScienceRange(5.895064056600376, 88.81371402318987))
      .toBe("5.89506 to 88.8137");
    expect(formatScienceRange(1, 1.0000000000000002))
      .toBe("1 to 1.0000000000000002");
    expect(formatScienceRange(-Number.MAX_VALUE, Number.MAX_VALUE))
      .toBe("-1.79769 × 10³⁰⁸ to 1.79769 × 10³⁰⁸");
    expect(formatScienceRange(0, 0)).toBe("0 to 0");
  });
});

describe("generated typed-table result sources", () => {
  const asset = {
    version: 1 as const, asset_ref: "a".repeat(64), mime_type: "application/json",
    rowCount: 12000, columnCount: 3, previewColumns: 3, sourceName: "statistics.generated result",
    columns: [
      { name: "Index", label: "Index", unit: "", type: "numeric" as const, missingCount: 0 },
      { name: "Group", label: "Group", unit: "", type: "categorical" as const, missingCount: 0 },
      { name: "Value", label: "Value (m)", unit: "m", type: "numeric" as const, missingCount: 3 },
    ],
    preview: [[0, "A", 1.2], [1, "B", null]],
    provenance: { fileHash: "b".repeat(64), tableHash: "a".repeat(64),
      receiptHash: "c".repeat(64), parseOptions: { source: "operation" } },
  };
  const result: ScienceResult = {
    operationId: "generated", inputs: {}, values: {}, summary: "complete",
    tableAsset: asset, provenance: { resultHash: "d".repeat(64), receiptHash: "e".repeat(64) },
  };
  const raw = JSON.stringify({
    scienceVersion: 1, packId: "statistics", nodeKey: "f".repeat(32),
    operations: [{ id: "generated", title: "Generate", description: "", fields: [], example: {} }],
    latest: result,
  });

  it("parses compact generated metadata and rejects conflicting inline or corrupt data", () => {
    expect(readScienceState(raw)?.latest?.tableAsset).toEqual(asset);
    expect(readScienceState(JSON.stringify({
      ...JSON.parse(raw), latest: { ...result, table: { columns: ["x"], rows: [[1]] } },
    }))).toBeNull();
    expect(readScienceState(JSON.stringify({
      ...JSON.parse(raw), latest: { ...result, tableAsset: { ...asset, preview: [[1]] } },
    }))).toBeNull();
  });

  it("provides a whole result table plus explicit bounded numeric column selectors", () => {
    const options = sourceOptions([{ nodeId: "producer", title: "Source", raw }], 1024);
    const whole = options.find((option) => option.reference.selector[0] === "result_table")!;
    expect(whole.value).toEqual(asset);
    expect(whole.reference).toMatchObject({
      sourceKey: "f".repeat(32), resultHash: result.provenance.resultHash,
      receiptHash: result.provenance.receiptHash,
    });
    expect(compatibleOptions({
      key: "table", label: "Table", kind: "table", unit: "", description: "",
    }, options)).toEqual([whole]);
    const columns = options.filter((option) => option.reference.selector[0] === "result_table_column");
    expect(columns).toHaveLength(3);
    expect(columns[0].tableColumn).toEqual({
      nodeId: "producer", column: 0, policy: "reject", start: 0, end: 1024, source: "result",
    });
    expect(columns[1].tableColumn?.column).toBe(2);
    expect(columns[2].tableColumn?.policy).toBe("drop");
    expect(columns[1].reference.resultHash).toBe(result.provenance.resultHash);
    expect(compatibleOptions({
      key: "vector", label: "Vector", kind: "vector", unit: "", description: "",
    }, options)).toEqual(columns);
    expect(matchesSource(whole, { ...whole.reference, resultHash: "outdated" })).toBe(false);
  });
});
