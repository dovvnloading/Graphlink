export interface ScienceField {
  key: string;
  label: string;
  kind: "number" | "integer" | "vector" | "matrix" | "text";
  unit: string;
  description: string;
}

export interface ScienceOperation {
  id: string;
  title: string;
  description: string;
  fields: ScienceField[];
  example: Record<string, unknown>;
}

export interface SourceReference {
  nodeId: string;
  resultHash: string;
  sourceKey?: string;
  receiptHash?: string;
  selector: (string | number)[];
}

export interface ScienceLimits {
  points: number; columns: number; cells: number; text: number;
  tableRows?: number; tableColumns?: number; tableCells?: number; tableSourceBytes?: number;
}
/** Fallback for a v1 server whose catalog predates the limits field. */
const V1_LIMITS: ScienceLimits = { points: 1024, columns: 32, cells: 4096, text: 65536 };

export type Sources = Record<string, SourceReference>;
export interface EditorDraft { values: Record<string, string>; sources: Sources; revision: string; dirty: boolean }
export interface ScienceTable { columns: string[]; rows: unknown[][] }
export type ScientificColumnType = "numeric" | "text" | "categorical" | "datetime" | "boolean";
export interface ScientificTableColumn {
  name: string; label: string; type: ScientificColumnType; unit: string; missingCount: number;
}
export interface ScientificImportedTable {
  version: 1; asset_ref: string; mime_type: string;
  columns: ScientificTableColumn[]; rowCount: number; columnCount: number;
  preview: unknown[][]; previewColumns: number; sourceName: string;
  provenance: { fileHash: string; tableHash: string; receiptHash: string; parseOptions: Record<string, unknown> };
}
export interface ScientificTableSelection {
  nodeId: string; column: number; policy: "reject" | "drop"; start: number; end: number;
}
export interface SciencePlot { x: number[]; y: number[]; xLabel: string; yLabel: string; title: string }

export interface ScientificMeasurement {
  value: number;
  standardUncertainty: number;
  coverageFactor: number;
  coverageProbability?: number;
  unit: string;
  interval?: [number, number];
  components?: Record<string, number>;
  method?: "gum" | "monte_carlo";
  budget?: Array<{ input: string; kind: "input" | "covariance"; variance: number;
    unit?: string; standardUncertainty?: number; sensitivity?: number; signedContribution?: number }>;
  monteCarloMean?: number;
  monteCarloTrials?: number;
  budgetNote?: string;
}
export interface ScientificUncertainty {
  method: "gum" | "monte_carlo";
  inputs: Record<string, ScientificMeasurement>;
  outputs: Record<string, ScientificMeasurement>;
  unsupportedOutputs?: Record<string, string>;
}

/** Format values without discarding the standard uncertainty or its units. */
export function formatMeasurement(measurement: ScientificMeasurement): string {
  return `${measurement.value} ± ${measurement.standardUncertainty}${measurement.unit ? " " + measurement.unit : ""}`;
}

/** Also understand pre-existing pack outputs without changing their receipts. */
export function resultMeasurement(result: ScienceResult, name: string): ScientificMeasurement | undefined {
  const value = result.values[name];
  if (!finite(value)) return undefined;
  const attached = result.uncertainty?.outputs[name];
  if (attached) return attached;
  let sigma: unknown;
  let factor: unknown = 1.959963984540054;
  if (name === "estimate" && finite(result.values.combined_standard_uncertainty)) {
    sigma = result.values.combined_standard_uncertainty;
    factor = result.values.coverage_factor ?? 1.959963984540054;
  } else {
    const index = name.split("_").slice(-1)[0];
    const candidates = [name + "_standard_uncertainty", name + "_standard_error",
      name.replace("_per_", "_standard_error_per_"),
      name.replace("_per_", "_standard_uncertainty_per_"),
      name.includes("_") ? name.replace("_", "_standard_uncertainty_") : "",
      name.includes("_") ? name.replace("_", "_standard_error_") : "",
      name.startsWith("coefficient_") || name.startsWith("parameter_") ? "standard_error_" + index : "",
      name === "b_value_per_mw" ? "b_approx_standard_error_per_mw" : "",
      name === "decay_constant_per_day" ? "rate_standard_error_per_day" : ""];
    sigma = candidates.filter(Boolean).map((candidate) => result.values[candidate]).find(finite);
  }
  if (!finite(sigma) || sigma < 0 || !finite(factor) || factor <= 0) return undefined;
  const suffix = name.match(/\(([^()]+)\)\s*$/);
  const short = name.match(/_(ma|mm|cm|km|kg|hz|pa|rad|deg|ohm|mol|ms|m|s|g|j|k|v|a|l)$/i);
  const unit = suffix?.[1] ?? (short ? ({ ma: "Ma", hz: "Hz", pa: "Pa", j: "J", k: "K", v: "V", a: "A", l: "L" } as Record<string, string>)[short[1].toLowerCase()] ?? short[1] : "");
  return { value, standardUncertainty: sigma, coverageFactor: factor,
    coverageProbability: name === "estimate" && finite(result.values.coverage_probability)
      ? result.values.coverage_probability : 0.95, unit };
}

export interface ScienceResult {
  operationId: string;
  inputs: Record<string, unknown>;
  summary: string;
  values: Record<string, unknown>;
  table?: ScienceTable;
  plot?: SciencePlot;
  uncertainty?: ScientificUncertainty;
  provenance: Record<string, unknown> & { resultHash: string; receiptHash?: string; sources?: Sources };
}

export interface ScienceState {
  scienceVersion: 1;
  packId: string;
  nodeKey?: string;
  runToken?: string;
  limits?: ScienceLimits;
  error?: string;
  operations: ScienceOperation[];
  latest: ScienceResult | null;
  table?: ScientificImportedTable | null;
}

export interface ScienceParent { nodeId: string; title: string; raw: string }
export interface SourceOption {
  label: string; value: unknown; reference: SourceReference; tableColumn?: ScientificTableSelection;
  uncertainty?: ScientificMeasurement;
}


function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** An interval by itself is not an input PDF or a standard uncertainty. */
export function intervalOnly(result: ScienceResult, key: string): [number, number] | undefined {
  if (resultMeasurement(result, key) || !["estimate", "r", "rho"].includes(key) && !key.startsWith("odds_ratio_"))
    return undefined;
  const index = key.startsWith("odds_ratio_") ? key.slice("odds_ratio_".length) : "";
  const pairs = [
    ["ci95_lower", "ci95_upper"], ["percentile_lower", "percentile_upper"],
    ["bca_lower", "bca_upper"], ["confidence_lower", "confidence_upper"],
    ...(index ? [[`odds_ratio_ci95_lower_${index}`, `odds_ratio_ci95_upper_${index}`]] : []),
  ];
  for (const [lo, hi] of pairs) {
    const lower = result.values[lo], upper = result.values[hi];
    if (finite(lower) && finite(upper)) return [lower, upper];
  }
  return undefined;
}
const vector = (value: unknown): value is number[] => Array.isArray(value) && value.length > 0 && value.every(finite);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((v) => typeof v === "string");
const matrix = (value: unknown): value is number[][] => Array.isArray(value) && value.length > 0 &&
  value.every(vector) && value.every((row) => row.length === value[0].length);

function isField(value: unknown): value is ScienceField {
  return record(value) && ["key", "label", "unit", "description"].every((key) => typeof value[key] === "string")
    && ["number", "integer", "vector", "matrix", "text"].includes(String(value.kind));
}

function isOperation(value: unknown): value is ScienceOperation {
  return record(value) && typeof value.id === "string" && typeof value.title === "string"
    && typeof value.description === "string" && record(value.example) && Array.isArray(value.fields)
    && value.fields.every(isField);
}

function isMeasurement(value: unknown): value is ScientificMeasurement {
  return record(value) && finite(value.value) && finite(value.standardUncertainty)
    && value.standardUncertainty >= 0 && finite(value.coverageFactor) && value.coverageFactor > 0
    && typeof value.unit === "string"
    && (value.coverageProbability === undefined || (finite(value.coverageProbability) && value.coverageProbability > 0 && value.coverageProbability < 1))
    && (value.interval === undefined || (Array.isArray(value.interval) && value.interval.length === 2 && value.interval.every(finite)))
    && (value.components === undefined || (record(value.components) && Object.values(value.components).every(finite)));
}

function isResult(value: unknown): value is ScienceResult {
  if (!record(value) || typeof value.operationId !== "string" || typeof value.summary !== "string" ||
      !record(value.inputs) || !record(value.values) || !record(value.provenance) ||
      typeof value.provenance.resultHash !== "string" ||
      (value.provenance.receiptHash !== undefined && typeof value.provenance.receiptHash !== "string")) return false;
  const resultValues = value.values;
  const sources = value.provenance.sources;
  if (sources !== undefined && (!record(sources) || !Object.values(sources).every((source) =>
    record(source) && typeof source.nodeId === "string" && typeof source.resultHash === "string" &&
    (source.sourceKey == null || typeof source.sourceKey === "string") &&
    (source.receiptHash === undefined || typeof source.receiptHash === "string") &&
    Array.isArray(source.selector) && source.selector.every((key) => typeof key === "string" || Number.isInteger(key))))) return false;
  if (value.uncertainty !== undefined) {
    const data = value.uncertainty;
    if (!record(data) || !["gum", "monte_carlo"].includes(String(data.method))
        || !record(data.inputs) || !record(data.outputs)
        || !Object.values(data.inputs).every(isMeasurement) || !Object.values(data.outputs).every(isMeasurement)
        || Object.entries(data.outputs).some(([key, item]) => {
          const outputValue = resultValues[key];
          return !isMeasurement(item) || !finite(outputValue) || item.value !== outputValue;
        })) return false;
  }
  const { table, plot } = value;
  if (table !== undefined && !(record(table) && strings(table.columns) && Array.isArray(table.rows)
      && table.rows.every((row) => Array.isArray(row) && row.length === (table.columns as string[]).length))) return false;
  return plot === undefined || (record(plot) && typeof plot.title === "string"
    && typeof plot.xLabel === "string" && typeof plot.yLabel === "string" && vector(plot.x) && vector(plot.y)
    && plot.x.length === plot.y.length);
}

function isImportedTable(value: unknown): value is ScientificImportedTable {
  if (!record(value) || value.version !== 1 || typeof value.asset_ref !== "string"
      || typeof value.rowCount !== "number" || !Number.isSafeInteger(value.rowCount)
      || value.rowCount < 1 || !Array.isArray(value.columns) || value.columnCount !== value.columns.length
      || !Array.isArray(value.preview) || !record(value.provenance)
      || typeof value.provenance.fileHash !== "string" || typeof value.provenance.receiptHash !== "string"
      || !record(value.provenance.parseOptions)) return false;
  return value.columns.every((col) => record(col) && typeof col.name === "string"
    && typeof col.label === "string" && typeof col.unit === "string"
    && ["numeric", "text", "categorical", "datetime", "boolean"].includes(String(col.type))
    && Number.isSafeInteger(col.missingCount) && Number(col.missingCount) >= 0);
}

/** Plugin state crosses the generic SDK wire as string values. Decode once per revision. */
export function readScienceState(raw: string): ScienceState | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (record(value) && value.scienceVersion === 1 && typeof value.packId === "string" &&
        Array.isArray(value.operations) && (value.operations.length || (typeof value.error === "string" && value.error)) &&
        (value.nodeKey == null || typeof value.nodeKey === "string") &&
        (value.error === undefined || typeof value.error === "string") &&
        (value.runToken === undefined || typeof value.runToken === "string") &&
        (value.limits === undefined || (record(value.limits) && Object.keys(V1_LIMITS).every((k) =>
          Number.isSafeInteger((value.limits as Record<string, unknown>)[k]) && Number((value.limits as Record<string, unknown>)[k]) > 0))) &&
        value.operations.every(isOperation) &&
        (value.table == null || isImportedTable(value.table)) &&
        (value.latest === null || isResult(value.latest))) return value as unknown as ScienceState;
  } catch { /* A missing plugin or unsupported saved version still leaves the card visible. */ }
  return null;
}

function editableNumber(value: number): string {
  // A shortest round-trip spelling like "1000000000000000100" can denote a
  // different integer from the binary64 value (1000000000000000128).
  return Number.isInteger(value) && !Number.isSafeInteger(value) ? BigInt(value).toString() : String(value);
}

export function fieldText(field: ScienceField, value: unknown): string {
  if (value === undefined) return "";
  if (field.kind === "text") return String(value);
  if (typeof value === "number") return editableNumber(value);
  if (Array.isArray(value)) return "[" + value.map((part) => fieldText(field, part)).join(",") + "]";
  return JSON.stringify(value);
}

export function draftInputs(op: ScienceOperation, inputs: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(op.fields.map((field) => [field.key, fieldText(field, inputs[field.key])]));
}

function accepts(kind: ScienceField["kind"], value: unknown): boolean {
  switch (kind) {
    case "number": return finite(value);
    case "integer": return Number.isSafeInteger(value);
    case "vector": return vector(value);
    case "matrix": return matrix(value);
    case "text": return typeof value === "string";
  }
}

const expected = { number: "a finite number", integer: "a whole number", vector: "an array of numbers, such as [1,2,3]",
  matrix: "a rectangular numeric array, such as [[1,2],[3,4]]", text: "text" };

export class FieldError extends Error {
  constructor(readonly key: string, message: string) { super(message); }
}

const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const arrayTokens = /[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/g;

/** Match the CSV importer: binary64 fractions may round, integer counts must be exact,
 * and a nonzero decimal must not silently become zero. Compare before JSON.parse
 * discards the source spelling. Never construct an exponent-sized string or BigInt. */
function parseMeasurement(token: string, field: ScienceField): number {
  const number = Number(token);
  if (!Number.isFinite(number)) throw new FieldError(field.key, `${field.label} must contain finite numbers.`);
  const exponentAt = token.search(/[eE]/);
  const mantissa = exponentAt < 0 ? token : token.slice(0, exponentAt);
  const point = mantissa.indexOf(".");
  const digits = mantissa.replace(/^[+-]/, "").replace(".", "").replace(/^0+/, "");
  if (!digits) return number;
  if (number === 0) throw new FieldError(field.key, `${field.label} contains a nonzero number too small to represent.`);
  if (!Number.isInteger(number)) return number;

  const exponent = exponentAt < 0 ? 0 : Number(token.slice(exponentAt + 1));
  const shift = exponent - (point < 0 ? 0 : mantissa.length - point - 1);
  const significant = digits.replace(/0+$/, "");
  const zeros = shift + digits.length - significant.length;
  if (zeros < 0) return number; // The written decimal contains a fractional part.
  const binary = BigInt(Math.abs(number)).toString();
  if (binary.length !== significant.length + zeros || !binary.startsWith(significant) ||
      /[^0]/.test(binary.slice(significant.length))) {
    throw new FieldError(field.key, `${field.label} contains an integer count that cannot be represented exactly.`);
  }
  return number;
}

export function parseInputs(op: ScienceOperation, draft: Record<string, string>, limits = V1_LIMITS): Record<string, unknown> {
  return Object.fromEntries(op.fields.map((field) => {
    const raw = draft[field.key] ?? "";
    const invalid = (message: string): never => { throw new FieldError(field.key, message); };
    if (!raw || (field.kind !== "text" && !raw.trim())) invalid(`Enter a value for ${field.label}.`);
    let value: unknown;
    if (field.kind === "text") {
      if (raw.length > limits.text && Array.from(raw).length > limits.text)
        invalid(`${field.label} exceeds ${limits.text} characters.`);
      value = raw;
    } else if (field.kind === "integer") {
      // Decimal/exponent conversion through Number first silently rounds e.g. 9007199254740990.5.
      if (!/^[+-]?\d+$/.test(raw.trim())) invalid(`${field.label} must be a whole number written in integer notation.`);
      value = Number(raw);
    } else if (field.kind === "number") {
      if (decimal.test(raw.trim())) value = parseMeasurement(raw.trim(), field);
    } else {
      for (const match of raw.matchAll(arrayTokens)) parseMeasurement(match[0], field);
      try { value = JSON.parse(raw); } catch { value = undefined; }
    }
    if (!accepts(field.kind, value)) invalid(`${field.label} must be ${expected[field.kind]}.`);
    if (Array.isArray(value)) {
      if (value.length > limits.points) invalid(`${field.label} exceeds ${limits.points} entries.`);
      if (field.kind === "matrix" && (value[0].length > limits.columns || value.length * value[0].length > limits.cells))
        invalid(`${field.label} supports up to ${limits.columns} columns and ${limits.cells} cells.`);
    }
    return [field.key, value];
  }));
}

export function sourceOptions(parents: ScienceParent[], maxPoints = V1_LIMITS.points): SourceOption[] {
  const options: SourceOption[] = [];
  const names = new Map<string, number>();
  for (const parent of parents) names.set(parent.title, (names.get(parent.title) ?? 0) + 1);
  for (const parent of parents) {
    const state = readScienceState(parent.raw);
    if (!state || state.error) continue;
    const result = state.latest;
    const title = names.get(parent.title) === 1 ? parent.title : `${parent.title} (${parent.nodeId})`;
    if (result) {
      const add = (name: string, value: unknown, selector: SourceReference["selector"]) => {
        const uncertainty = selector[0] === "values" ? resultMeasurement(result, name) : undefined;
        const interval = selector[0] === "values" ? intervalOnly(result, name) : undefined;
        const detail = uncertainty ? " · " + formatMeasurement(uncertainty) :
          interval ? ` · interval [${interval[0]}, ${interval[1]}] · cannot propagate without a standard uncertainty` : "";
        options.push({ label: `${title} · ${name}${detail}`, value,
          ...(uncertainty && { uncertainty }),
          reference: { nodeId: parent.nodeId, ...(state.nodeKey && { sourceKey: state.nodeKey }), resultHash: result.provenance.resultHash,
            ...(result.provenance.receiptHash && { receiptHash: result.provenance.receiptHash }), selector } });
      };
      for (const [name, value] of Object.entries(result.values)) add(name, value, ["values", name]);
      if (result.plot) {
        add(result.plot.xLabel, result.plot.x, ["plot", "x"]);
        add(result.plot.yLabel, result.plot.y, ["plot", "y"]);
      }
      if (result.table) {
        const { columns, rows } = result.table;
        columns.forEach((label, i) => {
          const column = rows.map((row) => row[i]);
          if (vector(column)) add(label, column, ["column", i]);
        });
        if (matrix(rows)) add("Table rows", rows, ["table"]);
      }
    }
    if (state.table) {
      const table = state.table;
      table.columns.forEach((col, column) => {
        if (col.type !== "numeric") return;
        // Existing calculation inputs still have their historical maximum.
        // For larger datasets the dropdown explicitly chooses the first
        // bounded row window instead of silently truncating the table.
        const start = 0, end = Math.min(table.rowCount, maxPoints);
        const modes: Array<"reject" | "drop"> = col.missingCount ? ["reject", "drop"] : ["reject"];
        modes.forEach((policy) => {
          const range = table.rowCount > end ? ` · first ${end} of ${table.rowCount} rows` : "";
          const missing = policy === "drop" ? " · Drop missing (explicit)" : col.missingCount ? " · Reject missing" : "";
          options.push({
            label: `${title} · ${col.label} [typed table${range}${missing}]`,
            value: undefined,
            tableColumn: { nodeId: parent.nodeId, column, policy, start, end },
            reference: {
              nodeId: parent.nodeId, ...(state.nodeKey && { sourceKey: state.nodeKey }),
              resultHash: table.asset_ref, receiptHash: table.provenance.receiptHash,
              selector: ["table_column", column, policy, start, end],
            },
          });
        });
      });
    }
  }
  // A table and plot from the same run often give different ordering to a column with the same name.
  const labels = new Set<string>(), duplicates = new Set<string>();
  for (const { label } of options) {
    if (labels.has(label)) duplicates.add(label);
    labels.add(label);
  }
  for (const option of options) {
    if (duplicates.has(option.label)) {
      const [kind, item] = option.reference.selector;
      const detail = kind === "column" ? `table column ${Number(item) + 1}` : kind === "plot" ? `plot ${item}` : kind;
      option.label += ` (${detail})`;
    }
  }
  return options;
}

export function compatibleOptions(field: ScienceField, options: SourceOption[], limits = V1_LIMITS): SourceOption[] {
  return options.filter(({ value, tableColumn }) => {
    if (tableColumn) return field.kind === "vector" && tableColumn.end - tableColumn.start <= limits.points;
    if (!accepts(field.kind, value)) return false;
    if (typeof value === "string") return value.length > 0 && (value.length <= limits.text || Array.from(value).length <= limits.text);
    if (!Array.isArray(value)) return true;
    return value.length <= limits.points && (field.kind !== "matrix" ||
      (value[0].length <= limits.columns && value.length * value[0].length <= limits.cells));
  });
}

export function matchesSource(option: SourceOption, source: SourceReference): boolean {
  const reference = option.reference;
  return (source.sourceKey ? reference.sourceKey === source.sourceKey : reference.nodeId === source.nodeId) &&
    reference.resultHash === source.resultHash && (!source.receiptHash || reference.receiptHash === source.receiptHash) &&
    reference.selector.length === source.selector.length && reference.selector.every((part, i) => part === source.selector[i]);
}


/** These are the four existing ChartSpec types, not new renderers. The menu
 * only offers shapes that fit the chart canonicalizer's exact limits. */
export type ScienceChartType = "scatter" | "line" | "boxplot" | "heatmap";
export interface ScienceChartLink {
  id: string;
  title: string;
  chartType: ScienceChartType;
  receiptHash: string;
  stale: boolean;
}

export function chartableScienceTypes(result: ScienceResult): ScienceChartType[] {
  const types: ScienceChartType[] = [];
  const { inputs, values, table, plot } = result;
  const pair = (a: unknown, b: unknown) => vector(a) && vector(b) && a.length === b.length;
  const fit = (result.provenance.packId === "statistics" && result.operationId === "linear_regression"
    && pair(inputs.x, inputs.y) && (inputs.x as number[]).length >= 3) ||
    (result.provenance.packId === "fitting" && result.operationId === "nonlinear_least_squares"
      && pair(inputs.x, inputs.y)) ||
    (result.provenance.packId === "fitting" && result.operationId === "weighted_linear_least_squares"
      && Array.isArray(inputs.predictors) && vector(inputs.response)
      && inputs.predictors.length === (inputs.response as number[]).length
      && inputs.predictors.every((row) => Array.isArray(row) && row.length === 1 && finite(row[0])));
  const count = result.operationId === "weighted_linear_least_squares"
    ? (inputs.response as number[] | undefined)?.length ?? 0
    : (inputs.x as number[] | undefined)?.length ?? 0;
  if (fit && count > 0 && count <= 75) types.push("line");
  if ((plot && pair(plot.x, plot.y) && plot.x.length <= 300)
      || (finite(values.estimate) && finite(values.combined_standard_uncertainty)
        && values.combined_standard_uncertainty >= 0)
      || (table && table.rows.length > 0 && table.rows.length <= 300 && table.columns.length <= 3
        && table.columns.filter((_, i) => vector(table.rows.map((row) => row[i]))).length >= 2)) {
    types.push("scatter");
  }
  if (!table || !table.rows.length || !table.columns.length) return types;

  const numeric = table.columns.map((_, i) => table.rows.map((row) => row[i]))
    .filter((cells) => vector(cells));
  const grouped = table.columns.length === 2 && table.rows.every((row) =>
    typeof row[0] === "string" && finite(row[1]));
  if (grouped) {
    const groups = new Map<string, number>();
    for (const row of table.rows) groups.set(row[0] as string, (groups.get(row[0] as string) ?? 0) + 1);
    if (groups.size <= 12 && groups.size > 0 && [...groups.values()].every((n) => n >= 2)
        && table.rows.length <= 300) types.push("boxplot");
  } else if (table.rows.length >= 2 && numeric.length > 0
             && numeric.length <= 12 && table.rows.length * numeric.length <= 300) {
    types.push("boxplot");
  }
  const withLabel = table.columns.length > 1 && table.rows.every((row) => typeof row[0] === "string")
    && new Set(table.rows.map((row) => row[0])).size === table.rows.length;
  const heat = table.rows.map((row) => withLabel ? row.slice(1) : row);
  if (heat.length * heat[0].length <= 300 && heat.every((row) =>
    row.length === heat[0].length && row.every(finite))) types.push("heatmap");
  return types;
}
