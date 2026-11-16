import { memo, useMemo, useState, type FormEvent } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import type { SceneStore } from "./sceneStore";
import { downloadBlob } from "./downloadTextFile";
import { writeTableCsv } from "./tableCsv";
import { createLinearScale } from "./charts/chartScales";
import {
  compatibleOptions, draftInputs, fieldText, parseInputs, readScienceState, sourceOptions,
  type ScienceField, type ScienceOperation, type ScienceParent, type SciencePlot, type ScienceResult,
  type ScienceState, type Sources, type SourceOption,
} from "./scientificWorkbench";
import "./ScientificNodeView.css";

export type ScientificFlowNode = Node<{
  title: string; raw: string; pluginId: string; store: SceneStore; parents: ScienceParent[];
}, "scientific">;

function display(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

function scienceScale(values: number[], from: number, to: number) {
  const low = values.reduce((a, b) => Math.min(a, b)), high = values.reduce((a, b) => Math.max(a, b));
  // Dividing by two avoids overflow when the finite bounds have opposite signs near 1e308.
  const divisor = Number.isFinite(high - low) ? 1 : 2;
  const position = createLinearScale([low / divisor, high / divisor], [from, to]);
  return { low, high, at: (n: number) => position(n / divisor) };
}

function Plot({ plot }: { plot: SciencePlot }) {
  const x = scienceScale(plot.x, 10, 310);
  const y = scienceScale(plot.y, 98, 8);
  const points = plot.x.map((v, i) => `${x.at(v)},${y.at(plot.y[i])}`).join(" ");
  const axis = (v: number) => Number(v.toPrecision(4)).toString();
  return <figure className="science-plot">
    <figcaption>{plot.title}</figcaption>
    <span>{plot.yLabel} · {axis(y.low)} to {axis(y.high)}</span>
    <svg viewBox="0 0 320 110" role="img" aria-label={`${plot.title}. ${plot.xLabel} versus ${plot.yLabel}, ${plot.x.length} points.`}>
      <path d="M10 8V98H310" fill="none" stroke="currentColor" opacity=".4" />
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.8" />
      {plot.x.length <= 48 && plot.x.map((v, i) =>
        <circle key={i} cx={x.at(v)} cy={y.at(plot.y[i])} r="2.4" fill="currentColor"><title>{v}, {plot.y[i]}</title></circle>)}
    </svg>
    <span>{plot.xLabel} · {axis(x.low)} to {axis(x.high)}</span>
  </figure>;
}

function saveText(data: string, name: string, mime: string) {
  downloadBlob(new Blob([data], { type: mime }), name);
}

// Spreadsheet applications may interpret leading formula characters inside quoted CSV strings.
function csvCell(value: unknown): string {
  if (typeof value === "string" && /^(?:[\t\r\n]|\s*[=+@-])/.test(value)) return "'" + value;
  return display(value);
}

const PREVIEW_ROWS = 30;

function Results({ result, title }: { result: ScienceResult; title: string }) {
  const { table, plot, provenance } = result;
  const baseName = `${provenance.packId}-${result.operationId}`;
  const csv = table ?? (plot ? { columns: [plot.xLabel, plot.yLabel], rows: plot.x.map((v, i) => [v, plot.y[i]]) } : { columns: ["Metric", "Value"], rows: Object.entries(result.values) });
  return <section className="science-results" aria-label="Latest result">
    <h4>Last run · {title}</h4>
    <p role="status">{result.summary}</p>
    <dl>{Object.entries(result.values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{display(value)}</dd></div>)}</dl>
    {plot && <Plot plot={plot} />}
    {table && <div className="science-table">
      <table aria-label="Result table"><thead><tr>{table.columns.map((col, i) => <th key={i} scope="col">{col}</th>)}</tr></thead>
        <tbody>{table.rows.slice(0, PREVIEW_ROWS).map((row, i) =>
          <tr key={i}>{row.map((cell, j) => <td key={j}>{display(cell)}</td>)}</tr>)}</tbody>
      </table>
      {table.rows.length > PREVIEW_ROWS && <small>Showing {PREVIEW_ROWS} of {table.rows.length} rows. Export CSV for all rows.</small>}
    </div>}
    <details><summary>Inputs and provenance</summary>
      <pre>{JSON.stringify({ inputs: result.inputs, provenance }, null, 2)}</pre>
    </details>
    <div className="science-actions">
      <button type="button" onClick={() => saveText(JSON.stringify(result, null, 2), `${baseName}.json`, "application/json")}>Export JSON</button>
      <button type="button" onClick={() => saveText(writeTableCsv([csv.columns.map(csvCell), ...csv.rows.map((row) => row.map(csvCell))]),
        `${baseName}.csv`, "text/csv;charset=utf-8")}>Export CSV</button>
    </div>
  </section>;
}

function FieldEditor({ field, value, onEdit, options, onImport, imported }: {
  field: ScienceField; value: string; onEdit: (value: string) => void;
  options: SourceOption[]; onImport: (option: SourceOption) => void; imported: string | undefined;
}) {
  const compact = field.kind === "number" || field.kind === "integer";
  return <div className="science-field">
    <label><span>{field.label}{field.unit && <small> ({field.unit})</small>}</span>
      {compact
        ? <input type="text" inputMode={field.kind === "integer" ? "numeric" : "decimal"}
          value={value} onChange={(e) => onEdit(e.target.value)} />
        : <textarea rows={field.kind === "matrix" || field.kind === "text" ? 3 : 2}
          value={value} onChange={(e) => onEdit(e.target.value)} spellCheck={field.kind === "text"} />}
    </label>
    {field.description && <small>{field.description}</small>}
    {options.length > 0 && <select aria-label={`Import ${field.label} from a connected result`} value=""
      onChange={(e) => { if (e.target.value) onImport(options[Number(e.target.value)]); }}>
      <option value="">Use connected result…</option>
      {options.map((option, i) => <option key={i} value={i}>{option.label}</option>)}
    </select>}
    {imported && <small>Source · {imported}</small>}
  </div>;
}

type Run = (operationId: string, inputs: Record<string, unknown>, sources: Sources) => Promise<void>;

function OperationEditor({ op, latest, options, run, busy, setBusy }: {
  op: ScienceOperation; latest: ScienceResult | null; options: SourceOption[]; run: Run;
  busy: boolean; setBusy: (busy: boolean) => void;
}) {
  const [draft, setDraft] = useState(() => draftInputs(op, latest?.inputs ?? op.example));
  const [sources, setSources] = useState<Sources>(() => latest?.provenance.sources ?? {});
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try { await run(op.id, parseInputs(op, draft), sources); }
    catch (e) { setError(e instanceof Error ? e.message : "The calculation failed."); }
    finally { setBusy(false); }
  }
  return <form onSubmit={submit}>
    <p>{op.description}</p>
    <fieldset disabled={busy}>
      {op.fields.map((field) => {
        const available = compatibleOptions(field, options);
        const source = sources[field.key];
        const previous = source && available.find((option) =>
          (source.sourceKey ? option.reference.sourceKey === source.sourceKey : option.reference.nodeId === source.nodeId) &&
          option.reference.resultHash === source.resultHash && JSON.stringify(option.reference.selector) === JSON.stringify(source.selector));
        return <FieldEditor key={field.key} field={field} value={draft[field.key] ?? ""} options={available}
          imported={source && (previous?.label ?? "Result changed or disconnected. Select a new value or edit this input.")}
          onEdit={(value) => {
            setDraft((old) => ({ ...old, [field.key]: value }));
            setSources((old) => { const next = { ...old }; delete next[field.key]; return next; });
            setError("");
          }}
          onImport={(option) => {
            setDraft((old) => ({ ...old, [field.key]: fieldText(field, option.value) }));
            setSources((old) => ({ ...old, [field.key]: option.reference }));
            setError("");
          }} />;
      })}
      <div className="science-actions">
        <button className="science-run" type="submit">{busy ? "Running…" : "Run calculation"}</button>
        <button type="button" onClick={() => { setDraft(draftInputs(op, op.example)); setSources({}); setError(""); }}>Load example</button>
      </div>
    </fieldset>
    {error && <p role="alert">{error}</p>}
  </form>;
}

function Workbench({ state, options, run }: { state: ScienceState; options: SourceOption[]; run: Run }) {
  const [selected, setSelected] = useState(state.latest?.operationId ?? state.operations[0].id);
  const [busy, setBusy] = useState(false);
  const op = state.operations.find((candidate) => candidate.id === selected) ?? state.operations[0];
  const latest = state.latest?.operationId === op.id ? state.latest : null;
  return <>
    <label>Operation<select value={op.id} disabled={busy} onChange={(e) => setSelected(e.target.value)}>
      {state.operations.map((operation) => <option key={operation.id} value={operation.id}>{operation.title}</option>)}
    </select></label>
    <OperationEditor key={op.id + ":" + (latest?.provenance.resultHash ?? "")} op={op} latest={latest} options={options}
      run={run} busy={busy} setBusy={setBusy} />
    {state.latest && <Results result={state.latest} title={
      state.operations.find((candidate) => candidate.id === state.latest?.operationId)?.title ?? state.latest.operationId
    } />}
  </>;
}

export const ScientificNodeView = memo(function ScientificNodeView({ data, id, selected }: NodeProps<ScientificFlowNode>) {
  const state = useMemo(() => readScienceState(data.raw), [data.raw]);
  const options = useMemo(() => sourceOptions(data.parents), [data.parents]);
  const collapsed = useLodVisibility();
  return <NodeShell kindClassName="scientific-node" selected={!!selected} collapsed={collapsed}
    header={<NodeHeading glyph="chart">{data.title}</NodeHeading>} bodyClassName="science-body nodrag nopan nowheel">
    {state?.error ? <p role="alert">{state.error}</p> : state && data.pluginId === "science_" + state.packId
      ? <Workbench state={state} options={options}
        run={(operationId, inputs, sources) => data.store.runScientificOperation(data.pluginId, id, operationId, inputs, sources)} />
      : <p role="alert">This workbench version could not be loaded. Check the plugin in Settings.</p>}
  </NodeShell>;
});
