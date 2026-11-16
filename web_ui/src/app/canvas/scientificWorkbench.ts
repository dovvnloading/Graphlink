export interface ScienceField {
  key: string;
  label: string;
  kind: "number" | "integer" | "vector" | "matrix" | "text" | "table";
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
  selector: (string | number)[];
}

export type Sources = Record<string, SourceReference>;
export interface ScienceTable { columns: string[]; rows: unknown[][] }
export interface SciencePlot { x: number[]; y: number[]; xLabel: string; yLabel: string; title: string }

export interface ScienceResult {
  operationId: string;
  inputs: Record<string, unknown>;
  summary: string;
  values: Record<string, unknown>;
  table?: ScienceTable;
  plot?: SciencePlot;
  provenance: Record<string, unknown> & { resultHash: string; sources?: Sources };
}

export interface ScienceState {
  scienceVersion: 1;
  packId: string;
  nodeKey?: string;
  error?: string;
  operations: ScienceOperation[];
  latest: ScienceResult | null;
}

export interface ScienceParent { nodeId: string; title: string; raw: string }
export interface SourceOption { label: string; value: unknown; reference: SourceReference }


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
    && ["number", "integer", "vector", "matrix", "text", "table"].includes(String(value.kind));
}

function isOperation(value: unknown): value is ScienceOperation {
  return record(value) && typeof value.id === "string" && typeof value.title === "string"
    && typeof value.description === "string" && record(value.example) && Array.isArray(value.fields)
    && value.fields.every(isField);
}

function isResult(value: unknown): value is ScienceResult {
  if (!record(value) || typeof value.operationId !== "string" || typeof value.summary !== "string" ||
      !record(value.inputs) || !record(value.values) || !record(value.provenance) ||
      typeof value.provenance.resultHash !== "string") return false;
  const sources = value.provenance.sources;
  if (sources !== undefined && (!record(sources) || !Object.values(sources).every((source) =>
    record(source) && typeof source.nodeId === "string" && typeof source.resultHash === "string" &&
    Array.isArray(source.selector) && source.selector.every((key) => typeof key === "string" || Number.isInteger(key))))) return false;
  const { table, plot } = value;
  if (table !== undefined && !(record(table) && strings(table.columns) && Array.isArray(table.rows)
      && table.rows.every((row) => Array.isArray(row) && row.length === (table.columns as string[]).length))) return false;
  return plot === undefined || (record(plot) && typeof plot.title === "string"
    && typeof plot.xLabel === "string" && typeof plot.yLabel === "string" && vector(plot.x) && vector(plot.y)
    && plot.x.length === plot.y.length);
}

/** Plugin state crosses the generic SDK wire as string values. Decode once per revision. */
export function readScienceState(raw: string): ScienceState | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (record(value) && value.scienceVersion === 1 && typeof value.packId === "string" &&
        Array.isArray(value.operations) && (value.operations.length || (typeof value.error === "string" && value.error)) &&
        (value.nodeKey === undefined || typeof value.nodeKey === "string") && value.operations.every(isOperation) &&
        (value.latest === null || isResult(value.latest))) return value as unknown as ScienceState;
  } catch { /* A missing plugin or unsupported saved version still leaves the card visible. */ }
  return null;
}

export function fieldText(field: ScienceField, value: unknown): string {
  if (value === undefined) return "";
  if (field.kind === "text") return String(value);
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
    case "table": return Array.isArray(value) && value.length > 0 && value.every(Array.isArray);
    case "text": return typeof value === "string";
  }
}

const expected = { number: "a finite number", integer: "a whole number", vector: "an array of numbers, such as [1,2,3]",
  matrix: "a rectangular numeric array, such as [[1,2],[3,4]]", table: "an array of rows", text: "text" };

export function parseInputs(op: ScienceOperation, draft: Record<string, string>): Record<string, unknown> {
  return Object.fromEntries(op.fields.map((field) => {
    const raw = draft[field.key] ?? "";
    if (!raw.trim()) throw new Error(`Enter a value for ${field.label}.`);
    let value: unknown;
    if (field.kind === "text") value = raw;
    else if (field.kind === "number" || field.kind === "integer") {
      if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw.trim())) value = Number(raw);
    } else {
      try { value = JSON.parse(raw); } catch { value = undefined; }
    }
    if (!accepts(field.kind, value)) throw new Error(`${field.label} must be ${expected[field.kind]}.`);
    return [field.key, value];
  }));
}

export function sourceOptions(parents: ScienceParent[]): SourceOption[] {
  const options: SourceOption[] = [];
  for (const parent of parents) {
    const state = readScienceState(parent.raw);
    const result = state?.latest;
    if (!result || state.error) continue;
    const add = (name: string, value: unknown, selector: SourceReference["selector"]) => {
      options.push({ label: `${parent.title} · ${name}`, value,
        reference: { nodeId: parent.nodeId, ...(state.nodeKey && { sourceKey: state.nodeKey }), resultHash: result.provenance.resultHash, selector } });
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
  return options;
}

export function compatibleOptions(field: ScienceField, options: SourceOption[]): SourceOption[] {
  return options.filter((option) => accepts(field.kind, option.value));
}
