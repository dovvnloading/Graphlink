/**
 * ADR-013 stage 13.1: the frontend half of the ChartSpec contract - a pure
 * TypeScript port of graphlink_chart_data.py's canonicalize_chart_data,
 * validating the SAME rules on the SAME shape (ChartDataRow, contracts/
 * graphlink_scene_payload.py). The backend is still the source of truth for
 * what gets PERSISTED (every chart_data the wire ever carries already went
 * through the Python canonicalizer before this ever runs) - this exists so
 * the client-side renderer (ChartNodeView's interactive spec renderer) can
 * defensively re-validate what it's about to draw rather than trusting the
 * wire blindly, and so a hand-authored spec (this stage's own exit
 * criterion: "a hand-authored spec renders") has one obvious place to be
 * checked before it reaches the renderer.
 */

import type { ChartDataRow, ChartFlowRow } from "../../lib/bridge-core/generated/scene-state";

export const CHART_SPEC_VERSION = 1;
export const SUPPORTED_CHART_TYPES = ["bar", "line", "pie", "histogram", "sankey", "scatter"] as const;
export type SupportedChartType = (typeof SUPPORTED_CHART_TYPES)[number];

const MAX_SERIES_POINTS = 300;
const MAX_NAMED_SERIES = 12;
const MAX_SANKEY_FLOWS = 300;
const MAX_LABEL_LENGTH = 160;
const MAX_TITLE_LENGTH = 200;

export class ChartSpecError extends Error {}

function isSupportedType(value: unknown): value is SupportedChartType {
  return typeof value === "string" && (SUPPORTED_CHART_TYPES as readonly string[]).includes(value);
}

function cleanText(value: unknown, fieldName: string, opts: { default?: string; maxLength?: number } = {}): string {
  const maxLength = opts.maxLength ?? MAX_LABEL_LENGTH;
  const text = String(value ?? "").trim().replace(/\s+/g, " ");
  if (!text) {
    if (opts.default) return opts.default;
    throw new ChartSpecError(`${fieldName} must be non-empty`);
  }
  if (text.length > maxLength) {
    throw new ChartSpecError(`${fieldName} is too long (maximum ${maxLength} characters)`);
  }
  return text;
}

function finiteNumber(value: unknown, fieldName: string): number {
  // Number(null), Number(""), Number([]), and Number("0x10") all succeed
  // in JavaScript, but Python's float(value) rejects these inputs. Keep
  // this defensive frontend validator aligned with the backend parser.
  if (typeof value !== "number" && typeof value !== "string") {
    throw new ChartSpecError(`${fieldName} must be a finite number`);
  }
  if (typeof value === "string" && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value.trim())) {
    throw new ChartSpecError(`${fieldName} must be a finite number`);
  }
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) throw new ChartSpecError(`${fieldName} must be a finite number`);
  return num;
}


/** Additive scientific fields. The generated v1 wire validator accepts
 * unknown properties, but not a new nested type enum: the outer chartType
 * remains the scatter discriminator, and nested type is omitted for scatter. */
export type ErrorPair = [number, number];
export type AxisScale = "linear" | "log10";
export interface ScientificSeries {
  name: string;
  values: number[];
  xError?: ErrorPair[] | null;
  yError?: ErrorPair[] | null;
}
export interface ScientificChartSpec {
  version?: ChartDataRow["version"];
  type?: ChartDataRow["type"];
  title?: ChartDataRow["title"];
  labels?: ChartDataRow["labels"];
  values?: ChartDataRow["values"];
  xAxis?: ChartDataRow["xAxis"];
  yAxis?: ChartDataRow["yAxis"];
  xValues?: number[];
  series?: ScientificSeries[];
  xError?: ErrorPair[] | null;
  yError?: ErrorPair[] | null;
  xScale?: AxisScale;
  yScale?: AxisScale;
  xUnit?: string;
  yUnit?: string;
};
export function scientificFields(spec: ChartDataRow): ScientificChartSpec {
  return spec as ScientificChartSpec;
}
export function usesScientificMarks(data: ChartDataRow, chartType: string): boolean {
  return chartType === "scatter" ||
    ["xValues", "series", "xError", "yError", "xScale", "yScale", "xUnit", "yUnit"].some((key) => key in data);
}

function scientificErrors(value: unknown, field: string, count: number): ErrorPair[] | null {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value)) throw new ChartSpecError(field + " must be a list");
  if (value.length !== count) {
    throw new ChartSpecError(field + " length must match the series length (" + count + ")");
  }
  return value.map((entry, index): ErrorPair => {
    const name = field + " at index " + index;
    let minus: number;
    let plus: number;
    if (Array.isArray(entry)) {
      if (entry.length !== 2) throw new ChartSpecError(name + " must have exactly two magnitudes");
      minus = finiteNumber(entry[0], name + " lower magnitude");
      plus = finiteNumber(entry[1], name + " upper magnitude");
    } else {
      minus = plus = finiteNumber(entry, name);
    }
    if (minus < 0 || plus < 0) throw new ChartSpecError(name + " magnitudes must be nonnegative");
    return [minus, plus];
  });
}

function checkScientificAxis(values: number[], errors: ErrorPair[] | null | undefined, scale: AxisScale, field: string): void {
  let smallest = Infinity;
  let largest = -Infinity;
  values.forEach((value, index) => {
    const [minus, plus] = errors?.[index] ?? [0, 0];
    const lower = value - minus;
    const upper = value + plus;
    if (!Number.isFinite(lower) || !Number.isFinite(upper)) {
      throw new ChartSpecError(field + " at index " + index + " has a non-finite error endpoint");
    }
    if (scale === "log10" && lower <= 0) {
      throw new ChartSpecError(field + " at index " + index + " and its lower error endpoint must be greater than zero on a log10 axis");
    }
    smallest = Math.min(smallest, lower);
    largest = Math.max(largest, upper);
  });
  if (!Number.isFinite(largest - smallest)) {
    throw new ChartSpecError(field + " range including error endpoints must be finite");
  }
}

interface ScientificValues {
  name?: string;
  values: number[];
  xError?: ErrorPair[] | null;
  yError?: ErrorPair[] | null;
}

function canonicalizeScientific(raw: Record<string, unknown>, type: "line" | "scatter", result: ChartDataRow): ChartDataRow {
  const output = result as ScientificChartSpec;
  if (type === "scatter" && raw.xValues == null) {
    throw new ChartSpecError("Scatter charts require numeric xValues");
  }
  let xValues: number[] | undefined;
  if (raw.xValues != null) {
    if (!Array.isArray(raw.xValues)) throw new ChartSpecError("xValues must be a list");
    if (raw.xValues.length === 0) throw new ChartSpecError("xValues require at least one data point");
    if (raw.xValues.length > MAX_SERIES_POINTS) throw new ChartSpecError("Charts support at most 300 data points");
    xValues = raw.xValues.map((value, i) => finiteNumber(value, "xValues at index " + i));
    output.xValues = xValues;
  }
  for (const axis of ["xScale", "yScale"] as const) {
    const scale = raw[axis] ?? "linear";
    if (scale !== "linear" && scale !== "log10") throw new ChartSpecError(axis + " must be linear or log10");
    output[axis] = scale;
  }
  if (output.xScale === "log10" && !xValues) {
    throw new ChartSpecError("A log10 x-axis requires numeric xValues");
  }
  for (const field of ["xUnit", "yUnit"] as const) {
    if (raw[field] != null) output[field] = cleanText(raw[field], field);
  }
  if (raw.labels != null) {
    if (!Array.isArray(raw.labels)) throw new ChartSpecError("Labels must be a list");
    output.labels = raw.labels.map((label, i) => cleanText(label, "Label at index " + i));
  }
  const series: ScientificValues[] = [];
  if (raw.series != null) {
    if (!Array.isArray(raw.series) || raw.series.length === 0) {
      throw new ChartSpecError("series must be a nonempty list");
    }
    if (raw.series.length > MAX_NAMED_SERIES) throw new ChartSpecError("Charts support at most 12 named series");
    if ("values" in raw || "xError" in raw || "yError" in raw) {
      throw new ChartSpecError("Named series cannot be combined with top-level values or errors");
    }
    const names = new Set<string>();
    raw.series.forEach((entry, index) => {
      if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
        throw new ChartSpecError("series at index " + index + " must be an object");
      }
      const item = entry as Record<string, unknown>;
      const name = cleanText(item.name, "series at index " + index + " name");
      if (names.has(name)) throw new ChartSpecError("Duplicate series name: " + name);
      names.add(name);
      if (!Array.isArray(item.values)) throw new ChartSpecError("series " + name + " values must be a list");
      if (!item.values.length) throw new ChartSpecError("series " + name + " requires at least one data point");
      if (item.values.length > MAX_SERIES_POINTS) throw new ChartSpecError("Charts support at most 300 data points");
      const values = item.values.map((value, i) => finiteNumber(value, "series " + name + " value at index " + i));
      const row: ScientificValues = { name, values };
      for (const field of ["xError", "yError"] as const) {
        if (field in item) row[field] = scientificErrors(item[field], "series " + name + " " + field, values.length);
      }
      series.push(row);
    });
    output.series = series as ScientificSeries[];
  } else {
    if (!Array.isArray(raw.values) || !raw.values.length) {
      throw new ChartSpecError(type[0].toUpperCase() + type.slice(1) + " charts require at least one data point");
    }
    if (raw.values.length > MAX_SERIES_POINTS) throw new ChartSpecError("Charts support at most 300 data points");
    const values = raw.values.map((value, i) => finiteNumber(value, "Value at index " + i));
    const row: ScientificValues = { values };
    output.values = values;
    for (const field of ["xError", "yError"] as const) {
      if (field in raw) {
        row[field] = scientificErrors(raw[field], field, values.length);
        output[field] = row[field];
      }
    }
    series.push(row);
  }
  const count = series[0].values.length;
  if (count > MAX_SERIES_POINTS || series.length * count > MAX_SERIES_POINTS) {
    throw new ChartSpecError("Charts support at most 300 total series points");
  }
  if (type === "line" && count < 2) throw new ChartSpecError("Line charts require at least two data points");
  for (const row of series) {
    if (row.values.length !== count) {
      throw new ChartSpecError("series " + (row.name ?? "values") + " length must match the other series (" + count + ")");
    }
  }
  if (xValues && xValues.length !== count) {
    throw new ChartSpecError("xValues length must match the series length (" + count + ")");
  }
  if (output.labels && output.labels.length !== count) {
    throw new ChartSpecError("labels length must match the series length (" + count + ")");
  }
  if (type === "line" && !xValues && !output.labels) throw new ChartSpecError("Line charts require labels or numeric xValues");
  if (!xValues && series.some((row) => row.xError != null)) {
    throw new ChartSpecError("xError requires numeric xValues");
  }
  if (xValues) series.forEach((row) => checkScientificAxis(xValues!, row.xError, output.xScale!, "xValues"));
  series.forEach((row) => checkScientificAxis(row.values, row.yError, output.yScale!, "series " + (row.name ?? "values") + " values"));
  const allY = series.flatMap((row) => row.values);
  if (xValues && !Number.isFinite(Math.max(...xValues) - Math.min(...xValues))) {
    throw new ChartSpecError("xValues range must be finite");
  }
  if (!Number.isFinite(Math.max(...allY) - Math.min(...allY))) {
    throw new ChartSpecError("values range must be finite");
  }
  output.xAxis = cleanText(raw.xAxis, "X-axis label", { default: type === "scatter" ? "X" : "Sequence" });
  output.yAxis = cleanText(raw.yAxis, "Y-axis label", { default: type === "scatter" ? "Y" : "Value" });
  return output;
}

interface RawSankeyFlow {
  source: unknown;
  target: unknown;
  value: unknown;
}

/**
 * Mirrors graphlink_chart_data.py's _legacy_sankey_flows: the pre-"flows"-key
 * sankey shape, `{data: {nodes: [...], links: [...]}}`. Returns null when
 * `data` isn't a nested object with list-typed nodes/links (the caller then
 * falls through to the standard "at least one flow" rejection), otherwise a
 * raw, unvalidated flows array for the normal aggregation/validation loop
 * below to clean and check exactly as it does for the direct flows-array
 * shape.
 */
function legacySankeyFlows(payload: Record<string, unknown>): RawSankeyFlow[] | null {
  const nested = payload.data;
  if (typeof nested !== "object" || nested === null || Array.isArray(nested)) return null;
  const nestedRecord = nested as Record<string, unknown>;
  const nodes = nestedRecord.nodes ?? [];
  const links = nestedRecord.links ?? [];
  if (!Array.isArray(nodes) || !Array.isArray(links)) return null;

  // Bounds checked BEFORE names/flows are built below, not after - mirrors
  // the Python side's own comment: an oversized legacy payload must be
  // rejected in O(1), not after two full O(n) passes over nodes and links.
  if (links.length > MAX_SANKEY_FLOWS) {
    throw new ChartSpecError(`Sankey charts support at most ${MAX_SANKEY_FLOWS} flows`);
  }
  if (nodes.length > MAX_SANKEY_FLOWS + 1) {
    throw new ChartSpecError(`Sankey charts support at most ${MAX_SANKEY_FLOWS + 1} nodes`);
  }

  const names: string[] = nodes.map((node: unknown, index: number) => {
    const nodeName =
      typeof node === "object" && node !== null && !Array.isArray(node)
        ? ((node as Record<string, unknown>).name ?? `Node ${index}`)
        : node;
    return String(nodeName ?? "").trim();
  });

  return links.map((link: unknown): RawSankeyFlow => {
    if (typeof link !== "object" || link === null || Array.isArray(link)) {
      throw new ChartSpecError("Sankey links must be objects");
    }
    const linkRecord = link as Record<string, unknown>;
    let source = linkRecord.source;
    let target = linkRecord.target;
    if (typeof source === "number" && Number.isInteger(source) && source >= 0 && source < names.length) {
      source = names[source];
    }
    if (typeof target === "number" && Number.isInteger(target) && target >= 0 && target < names.length) {
      target = names[target];
    }
    return { source, target, value: linkRecord.value };
  });
}

/**
 * Mirrors canonicalize_chart_data(data, chart_type) exactly - same field
 * names, same defaults, same caps, same rejection messages where feasible.
 * Never mutates `data`. Throws ChartSpecError on anything the backend would
 * also reject.
 */
export function canonicalizeChartSpec(data: unknown, chartType?: string | null): ChartDataRow {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    throw new ChartSpecError("Chart payload must be an object");
  }
  const raw = data as Record<string, unknown>;

  const expectedType = String(chartType || raw.type || "").trim().toLowerCase();
  if (!isSupportedType(expectedType)) {
    throw new ChartSpecError(`Unsupported chart type: ${expectedType || "unknown"}`);
  }
  const actualType = String(raw.type || expectedType).trim().toLowerCase();
  if (actualType !== expectedType) {
    throw new ChartSpecError(`Chart payload type must be ${expectedType}`);
  }

  const result: ChartDataRow = {
    version: CHART_SPEC_VERSION,
    type: expectedType === "scatter" ? undefined : expectedType,
    title: cleanText(raw.title, "Chart title", {
      default: `${expectedType[0].toUpperCase()}${expectedType.slice(1)} Chart`,
      maxLength: MAX_TITLE_LENGTH,
    }),
  };

  if (expectedType === "scatter" || (expectedType === "line" && usesScientificMarks(raw as ChartDataRow, expectedType))) {
    return canonicalizeScientific(raw, expectedType, result);
  }

  if (expectedType === "bar" || expectedType === "line" || expectedType === "pie") {
    const labels = raw.labels;
    const values = raw.values;
    if (!Array.isArray(labels) || !Array.isArray(values)) {
      throw new ChartSpecError("Labels and values must both be lists");
    }
    if (labels.length === 0 || values.length === 0) {
      throw new ChartSpecError(`${expectedType} charts require at least one data point`);
    }
    if (labels.length !== values.length) {
      throw new ChartSpecError("Labels and values must have the same length");
    }
    if (labels.length > MAX_SERIES_POINTS) {
      throw new ChartSpecError(`Charts support at most ${MAX_SERIES_POINTS} data points`);
    }
    if (expectedType === "line" && labels.length < 2) {
      throw new ChartSpecError("Line charts require at least two data points");
    }
    result.labels = labels.map((label, index) => cleanText(label, `Label at index ${index}`));
    result.values = values.map((value, index) => finiteNumber(value, `Value at index ${index}`));
    if (expectedType === "pie" && result.values.some((value) => value <= 0)) {
      throw new ChartSpecError("Pie chart values must be greater than zero");
    }
    result.xAxis = cleanText(raw.xAxis, "X-axis label", {
      default: expectedType === "bar" ? "Category" : "Sequence",
    });
    result.yAxis = cleanText(raw.yAxis, "Y-axis label", { default: "Value" });
    return result;
  }

  if (expectedType === "histogram") {
    const values = raw.values;
    if (!Array.isArray(values) || values.length < 2) {
      throw new ChartSpecError("Histogram charts require at least two values");
    }
    if (values.length > MAX_SERIES_POINTS) {
      throw new ChartSpecError(`Histograms support at most ${MAX_SERIES_POINTS} values`);
    }
    const bins = finiteNumber(raw.bins ?? 10, "Histogram bins");
    if (bins < 2) throw new ChartSpecError("Histogram bins must be at least 2");
    result.values = values.map((value, index) => finiteNumber(value, `Value at index ${index}`));
    result.bins = Math.max(2, Math.min(Math.trunc(bins), 24, result.values.length));
    result.xAxis = cleanText(raw.xAxis, "X-axis label", { default: "Value" });
    result.yAxis = cleanText(raw.yAxis, "Y-axis label", { default: "Frequency" });
    return result;
  }

  // sankey
  let rawFlows: unknown = raw.flows;
  if (rawFlows === undefined || rawFlows === null) {
    rawFlows = legacySankeyFlows(raw);
  }
  if (!Array.isArray(rawFlows) || rawFlows.length === 0) {
    throw new ChartSpecError("Sankey charts require at least one flow");
  }
  if (rawFlows.length > MAX_SANKEY_FLOWS) {
    throw new ChartSpecError(`Sankey charts support at most ${MAX_SANKEY_FLOWS} flows`);
  }
  const aggregated = new Map<string, ChartFlowRow>();
  rawFlows.forEach((flow, index) => {
    if (typeof flow !== "object" || flow === null || Array.isArray(flow)) {
      throw new ChartSpecError(`Sankey flow at index ${index} must be an object`);
    }
    const flowRecord = flow as Record<string, unknown>;
    const source = cleanText(flowRecord.source, `Sankey source at index ${index}`);
    const target = cleanText(flowRecord.target, `Sankey target at index ${index}`);
    const value = finiteNumber(flowRecord.value, `Sankey value at index ${index}`);
    if (value <= 0) throw new ChartSpecError(`Sankey value at index ${index} must be greater than zero`);
    // A pair's boundary must be unambiguous even if a user-provided
    // source/target contains the delimiter (NUL is valid in JSON strings).
    const key = JSON.stringify([source, target]);
    const existing = aggregated.get(key);
    const total = (existing?.value ?? 0) + value;
    if (!Number.isFinite(total)) throw new ChartSpecError("Sankey flow totals must be finite");
    aggregated.set(key, { source, target, value: total });
  });
  const flows = [...aggregated.values()].sort((a, b) =>
    a.source === b.source ? a.target.localeCompare(b.target) : a.source.localeCompare(b.source),
  );
  // Distinct edges can also overflow source or sink node throughput.
  const outgoingTotals = new Map<string, number>();
  const incomingTotals = new Map<string, number>();
  for (const flow of flows) {
    const out = (outgoingTotals.get(flow.source) ?? 0) + flow.value;
    const input = (incomingTotals.get(flow.target) ?? 0) + flow.value;
    if (!Number.isFinite(out) || !Number.isFinite(input)) {
      throw new ChartSpecError("Sankey node totals must be finite");
    }
    outgoingTotals.set(flow.source, out);
    incomingTotals.set(flow.target, input);
  }
  assertAcyclic(flows);
  result.flows = flows;
  return result;
}

function assertAcyclic(flows: ChartFlowRow[]): void {
  const adjacency = new Map<string, Set<string>>();
  for (const flow of flows) {
    if (!adjacency.has(flow.source)) adjacency.set(flow.source, new Set());
    adjacency.get(flow.source)!.add(flow.target);
    if (!adjacency.has(flow.target)) adjacency.set(flow.target, new Set());
  }
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const visit = (node: string): void => {
    if (visiting.has(node)) throw new ChartSpecError("Sankey charts cannot contain cycles");
    if (visited.has(node)) return;
    visiting.add(node);
    for (const target of [...(adjacency.get(node) ?? [])].sort()) visit(target);
    visiting.delete(node);
    visited.add(node);
  };
  for (const node of [...adjacency.keys()].sort()) visit(node);
}
