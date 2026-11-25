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

import { linearFits } from "./charts/linearFit";
import type { ChartBoxplotGroupRow, ChartDataRow, ChartFlowRow, ChartScientificSeriesRow } from "../../lib/bridge-core/generated/scene-state";

export const CHART_SPEC_VERSION = 1;
export const SUPPORTED_CHART_TYPES = ["bar", "line", "pie", "histogram", "sankey", "scatter", "boxplot", "heatmap", "composite"] as const;
export type SupportedChartType = (typeof SUPPORTED_CHART_TYPES)[number];

const MAX_SERIES_POINTS = 300;
const MAX_SCIENTIFIC_OBSERVATIONS = 10_000;
const MAX_NAMED_SERIES = 12;
const MAX_SANKEY_FLOWS = 300;
const MAX_LABEL_LENGTH = 160;
const MAX_TITLE_LENGTH = 200;

export class ChartSpecError extends Error {}

/** Print export settings are ephemeral, not persisted in the v1 ChartSpec. */
export type PublicationFormat = "svg" | "pdf" | "png";
export type PublicationSize = "single" | "double" | "custom";
export interface PublicationExportOptions {
  format: PublicationFormat;
  size: PublicationSize;
  widthMm?: number;
  heightMm?: number;
  dpi: 300 | 600;
  colorblindSafe: boolean;
  grayscaleSafe: boolean;
}
export interface ResolvedPublicationExportOptions extends Omit<PublicationExportOptions, "widthMm" | "heightMm"> {
  widthMm: number;
  heightMm: number;
}

export const PUBLICATION_PRESET_MM = { single: 85, double: 180 } as const;

/** Mirrors graphlink_chart_data.validate_publication_export, including the
 * pixel-allocation safety cap; physical sizes never depend on display DPI. */
export function resolvePublicationExportOptions(
  options: PublicationExportOptions,
  aspectRatio: number,
): ResolvedPublicationExportOptions {
  if (!options || !["png", "svg", "pdf"].includes(options.format)) {
    throw new ChartSpecError("Export format must be svg, pdf or png");
  }
  const allowed = new Set([
    "format", "size", "widthMm", "heightMm", "dpi", "colorblindSafe", "grayscaleSafe",
  ]);
  if (Object.keys(options).some((key) => !allowed.has(key))) {
    throw new ChartSpecError("Unknown publication export option");
  }
  if (options.dpi !== 300 && options.dpi !== 600) {
    throw new ChartSpecError("Export DPI must be 300 or 600");
  }
  if (typeof options.colorblindSafe !== "boolean" || typeof options.grayscaleSafe !== "boolean") {
    throw new ChartSpecError("Export palette options must be booleans");
  }
  if (!Number.isFinite(aspectRatio) || aspectRatio <= 0) {
    throw new ChartSpecError("Invalid chart aspect ratio");
  }
  const size = options.size;
  if (size !== "single" && size !== "double" && size !== "custom") {
    throw new ChartSpecError("Export size must be single, double or custom");
  }
  if (size !== "custom" && (options.widthMm !== undefined || options.heightMm !== undefined)) {
    throw new ChartSpecError("Preset sizes derive dimensions from chart aspect ratio");
  }
  const widthMm = size === "custom" ? options.widthMm : PUBLICATION_PRESET_MM[size];
  const heightMm = size === "custom" ? options.heightMm : (widthMm ?? 0) * aspectRatio;
  if (typeof widthMm !== "number" || typeof heightMm !== "number"
      || !Number.isFinite(widthMm) || !Number.isFinite(heightMm)
      || widthMm < 35 || widthMm > 300 || heightMm < 30 || heightMm > 300) {
    throw new ChartSpecError("Publication dimensions must be 35-300 mm wide and 30-300 mm high");
  }
  if ((widthMm / 25.4 * options.dpi) * (heightMm / 25.4 * options.dpi) > 30_000_000) {
    throw new ChartSpecError("Export dimensions exceed the 30 megapixel safety limit");
  }
  return { ...options, widthMm, heightMm };
}

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


/** Error pairs and axis scales are defined by the generated scene wire type.
 * The canonicalizer checks that each pair contains exactly two magnitudes. */
/** Persisted user-authored analysis metadata; absent for all unedited charts. */
export type ChartAnnotation =
  | { kind: "label"; x: number; y: number; text: string }
  | { kind: "arrow"; x: number; y: number; x2: number; y2: number }
  | { kind: "region"; axis: "x" | "y"; from: number; to: number };

export interface ChartAnalysis {
  lockX: boolean;
  lockY: boolean;
  xRange?: [number, number];
  yRange?: [number, number];
  annotations: ChartAnnotation[];
  regression?: "ols95";
  distribution?: "raincloud";
  clustering?: "rows" | "columns" | "both";
  density?: "hexbin";
}

export const EMPTY_CHART_ANALYSIS: ChartAnalysis = { lockX: false, lockY: false, annotations: [] };

export function chartAnalysisOf(data: ChartDataRow): ChartAnalysis {
  return (data.analysis as ChartAnalysis | null | undefined) ?? EMPTY_CHART_ANALYSIS;
}

/** Mirror graphlink_chart_data.canonicalize_chart_analysis; never silently
 * drop malformed annotations or reinterpret real data as view coordinates. */
export function canonicalizeChartAnalysis(
  value: unknown, type: string, xScale: AxisScale = "linear", yScale: AxisScale = "linear",
  data?: ChartDataRow,
): ChartAnalysis {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ChartSpecError("Chart analysis must be an object");
  }
  if (!["line", "scatter", "histogram", "boxplot", "heatmap"].includes(type)) {
    throw new ChartSpecError("Chart analysis is only supported on cartesian analysis charts");
  }
  const raw = value as Record<string, unknown>;
  if (Object.keys(raw).some((key) => !["lockX", "lockY", "xRange", "yRange", "annotations", "regression", "distribution", "clustering", "density"].includes(key))) {
    throw new ChartSpecError("Unknown chart analysis field");
  }
  const result: ChartAnalysis = { lockX: false, lockY: false, annotations: [] };
  for (const [axis, scale] of [["x", xScale], ["y", yScale]] as const) {
    const lock = axis === "x" ? "lockX" : "lockY";
    const range = axis === "x" ? "xRange" : "yRange";
    const locked = raw[lock] ?? false;
    if (typeof locked !== "boolean") throw new ChartSpecError(lock + " must be a boolean");
    result[lock] = locked;
    if (range in raw) {
      const bounds = raw[range];
      if (!Array.isArray(bounds) || bounds.length !== 2) {
        throw new ChartSpecError(range + " must contain two bounds");
      }
      const low = finiteNumber(bounds[0], range + " minimum");
      const high = finiteNumber(bounds[1], range + " maximum");
      if (!(low < high) || !Number.isFinite(high - low)) {
        throw new ChartSpecError(range + " must increase across a finite span");
      }
      if (scale === "log10" && low <= 0) {
        throw new ChartSpecError(range + " must be positive on a log10 axis");
      }
      result[range] = [low, high];
    }
    if (locked && !result[range]) throw new ChartSpecError(lock + " requires " + range);
  }
  if ("regression" in raw) {
    if (raw.regression !== "ols95" || !["line", "scatter"].includes(type) || xScale !== "linear"
        || yScale !== "linear" || !data || !linearFits(data)) {
      throw new ChartSpecError("Linear regression requires at least three numeric observations, varying X, linear axes and finite confidence bounds");
    }
    result.regression = "ols95";
  }
  if ("distribution" in raw) {
    if (type !== "boxplot" || raw.distribution !== "raincloud") {
      throw new ChartSpecError("Raincloud distributions require a box plot");
    }
    result.distribution = "raincloud";
  }
  if ("clustering" in raw) {
    if (type !== "heatmap" || !["rows", "columns", "both"].includes(raw.clustering as string)) {
      throw new ChartSpecError("Heatmap clustering requires a heatmap and rows, columns, or both");
    }
    result.clustering = raw.clustering as "rows" | "columns" | "both";
  }
  if ("density" in raw) {
    if (type !== "scatter" || raw.density !== "hexbin" || !data
        || (data.series?.length ?? 1) !== 1 || xScale !== "linear" || yScale !== "linear") {
      throw new ChartSpecError("Hexbin density requires single-series scatter with linear axes");
    }
    result.density = "hexbin";
  }
  if (raw.annotations !== undefined && (!Array.isArray(raw.annotations) || raw.annotations.length > 60)) {
    throw new ChartSpecError("Chart annotations must be a list of at most 60 items");
  }
  for (const [index, entry] of ((raw.annotations ?? []) as unknown[]).entries()) {
    const where = "Annotation " + index;
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new ChartSpecError(where + " must be an object");
    }
    const item = entry as Record<string, unknown>;
    const fields: Record<string, string[]> = {
      label: ["kind", "x", "y", "text"],
      arrow: ["kind", "x", "y", "x2", "y2"],
      region: ["kind", "axis", "from", "to"],
    };
    const kind = item.kind;
    if (typeof kind !== "string" || !fields[kind] ||
        Object.keys(item).length !== fields[kind].length ||
        Object.keys(item).some((key) => !fields[kind].includes(key))) {
      throw new ChartSpecError(where + " has invalid fields");
    }
    const checked = (field: string, scale: AxisScale) => {
      const numeric = finiteNumber(item[field], where + " " + field);
      if (scale === "log10" && numeric <= 0) {
        throw new ChartSpecError(where + " coordinates must be positive on log10 axes");
      }
      return numeric;
    };
    if (kind === "label") {
      result.annotations.push({
        kind, x: checked("x", xScale), y: checked("y", yScale),
        text: cleanText(item.text, where + " text"),
      });
    } else if (kind === "arrow") {
      result.annotations.push({
        kind, x: checked("x", xScale), y: checked("y", yScale),
        x2: checked("x2", xScale), y2: checked("y2", yScale),
      });
    } else {
      if (item.axis !== "x" && item.axis !== "y") {
        throw new ChartSpecError(where + " axis must be x or y");
      }
      const start = checked("from", item.axis === "x" ? xScale : yScale);
      const end = checked("to", item.axis === "x" ? xScale : yScale);
      if (!(start < end) || !Number.isFinite(end - start)) {
        throw new ChartSpecError(where + " region must have increasing finite bounds");
      }
      result.annotations.push({ kind: "region", axis: item.axis, from: start, to: end });
    }
  }
  if (type === "heatmap" && (result.lockX || result.lockY || result.xRange || result.yRange || result.annotations.length)) {
    throw new ChartSpecError("Heatmap analysis supports only clustering");
  }
  return result;
}

export type ErrorPair = NonNullable<ChartDataRow["xError"]>[number];
export type AxisScale = NonNullable<ChartDataRow["xScale"]>;

export function usesScientificMarks(data: object, chartType: string): boolean {
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
  const output = result;
  if (type === "scatter" && raw.xValues == null) {
    throw new ChartSpecError("Scatter charts require numeric xValues");
  }
  let xValues: number[] | undefined;
  if (raw.xValues != null) {
    if (!Array.isArray(raw.xValues)) throw new ChartSpecError("xValues must be a list");
    if (raw.xValues.length === 0) throw new ChartSpecError("xValues require at least one data point");
    if (raw.xValues.length > MAX_SCIENTIFIC_OBSERVATIONS) throw new ChartSpecError("Scientific charts support at most 10000 data points");
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
    const namedSeries: ChartScientificSeriesRow[] = [];
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
      if (item.values.length > MAX_SCIENTIFIC_OBSERVATIONS) throw new ChartSpecError("Scientific charts support at most 10000 data points");
      const values = item.values.map((value, i) => finiteNumber(value, "series " + name + " value at index " + i));
      const row: ChartScientificSeriesRow = { name, values };
      for (const field of ["xError", "yError"] as const) {
        if (field in item) row[field] = scientificErrors(item[field], "series " + name + " " + field, values.length);
      }
      series.push(row);
      namedSeries.push(row);
    });
    output.series = namedSeries;
  } else {
    if (!Array.isArray(raw.values) || !raw.values.length) {
      throw new ChartSpecError(type[0].toUpperCase() + type.slice(1) + " charts require at least one data point");
    }
    if (raw.values.length > MAX_SCIENTIFIC_OBSERVATIONS) throw new ChartSpecError("Scientific charts support at most 10000 data points");
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
  for (const row of series) {
    if (row.values.length !== count) {
      throw new ChartSpecError("series " + (row.name ?? "values") + " length must match the other series (" + count + ")");
    }
  }
  if (type === "line" && count < 2) throw new ChartSpecError("Line charts require at least two data points");
  if (count > MAX_SCIENTIFIC_OBSERVATIONS || series.length * count > MAX_SCIENTIFIC_OBSERVATIONS) {
    throw new ChartSpecError("Scientific charts support at most 10000 total series points");
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
  // Separate series may have finite individual error spans yet overflow
  // when their intervals are combined into the plot's shared axis.
  for (const field of ["xValues", "values"] as const) {
    if (field === "xValues" && !xValues) continue;
    let smallest = Infinity;
    let largest = -Infinity;
    for (const row of series) {
      const coordinates = field === "xValues" ? xValues! : row.values;
      const errors = field === "xValues" ? row.xError : row.yError;
      coordinates.forEach((value, index) => {
        const [minus, plus] = errors?.[index] ?? [0, 0];
        smallest = Math.min(smallest, value - minus);
        largest = Math.max(largest, value + plus);
      });
    }
    if (!Number.isFinite(largest - smallest)) {
      throw new ChartSpecError(field + " range including all series errors must be finite");
    }
  }
  output.xAxis = cleanText(raw.xAxis, "X-axis label", { default: type === "scatter" ? "X" : "Sequence" });
  output.yAxis = cleanText(raw.yAxis, "Y-axis label", { default: type === "scatter" ? "Y" : "Value" });
  return output;
}

function canonicalizeBoxplot(raw: Record<string, unknown>, result: ChartDataRow): ChartDataRow {
  if (!Array.isArray(raw.groups) || !raw.groups.length) {
    throw new ChartSpecError("Boxplot charts require a nonempty groups list");
  }
  if (raw.groups.length > MAX_NAMED_SERIES) throw new ChartSpecError("Boxplots support at most 12 groups");
  const seen = new Set<string>();
  let total = 0;
  let smallest = Infinity;
  let largest = -Infinity;
  const groups: ChartBoxplotGroupRow[] = raw.groups.map((entry, index) => {
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) {
      throw new ChartSpecError("Boxplot group at index " + index + " must be an object");
    }
    const item = entry as Record<string, unknown>;
    const label = cleanText(item.label, "Boxplot group at index " + index + " label");
    if (seen.has(label)) throw new ChartSpecError("Duplicate boxplot group label: " + label);
    seen.add(label);
    if (!Array.isArray(item.values) || item.values.length < 2) {
      throw new ChartSpecError("Boxplot group " + label + " requires at least two observations");
    }
    total += item.values.length;
    if (total > MAX_SERIES_POINTS) throw new ChartSpecError("Boxplots support at most 300 observations total");
    const values = item.values.map((value, i) => finiteNumber(value, "Boxplot group " + label + " value at index " + i));
    smallest = Math.min(smallest, ...values);
    largest = Math.max(largest, ...values);
    return { label, values };
  });
  if (!Number.isFinite(largest - smallest)) throw new ChartSpecError("Boxplot observation range must be finite");
  result.groups = groups;
  result.xAxis = cleanText(raw.xAxis, "X-axis label", { default: "Group" });
  result.yAxis = cleanText(raw.yAxis, "Y-axis label", { default: "Value" });
  return result;
}

/** Strict V2 grammar: every layer references bounded, typed source columns.
 * No execution, expression, fetch, URL, or arbitrary renderer properties. */
function canonicalizeComposite(raw: Record<string, unknown>, result: ChartDataRow): ChartDataRow {
  if (raw.version != null && raw.version !== 2) {
    throw new ChartSpecError("Composite figures require ChartSpec version 2");
  }
  if (Object.keys(raw).some(key => ![
    "version", "type", "title", "composite", "xAxis", "yAxis",
    "xScale", "yScale", "xUnit", "yUnit", "scienceReceipt",
  ].includes(key))) throw new ChartSpecError("Unsupported composite figure field");

  const data = raw.composite;
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new ChartSpecError("Composite data must be an object");
  }
  const spec = data as Record<string, unknown>;
  if (Object.keys(spec).some(key => !["numbers", "categories", "layers", "facetBy"].includes(key))) {
    throw new ChartSpecError("Unsupported composite figure field");
  }
  const rawNumbers = spec.numbers;
  const rawCategories = spec.categories ?? {};
  const rawLayers = spec.layers;
  const isObject = (v: unknown): v is Record<string, unknown> =>
    v !== null && typeof v === "object" && !Array.isArray(v);
  if (!isObject(rawNumbers) || Object.keys(rawNumbers).length < 2 || Object.keys(rawNumbers).length > 12) {
    throw new ChartSpecError("Composite figures require 2-12 numeric columns");
  }
  if (!isObject(rawCategories) || Object.keys(rawCategories).length > 4) {
    throw new ChartSpecError("Composite figures support at most four categorical columns");
  }
  if (!Array.isArray(rawLayers) || rawLayers.length < 1 || rawLayers.length > 8) {
    throw new ChartSpecError("Composite figures require 1-8 mark layers");
  }
  const numbers: Record<string, number[]> = {};
  const categories: Record<string, string[]> = {};
  let count: number | null = null;
  for (const [source, target, kind] of [
    [rawNumbers, numbers, "numeric"], [rawCategories, categories, "categorical"],
  ] as const) {
    for (const [key, values] of Object.entries(source)) {
      if (key !== key.trim().replace(/\s+/g, " ") || !key || key.length > 80
          || Object.hasOwn(numbers, key) || Object.hasOwn(categories, key)
          || ["__proto__", "constructor", "prototype"].includes(key)) {
        throw new ChartSpecError("Duplicate or invalid composite column: " + key);
      }
      if (!Array.isArray(values)) throw new ChartSpecError("Composite columns must contain arrays");
      if (count === null) count = values.length;
      if (values.length !== count || count < 2 || count > 2000) {
        throw new ChartSpecError("Composite columns must have 2-2000 aligned observations");
      }
      if (kind === "numeric") {
        (target as Record<string, number[]>)[key] = values.map((value, index) =>
          finiteNumber(value, "Composite " + key + "[" + index + "]"));
        const col = (target as Record<string, number[]>)[key];
        if (!Number.isFinite(Math.max(...col) - Math.min(...col))) {
          throw new ChartSpecError("Composite numeric column range must be finite");
        }
      } else {
        const cleaned = values.map((value, index) => cleanText(value,
          "Composite " + key + "[" + index + "]"));
        if (new Set(cleaned).size > 30) throw new ChartSpecError("Composite categories exceed 30 values");
        (target as Record<string, string[]>)[key] = cleaned;
      }
    }
  }
  const facetBy = spec.facetBy;
  if (facetBy !== undefined && (typeof facetBy !== "string" || !Object.hasOwn(categories, facetBy))) {
    throw new ChartSpecError("Composite facetBy must name a categorical column");
  }
  if (typeof facetBy === "string" && new Set(categories[facetBy]).size > 6) {
    throw new ChartSpecError("Composite figures support at most six facets");
  }

  const layers: NonNullable<ChartDataRow["composite"]>["layers"] = [];
  // One global color/legend slot per (categorical field, value) or
  // ungrouped layer. Reusing a field across layers does not spend slots twice.
  const colorSlots = new Set<string>();
  const xBounds: number[] = [];
  const yBounds: number[] = [];
  for (const [index, entry] of rawLayers.entries()) {
    if (!isObject(entry) || Object.keys(entry).some(k => !["mark", "x", "y", "y2", "colorBy", "label"].includes(k))) {
      throw new ChartSpecError("Composite layer " + index + " has unknown fields");
    }
    if (!["point", "line", "band", "area"].includes(String(entry.mark))) {
      throw new ChartSpecError("Unsupported composite mark");
    }
    const mark = entry.mark as "point" | "line" | "band" | "area";
    if (typeof entry.x !== "string" || !Object.hasOwn(numbers, entry.x)
        || typeof entry.y !== "string" || !Object.hasOwn(numbers, entry.y)) {
      throw new ChartSpecError("Composite layer must reference numeric x/y columns");
    }
    if ((mark === "band") !== (entry.y2 != null)) {
      throw new ChartSpecError("Composite band requires y2; other marks forbid y2");
    }
    const x = entry.x, y = entry.y;
    const layer: (typeof layers)[number] = { mark, x, y };
    if (entry.y2 != null) {
      if (typeof entry.y2 !== "string" || !Object.hasOwn(numbers, entry.y2)) {
        throw new ChartSpecError("Composite y2 must reference a numeric column");
      }
      if (numbers[y].some((v, i) => v > numbers[entry.y2 as string][i])) {
        throw new ChartSpecError("Composite band lower bounds must not exceed upper bounds");
      }
      layer.y2 = entry.y2;
      yBounds.push(...numbers[entry.y2]);
    }
    if (entry.colorBy != null) {
      if (typeof entry.colorBy !== "string" || !Object.hasOwn(categories, entry.colorBy)) {
        throw new ChartSpecError("Composite colorBy must name a categorical column");
      }
      for (const value of categories[entry.colorBy]) colorSlots.add(JSON.stringify([entry.colorBy, value]));
      layer.colorBy = entry.colorBy;
    } else {
      colorSlots.add(JSON.stringify([index]));
    }
    if (colorSlots.size > 8) {
      throw new ChartSpecError("Composite figures support at most eight color legend entries across all layers");
    }
    if (entry.label != null) layer.label = cleanText(entry.label, "Composite layer label");
    if (mark === "area") yBounds.push(0);
    xBounds.push(...numbers[x]);
    yBounds.push(...numbers[y]);
    layers.push(layer);
  }
  for (const [axis, values] of [["x", xBounds], ["y", yBounds]] as const) {
    const scale = raw[axis + "Scale"] ?? "linear";
    if (scale !== "linear" && scale !== "log10") {
      throw new ChartSpecError("Composite " + axis + "Scale must be linear or log10");
    }
    if (scale === "log10" && values.some(v => v <= 0)) {
      throw new ChartSpecError("Composite " + axis + "Scale requires positive observations");
    }
    if (!Number.isFinite(Math.max(...values) - Math.min(...values))) {
      throw new ChartSpecError("Composite " + axis + " domain is not finite");
    }
    if (axis === "x") outputAxis("x", scale);
    else outputAxis("y", scale);
  }
  function outputAxis(axis: "x" | "y", scale: "linear" | "log10") {
    if (axis === "x") {
      result.xScale = scale;
      result.xAxis = cleanText(raw.xAxis, "Composite xAxis", { default: "X" });
      if (raw.xUnit != null) result.xUnit = cleanText(raw.xUnit, "Composite xUnit");
    } else {
      result.yScale = scale;
      result.yAxis = cleanText(raw.yAxis, "Composite yAxis", { default: "Y" });
      if (raw.yUnit != null) result.yUnit = cleanText(raw.yUnit, "Composite yUnit");
    }
  }
  result.version = 2;
  result.composite = { numbers, layers, categories };
  if (typeof facetBy === "string") result.composite.facetBy = facetBy;
  return result;
}


function canonicalizeHeatmap(raw: Record<string, unknown>, result: ChartDataRow): ChartDataRow {
  if (!Array.isArray(raw.rowLabels) || !raw.rowLabels.length) {
    throw new ChartSpecError("Heatmap rowLabels must be a nonempty list");
  }
  if (!Array.isArray(raw.columnLabels) || !raw.columnLabels.length) {
    throw new ChartSpecError("Heatmap columnLabels must be a nonempty list");
  }
  if (raw.rowLabels.length * raw.columnLabels.length > MAX_SERIES_POINTS) {
    throw new ChartSpecError("Heatmaps support at most 300 cells");
  }
  if (!Array.isArray(raw.matrix) || raw.matrix.length !== raw.rowLabels.length) {
    throw new ChartSpecError("Heatmap matrix row count must match rowLabels");
  }
  let smallest = Infinity;
  let largest = -Infinity;
  const matrix = raw.matrix.map((row, i) => {
    if (!Array.isArray(row) || row.length !== (raw.columnLabels as unknown[]).length) {
      throw new ChartSpecError("Heatmap matrix row " + i + " length must match columnLabels");
    }
    const values = row.map((value, j) => finiteNumber(value, "Heatmap cell at row " + i + " column " + j));
    smallest = Math.min(smallest, ...values);
    largest = Math.max(largest, ...values);
    return values;
  });
  if (!Number.isFinite(largest - smallest)) throw new ChartSpecError("Heatmap value range must be finite");
  const output = result;
  output.rowLabels = raw.rowLabels.map((label, i) => cleanText(label, "Heatmap row label at index " + i));
  output.columnLabels = raw.columnLabels.map((label, i) => cleanText(label, "Heatmap column label at index " + i));
  output.matrix = matrix;
  output.xAxis = cleanText(raw.xAxis, "X-axis label", { default: "Columns" });
  output.yAxis = cleanText(raw.yAxis, "Y-axis label", { default: "Rows" });
  if (raw.valueUnit != null) output.valueUnit = cleanText(raw.valueUnit, "Heatmap valueUnit");
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
function canonicalizeChartSpecBase(data: unknown, chartType?: string | null): ChartDataRow {
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
    type: expectedType,
    title: cleanText(raw.title, "Chart title", {
      default: `${expectedType[0].toUpperCase()}${expectedType.slice(1)} Chart`,
      maxLength: MAX_TITLE_LENGTH,
    }),
  };

  if (expectedType === "composite") return canonicalizeComposite(raw, result);
  if (expectedType === "boxplot") return canonicalizeBoxplot(raw, result);
  if (expectedType === "heatmap") return canonicalizeHeatmap(raw, result);

  if (expectedType === "scatter" || (expectedType === "line" && usesScientificMarks(raw, expectedType))) {
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

export function canonicalizeChartSpec(data: unknown, chartType?: string | null): ChartDataRow {
  const canonical = canonicalizeChartSpecBase(data, chartType);
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const raw = data as Record<string, unknown>;
    if (raw.analysis != null) {
      canonical.analysis = canonicalizeChartAnalysis(
        raw.analysis, canonical.type ?? chartType ?? "",
        canonical.xScale ?? "linear", canonical.yScale ?? "linear", canonical,
      );
    }
  }
  return canonical;
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
