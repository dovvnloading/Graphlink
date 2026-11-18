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

export interface ScienceResult {
  operationId: string;
  inputs: Record<string, unknown>;
  summary: string;
  values: Record<string, unknown>;
  table?: ScienceTable;
  plot?: SciencePlot;
  provenance: Record<string, unknown> & { resultHash: string; receiptHash?: string; sources?: Sources };
}

export interface ScienceRunnerConfiguration {
  version: 1;
  operationId: string;
  inputs: Record<string, unknown>;
  sources: Sources;
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
  runner?: ScienceRunnerConfiguration | null;
  table?: ScientificImportedTable | null;
}

export interface ScienceParent { nodeId: string; title: string; raw: string }
export interface SourceOption {
  label: string; value: unknown; reference: SourceReference; tableColumn?: ScientificTableSelection;
}


function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
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

function isResult(value: unknown): value is ScienceResult {
  if (!record(value) || typeof value.operationId !== "string" || typeof value.summary !== "string" ||
      !record(value.inputs) || !record(value.values) || !record(value.provenance) ||
      typeof value.provenance.resultHash !== "string" ||
      (value.provenance.receiptHash !== undefined && typeof value.provenance.receiptHash !== "string")) return false;
  const sources = value.provenance.sources;
  if (sources !== undefined && (!record(sources) || !Object.values(sources).every((source) =>
    record(source) && typeof source.nodeId === "string" && typeof source.resultHash === "string" &&
    (source.sourceKey == null || typeof source.sourceKey === "string") &&
    (source.receiptHash === undefined || typeof source.receiptHash === "string") &&
    Array.isArray(source.selector) && source.selector.every((key) => typeof key === "string" || Number.isInteger(key))))) return false;
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
        (value.runner == null || (record(value.runner) && value.runner.version === 1 &&
          typeof value.runner.operationId === "string" && record(value.runner.inputs) &&
          record(value.runner.sources))) &&
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
        options.push({ label: `${title} · ${name}`, value,
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
