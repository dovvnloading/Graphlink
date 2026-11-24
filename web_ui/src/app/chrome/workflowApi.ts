import { authHeaders } from "../../lib/auth/token";
import type { PortSpec, PortValue, WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import type { StatusWord } from "../ui/Badge";
import { formatDuration } from "../ui/format";

/** Shapes returned by the detached workflow HTTP API (backend/workflow/api.py). */
export type Workspace = { id: number; name: string };
export type StepPolicy = { execution_lane: string; model_identity: [string, string] | null; cost_usd: number };
export type ReviewedStep = [string, string, StepPolicy];
export type ControlPreview = {
  paths?: string[]; predicate?: string; message?: string; mode?: string;
  item_count?: number; estimated_cost_usd?: number; child_steps?: ReviewedStep[];
};
export type WorkflowPreview = {
  fingerprint: string; estimated_cost_usd: number; requires_approval: boolean;
  steps: ReviewedStep[]; control_flow: Record<string, ControlPreview>;
};
export type RunStep = {
  step_id: string; status: string; error: string | null; cost_usd?: number;
  started_at?: string | null; finished_at?: string | null; side_effect_class?: string;
  outputs?: Record<string, PortValue>; skipped_outputs?: string[];
  approval?: { status: string; message: string };
  map_items?: Record<string, { status: string; error: string | null; cost_usd: number }>;
};
export type UnattendedPause = { step_id: string; nonce: string; side_effect_class: string; preview_fingerprint: string };
export type RunEvent = { sequence: number; kind: string; step_id: string | null; message: string; timestamp: string };
export type DetachedRun = {
  run_id: string; preset_id?: string | null; status: string; error: string | null; cost_usd: number;
  created_at: string; started_at?: string | null; finished_at?: string | null;
  spec: WorkflowSpec; inputs: Record<string, PortValue>; outputs: Record<string, PortValue>;
  steps: Record<string, RunStep>; awaiting_approval?: UnattendedPause | null;
  preview: WorkflowPreview; events?: RunEvent[]; unattended_effects?: string[] | null;
};
export type PresetTrigger = null | { kind: "interval"; seconds: number } | { kind: "daily"; time: string }
  | { kind: "folder"; path: string };
export type Preset = {
  preset_id: string; name: string; trigger: PresetTrigger; cost_usd: number; allowed_effects: string[];
  request: Record<string, unknown>; workflow_id?: string | null; revision?: string | null; created_at?: string;
};
/** A typed run request: a frozen spec plus one value per workflow input. */
export type DetachedRequest = { spec: WorkflowSpec; parameters: Record<string, PortValue> };

export async function workflowRequest<T>(path: string, init: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const response = await fetch(path, {
    method: init.method ?? "GET", signal: init.signal,
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof body?.detail === "string" ? body.detail : `The workflow service answered ${response.status}. Try again.`);
  }
  return body as T;
}
export function workspacePath(workspace: number) { return "/api/workflows/" + workspace; }
export function errorText(error: unknown) { return error instanceof Error ? error.message : String(error); }

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Not recorded";
  if (value === 0) return "$0.00";
  if (value < 0.01) return value < 0.000001 ? "Under $0.000001" : "$" + value.toFixed(6).replace(/0+$/, "");
  return "$" + value.toFixed(2);
}
export function when(value: string | null | undefined): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not recorded";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
export function elapsed(start: string | null | undefined, end: string | null | undefined): string | null {
  if (!start || !end) return null;
  const ms = Date.parse(end) - Date.parse(start);
  return Number.isFinite(ms) && ms >= 0 ? formatDuration(ms / 1000) : null;
}
export function plural(count: number, one: string, many = one + "s") { return `${count} ${count === 1 ? one : many}`; }

const effectCopy: Record<string, { label: string; detail: string }> = {
  pure: { label: "Pure", detail: "Computes from its inputs only" },
  "local-read": { label: "Local read", detail: "Reads local knowledge or files" },
  cloud: { label: "Cloud", detail: "Sends data to a hosted service and may cost money" },
  executes: { label: "Runs code", detail: "Runs a model, code or an agent on this computer" },
  writes: { label: "Writes", detail: "Changes files or services outside the graph" },
};
export function effectLabel(effect: string) { return effectCopy[effect]?.label ?? effect; }
const effectSentences: Record<string, string> = {
  executes: "runs a model, code or an agent on this computer",
  writes: "changes files or services outside the graph",
  cloud: "sends data to a hosted service and may cost money",
  "local-read": "reads local knowledge or files",
  pure: "only computes from its inputs",
};
export function effectSentence(effect: string) { return effectSentences[effect] ?? "has side effects"; }
export function effectDetail(effect: string) { return effectCopy[effect]?.detail ?? ""; }

const adapterCopy: Record<string, string> = {
  "transform.template": "Fill a template", "transform.json_path": "Extract with a JSON path",
  "transform.text_split": "Split text", "transform.text_join": "Join text",
  "transform.table_filter": "Filter table rows", "transform.table_sort": "Sort table",
  "transform.table_select": "Select columns", "transform.table_rename": "Rename columns",
  "graph.local_model": "Local model", "control.condition": "Condition", "control.merge": "Merge",
  "control.approval": "Approval", "control.assert": "Assert", "control.map": "Map over items",
};
/** A readable step kind, falling back to the adapter's own name. */
export function adapterLabel(adapterId: string) {
  if (adapterCopy[adapterId]) return adapterCopy[adapterId];
  const tail = adapterId.split(".").pop() ?? adapterId;
  return tail.replace(/[_-]+/g, " ").replace(/^./, c => c.toUpperCase());
}

const runCopy: Record<string, string> = {
  pending: "Queued", running: "Running", completed: "Completed", failed: "Failed", cancelled: "Cancelled",
  interrupted: "Interrupted", blocked: "Blocked", skipped: "Skipped", partial: "Completed with issues",
};
export function statusText(status: string) { return runCopy[status] ?? status.replace(/[-_]+/g, " "); }
/** StatusPill words carry shape and text, so status never depends on colour. */
export function statusWord(status: string, waiting = false): StatusWord | null {
  if (waiting) return "Waiting for approval";
  // Interrupted and blocked have no pill of their own; they render as a text badge.
  return ({ pending: "Queued", running: "Running", completed: "Done", failed: "Failed", cancelled: "Cancelled",
    skipped: "Skipped" } as Record<string, StatusWord>)[status] ?? null;
}
export function runWaiting(run: DetachedRun) {
  return run.status === "running" && (!!run.awaiting_approval
    || Object.values(run.steps).some(step => step.approval?.status === "pending"));
}

export function triggerSummary(trigger: PresetTrigger): string {
  if (!trigger) return "Manual, command line or MCP";
  if (trigger.kind === "interval") return "Every " + formatDuration(trigger.seconds);
  if (trigger.kind === "daily") return "Daily at " + trigger.time;
  return "When files change in " + trigger.path;
}

/** Plain text for an output or input value, for display only. */
export function valueText(value: PortValue): string {
  switch (value.kind) {
    case "text": return value.text;
    case "json": return JSON.stringify(value.value, null, 2);
    case "table": return [value.columns.join(", "), ...value.rows.map(row => row.map(cell => cell ?? "").join(", "))].join("\n");
    default: return value.asset.filename + " (" + value.asset.media_type + ")";
  }
}
export function valueSummary(value: PortValue): string {
  if (value.kind === "table") return plural(value.rows.length, "row") + " · " + plural(value.columns.length, "column");
  if (value.kind === "file" || value.kind === "image") return value.asset.filename;
  const text = valueText(value).replace(/\s+/g, " ").trim();
  return text.length > 140 ? text.slice(0, 139) + "…" : text || "Empty";
}

/** Form drafts are strings; convert them to the typed values a run needs. */
export type InputDrafts = Record<string, string>;
export function parseCsv(text: string): { columns: string[]; rows: (string | number | null)[][] } {
  const lines: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = ""; if (row.some(v => v.trim())) lines.push(row); row = [];
    } else cell += c;
  }
  row.push(cell); if (row.some(v => v.trim())) lines.push(row);
  if (!lines.length) throw new Error("Add a header row and at least one data row.");
  const columns = lines[0].map(c => c.trim());
  if (columns.some(c => !c) || new Set(columns).size !== columns.length) {
    throw new Error("Column names must be unique and not empty.");
  }
  const rows = lines.slice(1).map(cells => columns.map((_, index) => {
    const raw = (cells[index] ?? "").trim();
    if (!raw) return null;
    const number = Number(raw);
    return Number.isFinite(number) && /^-?\d/.test(raw) ? number : raw;
  }));
  return { columns, rows };
}
export function draftFor(port: PortSpec, value: PortValue | undefined): string {
  if (!value) return "";
  if (value.kind === "table") {
    const quote = (v: unknown) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    return [value.columns.map(quote).join(","), ...value.rows.map(r => r.map(quote).join(","))].join("\n");
  }
  return port.kind === value.kind ? valueText(value) : "";
}
function parsedJson(port: PortSpec, draft: string) {
  try { return JSON.parse(draft); } catch { throw new Error(`“${port.name}” must be valid JSON.`); }
}
/** Typed values for a detached run request. Empty optional inputs are omitted. */
export function typedInputs(ports: readonly PortSpec[], drafts: InputDrafts): Record<string, PortValue> {
  const values: Record<string, PortValue> = {};
  for (const port of ports) {
    const draft = drafts[port.name] ?? "";
    if (!draft.trim()) {
      if (port.required) throw new Error(`Enter a value for “${port.name}”.`);
      continue;
    }
    if (port.kind === "text") values[port.name] = { kind: "text", text: draft };
    else if (port.kind === "json") values[port.name] = { kind: "json", value: parsedJson(port, draft) };
    else if (port.kind === "table") values[port.name] = { kind: "table", ...parseCsv(draft) };
    else throw new Error(`“${port.name}” needs a ${port.kind}. Edit the request as JSON to supply it.`);
  }
  return values;
}
/** Plain values for a library preset request (backend typed_inputs). */
export function plainInputs(ports: readonly PortSpec[], drafts: InputDrafts): Record<string, unknown> {
  const typed = typedInputs(ports, drafts);
  return Object.fromEntries(Object.entries(typed).map(([name, value]) => [name,
    value.kind === "text" ? value.text : value.kind === "json" ? value.value
      : value.kind === "table" ? { columns: value.columns, rows: value.rows } : value]));
}
