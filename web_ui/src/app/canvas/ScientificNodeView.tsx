import { memo, useId, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import type { SceneStore, ScienceEditorSession } from "./sceneStore";
import { downloadBlob } from "./downloadTextFile";
import { writeTableCsv } from "./tableCsv";
import { createLinearScale } from "./charts/chartScales";
import {
  compatibleOptions, draftInputs, FieldError, fieldText, matchesSource, parseInputs, readScienceState, sourceOptions,
  type EditorDraft, type ScienceField, type ScienceLimits, type ScienceOperation, type ScienceParent, type SciencePlot, type ScienceResult,
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

  return <figure className="science-plot">
    <figcaption>{plot.title}</figcaption>
    <span>{plot.yLabel} · {y.low} to {y.high}</span>
    <svg viewBox="0 0 320 110" role="img" aria-label={`${plot.title}. ${plot.yLabel} versus ${plot.xLabel}, ${plot.x.length} points.`}>
      <path d="M10 8V98H310" fill="none" stroke="currentColor" opacity=".4" />
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.8" />
      {plot.x.length <= 48 && plot.x.map((v, i) =>
        <circle key={i} cx={x.at(v)} cy={y.at(plot.y[i])} r="2.4" fill="currentColor"><title>{v}, {plot.y[i]}</title></circle>)}
    </svg>
    <span>{plot.xLabel} · {x.low} to {x.high}</span>
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

function Results({ result, title, exportJson }: { result: ScienceResult; title: string; exportJson: () => Promise<string> }) {
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
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
      <button type="button" disabled={exporting} onClick={async () => {
        setError("");
        setExporting(true);
        try { saveText(await exportJson(), `${baseName}.json`, "application/json"); }
        catch (e) { setError(e instanceof Error ? e.message : "Export failed."); }
        finally { setExporting(false); }
      }}>Export JSON</button>
      <button type="button" onClick={() => saveText(writeTableCsv([csv.columns.map(csvCell), ...csv.rows.map((row) => row.map(csvCell))]),
        `${baseName}.csv`, "text/csv;charset=utf-8")}>Export CSV</button>
    </div>
    {error && <p role="alert">{error}</p>}
  </section>;
}

function FieldEditor({ field, value, onEdit, options, onImport, imported, errorId, stale }: {
  field: ScienceField; value: string; onEdit: (value: string) => void;
  options: SourceOption[]; onImport: (option: SourceOption) => void; imported?: string; errorId?: string; stale: boolean;
}) {
  const id = useId();
  const description = [field.description && id + "-hint", imported && id + "-source", errorId].filter(Boolean).join(" ");
  const common = { id, name: field.key, value, "aria-invalid": errorId || stale ? true as const : undefined,
    "aria-describedby": description || undefined };
  return <div className="science-field">
    <label htmlFor={id}>{field.unit ? `${field.label} (${field.unit})` : field.label}</label>
    {field.kind === "number" || field.kind === "integer"
      ? <input {...common} type="text" inputMode={field.kind === "integer" ? "numeric" : "decimal"}
        onChange={(e) => onEdit(e.target.value)} />
      : <textarea {...common} rows={field.kind === "matrix" || field.kind === "text" ? 3 : 2}
        onChange={(e) => onEdit(e.target.value)} spellCheck={field.kind === "text"} />}
    {field.description && <small id={id + "-hint"}>{field.description}</small>}
    {options.length > 0 && <select aria-label={`Import ${field.label} from a connected result`} value=""
      onChange={(e) => { if (e.target.value) onImport(options[Number(e.target.value)]); }}>
      <option value="">Use connected result…</option>
      {options.map((option, i) => <option key={i} value={i}>{option.label}</option>)}
    </select>}
    {imported && <small id={id + "-source"}>Source · {imported}</small>}
  </div>;
}

type Run = (operationId: string, inputs: Record<string, unknown>, sources: Sources, expectedRun?: string) => Promise<void>;
function restoreDraft(op: ScienceOperation, result: ScienceResult | null, revision: string): EditorDraft {
  return { values: draftInputs(op, result?.inputs ?? op.example), sources: result?.provenance.sources ?? {}, revision, dirty: false };
}

function OperationEditor({ session, op, result, revision, runToken, limits, options, run, busy, setBusy }: {
  session: ScienceEditorSession; op: ScienceOperation; result: ScienceResult | null; revision: string; runToken?: string; limits?: ScienceLimits;
  options: SourceOption[]; run: Run; busy: boolean; setBusy: (busy: boolean) => void;
}) {
  const [draft, setDraft] = useState(() => session.drafts.get(op.id) ?? restoreDraft(op, result, revision));
  useLayoutEffect(() => {
    if (draft.dirty) session.drafts.set(op.id, draft);
    else session.drafts.delete(op.id);
  }, [session, op.id, draft]);
  const [error, setError] = useState<{ message: string; field?: string } | null>(null);
  const errorId = useId();
  const changed = draft.revision !== revision;
  if (changed && !draft.dirty) {
    setDraft(restoreDraft(op, result, revision));
    setError(null);
  }
  const conflict = changed && draft.dirty;
  const bound = Object.entries(draft.sources);
  const stale = bound.some(([, ref]) => !options.some((option) => matchesSource(option, ref)));
  const pending = useRef(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || conflict || stale) return;
    setError(null);
    const form = event.currentTarget;
    let inputs: Record<string, unknown>;
    try { inputs = parseInputs(op, draft.values, limits); }
    catch (e) {
      const field = e instanceof FieldError ? e.key : undefined;
      setError({ message: e instanceof Error ? e.message : "Check the inputs.", field });
      if (field) (form.elements.namedItem(field) as HTMLElement | null)?.focus();
      return;
    }
    pending.current = true;
    setBusy(true);
    try {
      await run(op.id, inputs, draft.sources, runToken);
      setDraft((old) => ({ ...old, dirty: false }));
    } catch (e) { setError({ message: e instanceof Error ? e.message : "The calculation failed." }); }
    finally { pending.current = false; setBusy(false); }
  }

  return <form onSubmit={submit}>
    <p>{op.description}</p>
    <fieldset disabled={busy}>
      {op.fields.map((field) => {
        const available = compatibleOptions(field, options, limits);
        const source = draft.sources[field.key];
        const previous = source && available.find((option) => matchesSource(option, source));
        return <FieldEditor key={field.key} field={field} value={draft.values[field.key] ?? ""} options={available}
          stale={!!source && !previous} errorId={error?.field === field.key ? errorId : undefined}
          imported={source && (previous?.label ?? "Result changed or disconnected. Select its current value or edit this input.")}
          onEdit={(value) => {
            setDraft((old) => {
              const sources = { ...old.sources };
              delete sources[field.key];
              return { ...old, values: { ...old.values, [field.key]: value }, sources, dirty: true };
            });
            setError(null);
          }}
          onImport={(option) => {
            setDraft((old) => ({ ...old, dirty: true, values: { ...old.values, [field.key]: fieldText(field, option.value) },
              sources: { ...old.sources, [field.key]: option.reference } }));
            setError(null);
          }} />;
      })}
      {conflict && <div className="science-conflict">
        <p role="alert">The saved run changed while you were editing. Your inputs are still here.</p>
        <div className="science-actions">
          <button type="button" onClick={() => { setDraft({ ...draft, revision }); setError(null); }}>Keep my inputs</button>
          <button type="button" onClick={() => { setDraft(restoreDraft(op, result, revision)); setError(null); }}>
            {result ? "Load saved inputs" : "Reset to example"}
          </button>
        </div>
      </div>}
      <div className="science-actions">
        <button className="science-run" type="submit" disabled={conflict || stale}>{busy ? "Running…" : "Run calculation"}</button>
        <button type="button" onClick={() => {
          setDraft({ ...restoreDraft(op, null, revision), dirty: true }); setError(null);
        }}>Load example</button>
      </div>
    </fieldset>
    {error && <p id={errorId} role="alert">{error.message}</p>}
  </form>;
}

function Workbench({ session, state, options, run, exportJson }: {
  session: ScienceEditorSession; state: ScienceState; options: SourceOption[]; run: Run; exportJson: () => Promise<string>;
}) {
  const [selected, setSelected] = useState(session.selected ?? state.latest?.operationId ?? state.operations[0].id);
  const [busy, setBusy] = useState(false);
  const op = state.operations.find((candidate) => candidate.id === selected) ?? state.operations[0];
  const result = state.latest?.operationId === op.id ? state.latest : null;
  const revision = state.runToken ?? JSON.stringify(state.latest?.provenance ?? null);
  return <>
    <label>Operation<select value={op.id} disabled={busy} onChange={(e) => { session.selectOperation(e.target.value); setSelected(e.target.value); }}>
      {state.operations.map((operation) => <option key={operation.id} value={operation.id}>{operation.title}</option>)}
    </select></label>
    <OperationEditor key={op.id} session={session} op={op} result={result} revision={revision} runToken={state.runToken} limits={state.limits}
      options={options} run={run} busy={busy} setBusy={setBusy} />
    {state.latest && <Results key={revision} result={state.latest} exportJson={exportJson} title={
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
    {!data.raw ? <p role="status">Enable {data.title} in Settings → Plugins to open this workbench.</p>
      : state?.error ? <p role="alert">{state.error}</p> : state && data.pluginId === "science_" + state.packId
      ? <Workbench key={state.nodeKey ?? id} session={data.store.scientificEditor(id, state.nodeKey)} state={state} options={options}
        exportJson={() => data.store.exportScientificResult(data.pluginId, id, state.runToken)}
        run={(operationId, inputs, sources, token) => data.store.runScientificOperation(data.pluginId, id, operationId, inputs, sources, token)} />
      : <p role="alert">This workbench version could not be loaded. Check the plugin in Settings.</p>}
  </NodeShell>;
});
