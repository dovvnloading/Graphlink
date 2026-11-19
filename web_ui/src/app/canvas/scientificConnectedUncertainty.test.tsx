import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WsTransport } from "../../lib/ws/transport";
import { SceneStore } from "./sceneStore";
import { ScientificNodeView, type ScientificFlowNode } from "./ScientificNodeView";
import {
  compatibleOptions, connectedInputText, formatMeasurement, matchesSource, parseInputs, readScienceState,
  resultMeasurement, sourceOptions, type ScienceResult, type ScienceState,
} from "./scientificWorkbench";

const convert = {
  id: "convert_unit", title: "Convert", description: "Convert a measured quantity.",
  fields: [
    { key: "value", label: "Measured value", kind: "number" as const, unit: "", description: "" },
    { key: "from_unit", label: "Source unit", kind: "text" as const, unit: "", description: "" },
    { key: "to_unit", label: "Destination unit", kind: "text" as const, unit: "", description: "" },
  ],
  example: { value: 5, from_unit: "mm", to_unit: "m" },
};
const core: ScienceState = { scienceVersion: 1, packId: "core", operations: [convert], latest: null };

const parentResult: ScienceResult = {
  operationId: "propagate_uncertainty", inputs: {}, summary: "GUM computed",
  values: {
    estimate: 5, combined_standard_uncertainty: .2,
    coverage_factor: 2.1, coverage_probability: .95,
    coverage_lower: 4.58, coverage_upper: 5.42,
  },
  provenance: { packId: "uncertainty", resultHash: "a".repeat(64), receiptHash: "b".repeat(64) },
};
const parent = {
  nodeId: "parent", title: "Uncertainty",
  raw: JSON.stringify({
    scienceVersion: 1, packId: "uncertainty", nodeKey: "c".repeat(32),
    latest: parentResult, operations: [{ id: "propagate_uncertainty", title: "GUM",
      description: "", fields: [], example: {} }],
  }),
};

const linkedResult: ScienceResult = {
  operationId: "convert_unit", inputs: { value: 5, from_unit: "mm", to_unit: "m" },
  summary: "Converted millimetres to metres.", values: { "value (m)": .005 },
  uncertainty: {
    method: "gum",
    inputs: {
      value: { value: 5, standardUncertainty: .2, coverageFactor: 2.1,
        coverageProbability: .95, unit: "mm", components: { ["a".repeat(64)]: .2 } },
    },
    outputs: {
      "value (m)": {
        value: .005, standardUncertainty: .0002, coverageFactor: 2.1,
        coverageProbability: .95, unit: "m",
        interval: [.00458, .00542], method: "gum",
        components: { ["a".repeat(64)]: .0002 },
        budget: [{ input: "value", kind: "input", unit: "mm", standardUncertainty: .2,
          sensitivity: .001, signedContribution: .0002, variance: 4e-8 }],
      },
    },
  },
  provenance: { packId: "core", resultHash: "d".repeat(64) },
};

function mount(raw = JSON.stringify(core)) {
  const request = vi.fn<WsTransport["request"]>().mockResolvedValue({ ok: true });
  const store = new SceneStore({ request } as unknown as WsTransport);
  const props = { id: "target", data: {
    title: "Scientific Core", raw, pluginId: "science_core", parents: [parent], store,
  } } as NodeProps<ScientificFlowNode>;
  render(<ScientificNodeView {...props} />, { wrapper: ReactFlowProvider });
  return { request };
}

describe("uncertainty-aware Scientific Workbench connections", () => {
  it("advertises a parent's nominal value, 1σ, coverage and lineage without changing the wire source", () => {
    const options = sourceOptions([parent]);
    const estimate = options.find((option) => option.reference.selector.join("/") === "values/estimate")!;
    expect(estimate.label).toContain("5 ± 0.2");
    expect(estimate.value).toBe(5);
    expect(estimate.uncertainty?.coverageFactor).toBe(2.1);
    expect(estimate.uncertainty?.coverageProbability).toBe(.95);
    expect(estimate.reference).toEqual({
      nodeId: "parent", sourceKey: "c".repeat(32),
      resultHash: "a".repeat(64), receiptHash: "b".repeat(64),
      selector: ["values", "estimate"],
    });
    expect(matchesSource(estimate, { ...estimate.reference, nodeId: "reopened-parent" })).toBe(true);
    expect(compatibleOptions(convert.fields[0], options)).toContain(estimate);
    expect(formatMeasurement(estimate.uncertainty!)).toBe("5 ± 0.2");
    expect(resultMeasurement(parentResult, "estimate")?.standardUncertainty).toBe(.2);
    expect(readScienceState(JSON.stringify({ ...core, latest: linkedResult }))?.latest?.uncertainty?.method).toBe("gum");
  });

  it("shows connected value ± uncertainty, and sends explicit Monte Carlo only when selected", async () => {
    const { request } = mount();
    const source = screen.getByRole("combobox", { name: "Import Measured value from a connected result" });
    const estimate = [...(source as HTMLSelectElement).options].find((option) => option.text.includes("estimate · 5 ± 0.2"))!;
    fireEvent.change(source, { target: { value: estimate.value } });
    expect(screen.getByLabelText(/^Measured value(?: \(.+\))?$/)).toHaveValue("5");
    expect(screen.getByText(/Source · Uncertainty · estimate · 5 ± 0.2/)).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Uncertainty propagation method" }),
      { target: { value: "monte_carlo" } });
    fireEvent.click(screen.getByRole("button", { name: "Run calculation" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", [
      "science_core", "run", {
        node_id: "target", operation_id: "convert_unit",
        inputs: { value: 5, from_unit: "mm", to_unit: "m" },
        sources: { value: {
          nodeId: "parent", sourceKey: "c".repeat(32),
          resultHash: "a".repeat(64), receiptHash: "b".repeat(64),
          selector: ["values", "estimate"],
        } },
        uncertainty_method: "monte_carlo",
      },
    ], 615_000);
  });

  it("renders the propagated interval, named input budget and native standard errors", () => {
    mount(JSON.stringify({ ...core, latest: linkedResult }));
    expect(screen.getByText("0.005 ± 0.0002 m")).toBeVisible();
    fireEvent.click(screen.getByText(/Propagated uncertainty budget · first-order GUM/));
    expect(screen.getByRole("table", { name: "Uncertainty budget for value (m)" })).toBeVisible();
    expect(screen.getByText("value (mm)")).toBeVisible();
    expect(screen.getByText(/95% coverage interval/)).toBeVisible();
    expect(screen.getByText(/coverage factor k = 2.1/)).toBeVisible();
  });

  it("does not alter the display of exact values or interpret interval-only values as standard uncertainties", () => {
    const exact: ScienceResult = {
      ...linkedResult, uncertainty: undefined, values: { "value (m)": .005 },
    };
    expect(resultMeasurement(exact, "value (m)")).toBeUndefined();
    expect(resultMeasurement({ ...parentResult, values: {
      estimate: 5, percentile_lower: 4.1, percentile_upper: 5.8,
    } }, "estimate")).toBeUndefined();
  });
  it("keeps connected source units on import and sends them to the SI normalizer", () => {
    const field = { key: "distance", label: "Distance", kind: "number" as const,
      unit: "m", default_unit: "m", dimension: "length (m)", description: "" };
    const option = {
      label: "Length · length_mm", value: 25, unit: "mm",
      uncertainty: { value: 25, standardUncertainty: 0.5,
        coverageFactor: 2, unit: "mm" },
      reference: { nodeId: "source", resultHash: "a".repeat(64), selector: ["values", "length_mm"] },
    };
    const text = connectedInputText(field, option);
    expect(text).toBe("25 mm");
    expect(parseInputs({ id: "length", title: "Length", description: "",
      fields: [field], example: { distance: 1 } }, { distance: text })).toEqual({ distance: "25 mm" });
  });

  it("labels nonlinear Monte Carlo moments and intervals without misleading coverage factors or connections", () => {
    const output = {
      ...linkedResult.uncertainty!.outputs["value (m)"],
      value: 0, standardUncertainty: 1.43, interval: [0, 5] as [number, number],
      method: "monte_carlo" as const, monteCarloMean: 1, monteCarloTrials: 200_000,
    };
    const mc: ScienceResult = {
      ...linkedResult, values: { "value (m)": 0 },
      uncertainty: { ...linkedResult.uncertainty!, method: "monte_carlo",
        outputs: { "value (m)": output } },
    };
    // A nonlinear PDF is centered on its sampled mean, not the nominal f(E[X]).
    expect(formatMeasurement(output)).toBe("nominal 0; Monte Carlo mean 1 ± 1.4 m");
    const parentState = JSON.parse(parent.raw) as Record<string, unknown>;
    const options = sourceOptions([{ ...parent, raw: JSON.stringify({ ...parentState, latest: mc }) }]);
    const linked = options.find((option) => option.reference.selector.join("/") === "values/value (m)");
    expect(linked).toBeDefined();
    expect(linked!.label).toContain("joint PDF");
    expect(compatibleOptions(convert.fields[0], options)).not.toContain(linked);

    mount(JSON.stringify({ ...core, latest: mc }));
    expect(screen.getAllByText(/Monte Carlo mean 1 ± 1.4 m/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText(/Propagated uncertainty budget · Monte Carlo/));
    expect(screen.getByText(/95% Monte Carlo quantile coverage interval/)).toBeVisible();
    expect(screen.getByText(/Gaussian input model/)).toBeVisible();
    expect(screen.queryByText(/coverage factor k =/)).not.toBeInTheDocument();
  });

  it("never presents an intrinsic standard error as complete propagated uncertainty", () => {
    const intrinsic: ScienceResult = {
      ...linkedResult,
      values: { ...linkedResult.values, "value (m)_standard_uncertainty": .3, other_m: 1 },
      uncertainty: {
        ...linkedResult.uncertainty!,
        outputs: { other_m: {
          ...linkedResult.uncertainty!.outputs["value (m)"], value: 1,
          interval: [.5, 1.5],
        } },
        unsupportedOutputs: { "value (m)": "intrinsic covariance is unknown" },
      },
    };
    expect(resultMeasurement({ ...intrinsic, uncertainty: undefined }, "value (m)")?.standardUncertainty).toBe(.3);
    expect(resultMeasurement(intrinsic, "value (m)")).toBeUndefined();
    const sourceState = JSON.parse(parent.raw) as Record<string, unknown>;
    const options = sourceOptions([{ ...parent, raw: JSON.stringify({ ...sourceState, latest: intrinsic }) }]);
    const unsupported = options.find((option) => option.reference.selector.join("/") === "values/value (m)");
    expect(unsupported?.label).toContain("intrinsic covariance is unknown");
    expect(compatibleOptions(convert.fields[0], options)).not.toContain(unsupported);

    mount(JSON.stringify({ ...core, latest: intrinsic }));
    expect(screen.getByText("0.005 · uncertainty unavailable")).toBeVisible();
    fireEvent.click(screen.getByText(/Propagated uncertainty budget/));
    expect(screen.getByText(/Uncertainty propagation unavailable for value \(m\)/)).toBeVisible();
  });

  it("labels a value with the unit its Source unit sibling sets, not the example's", () => {
    const labelled = { ...convert, fields: [
      { ...convert.fields[0], default_unit: "mm", unit_field: "from_unit" }, ...convert.fields.slice(1),
    ] };
    mount(JSON.stringify({ ...core, operations: [labelled] }));
    expect(screen.getByLabelText("Measured value (mm)")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Source unit"), { target: { value: "km" } });
    expect(screen.getByLabelText("Measured value (km)")).toBeInTheDocument();
    expect(screen.queryByText(/Unit: km, set by Source unit/)).not.toBeInTheDocument();
    const help = screen.getByRole("button", { name: "Input and unit help for Measured value" });
    expect(help).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(help);
    expect(help).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Unit: km, set by Source unit/)).toBeVisible();
    fireEvent.click(help);
    expect(screen.queryByText(/Unit: km, set by Source unit/)).not.toBeInTheDocument();
  });

  it("keeps input guidance and operation methods behind accessible disclosures", () => {
    const documented = { ...convert,
      description: "Convert a measured quantity with explicit units.",
      methods: "Multiply by the exact SI conversion factor.",
      citations: [{ label: "Unit reference", url: "https://example.org/units" }],
      fields: [
        { ...convert.fields[0], default_unit: "mm", dimension: "length",
          description: "Enter the measurement in its source unit." },
        ...convert.fields.slice(1),
      ],
    };
    mount(JSON.stringify({ ...core, operations: [documented] }));
    expect(screen.getByRole("combobox", { name: "Operation" })).toHaveValue("convert_unit");
    expect(screen.getByRole("group", { name: "Inputs" })).toBeInTheDocument();
    expect(screen.getByLabelText("Measured value (mm)").closest(".science-input-grid")).toBeInTheDocument();
    expect(screen.queryByText("Enter the measurement in its source unit.")).not.toBeInTheDocument();
    const help = screen.getByRole("button", { name: "Input and unit help for Measured value" });
    fireEvent.click(help);
    expect(screen.getByText("Enter the measurement in its source unit.")).toBeVisible();
    expect(screen.getByText(/Default unit: mm · length/)).toBeVisible();

    const details = screen.getByText("About & methods").closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("About & methods"));
    expect(details).toHaveAttribute("open");
    expect(screen.getByText("Multiply by the exact SI conversion factor.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Unit reference" })).toHaveAttribute("href", "https://example.org/units");
  });

  it("offers no connected results to unit selectors", () => {
    const text = sourceOptions([parent]).map((option) => ({ ...option, value: "infinite" }));
    expect(text.length).toBeGreaterThan(0);
    expect(compatibleOptions(convert.fields[1], text)).toEqual([]);
  });

  it("rounds binary noise in displayed result values", () => {
    const noisy: ScienceResult = { ...linkedResult, uncertainty: undefined,
      values: { "value (m)": 0.05000083799999999 } };
    mount(JSON.stringify({ ...core, latest: noisy }));
    expect(screen.getByText("0.0500008")).toBeVisible();
    expect(screen.queryByText("0.05000083799999999")).toBeNull();
  });

});
