import { memo, useId, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import type { SceneStore, ScienceEditorSession } from "./sceneStore";
import { downloadBlob } from "./downloadTextFile";
import { writeTableCsv } from "./tableCsv";
import { createLinearScale } from "./charts/chartScales";
import { ScientificResultViews } from "./ScientificResultViews";
import {
  chartableScienceTypes, columnChoices, compatibleOptions, connectedInputText, draftInputs, FieldError, fieldText, formatMeasurement, formatScienceNumber, matchesSource, parseInputs, readScienceState, resultMeasurement, sourceOptions,
  type EditorDraft, type ScienceField, type ScienceLimits, type ScienceOperation, type ScienceParent, type SciencePlot, type ScienceResult,
  type ScienceChartLink, type ScienceChartType, type ScienceState, type ScienceRunnerConfiguration, type ScientificColumnType, type Sources, type SourceOption, type SourceReference,
} from "./scientificWorkbench";
import "./ScientificNodeView.css";

export type ScientificFlowNode = Node<{
  title: string; raw: string; pluginId: string; store: SceneStore; parents: ScienceParent[]; charts?: ScienceChartLink[];
}, "scientific">;

function display(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return formatScienceNumber(value);
  // Arrays and records render their numbers the same rounded way.
  return JSON.stringify(value, (_key, item: unknown) =>
    typeof item === "number" && Number.isFinite(item) ? Number(formatScienceNumber(item)) : item) ?? "";
}

/** Budget and interval cells: a rounded number, or a dash when absent. */
function numberCell(value: number | undefined): string {
  return value === undefined ? "—" : formatScienceNumber(value);
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
  // Exports keep the exact value; only on-screen text is rounded.
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

const PREVIEW_ROWS = 30;

function Results({ result, title, exportJson, exportCsv, onChart, onRefresh, charts }: {
  result: ScienceResult; title: string; exportJson: () => Promise<string>; exportCsv: () => Promise<Blob>;
  onChart: (type: ScienceChartType) => void; onRefresh: (chart: ScienceChartLink) => void;
  charts: ScienceChartLink[];
}) {
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const { table, tableAsset, plot, provenance } = result;
  const chartTypes = chartableScienceTypes(result);
  const [chartType, setChartType] = useState<ScienceChartType>(chartTypes[0] ?? "scatter");
  const baseName = `${provenance.packId}-${result.operationId}`;
  const csv = table ?? (plot ? { columns: [plot.xLabel, plot.yLabel], rows: plot.x.map((v, i) => [v, plot.y[i]]) } : { columns: ["Metric", "Value"], rows: Object.entries(result.values) });
  return <section className="science-results" aria-label="Latest result">
    <h4>Last successful run · {title}</h4>
    <p role="status">{result.summary}</p>
    <dl>{Object.entries(result.values).map(([label, value]) => {
      const measured = resultMeasurement(result, label);
      const unpropagatable = result.uncertainty?.unsupportedOutputs?.[label];
      return <div key={label}><dt>{label}</dt><dd>{unpropagatable ? `${display(value)} · uncertainty unavailable` : measured ? formatMeasurement(measured) : display(value)}</dd></div>;
    })}</dl>
    {result.uncertainty && <details className="science-uncertainty-budget">
      <summary>Propagated uncertainty budget · {result.uncertainty.method === "gum" ? "first-order GUM" : "Monte Carlo"}</summary>
      {Object.entries(result.uncertainty.outputs).map(([name, measured]) =>
        <section key={name}>
          <h5>{name} · {formatMeasurement(measured)}</h5>
          {measured.interval && <small>{Math.round((measured.coverageProbability ?? 0.95) * 100)}% {measured.method === "monte_carlo" ? "Monte Carlo quantile coverage interval" : "coverage interval"} [{numberCell(measured.interval[0])}, {numberCell(measured.interval[1])}]{measured.method === "monte_carlo" ? " · Gaussian input model" : ` · coverage factor k = ${numberCell(measured.coverageFactor)}`}</small>}
          {measured.budgetNote && <small>{measured.budgetNote}</small>}
          {measured.budget && <table aria-label={`Uncertainty budget for ${name}`}>
            <thead><tr><th>Contributing input</th><th>Standard uncertainty</th><th>Sensitivity</th><th>Signed contribution</th><th>Variance / covariance</th></tr></thead>
            <tbody>{measured.budget.map((entry, index) => <tr key={index}>
              <td>{entry.input}{entry.kind === "covariance" ? " (shared source)" : entry.unit ? ` (${entry.unit})` : ""}</td>
              <td>{numberCell(entry.standardUncertainty)}</td><td>{numberCell(entry.sensitivity)}</td>
              <td>{numberCell(entry.signedContribution)}</td><td>{numberCell(entry.variance)}</td>
            </tr>)}</tbody>
          </table>}
        </section>)}
      {Object.entries(result.uncertainty.unsupportedOutputs ?? {}).map(([name, why]) =>
        <p role="note" key={name}>Uncertainty propagation unavailable for {name}: {why}</p>)}
    </details>}
    {result.views && <ScientificResultViews views={result.views} />}
    {plot && !result.views?.some((view) => view.type === "survival_curve") && <Plot plot={plot} />}
    {table && <div className="science-table">
      <table aria-label="Result table"><thead><tr>{table.columns.map((col, i) => <th key={i} scope="col">{col}</th>)}</tr></thead>
        <tbody>{table.rows.slice(0, PREVIEW_ROWS).map((row, i) =>
          <tr key={i}>{row.map((cell, j) => <td key={j}>{display(cell)}</td>)}</tr>)}</tbody>
      </table>
      {table.rows.length > PREVIEW_ROWS && <small>Showing {PREVIEW_ROWS} of {table.rows.length} rows. Export CSV for all rows.</small>}
    </div>}
    {tableAsset && <div className="science-table">
      <p role="status">Saved result table · {tableAsset.rowCount.toLocaleString()} rows · {tableAsset.columnCount} typed columns</p>
      <table aria-label="Generated result table preview">
        <thead><tr>{tableAsset.columns.slice(0, tableAsset.previewColumns).map((col, i) =>
          <th key={i} scope="col" title={col.type + ", " + col.missingCount + " missing"}>{col.label}<small>{col.type}{col.missingCount ? " · " + col.missingCount + " missing" : ""}</small></th>)}</tr></thead>
        <tbody>{tableAsset.preview.map((row, i) =>
          <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? "—" : display(cell)}</td>)}</tr>)}</tbody>
      </table>
      <small>Preview: {tableAsset.preview.length} of {tableAsset.rowCount.toLocaleString()} rows. Export CSV for the full table or connect this node to use its typed columns.</small>
    </div>}
    {provenance.inputUnits != null && <details><summary>Units used</summary>
      <pre>{JSON.stringify(provenance.inputUnits, null, 2)}</pre>
    </details>}
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
      <button type="button" disabled={exporting} onClick={async () => {
        setError("");
        setExporting(true);
        try {
          if (tableAsset) {
            downloadBlob(await exportCsv(), `${baseName}.csv`);
          } else {
            saveText(writeTableCsv([csv.columns.map(csvCell), ...csv.rows.map((row) => row.map(csvCell))]),
              `${baseName}.csv`, "text/csv;charset=utf-8");
          }
        } catch (e) { setError(e instanceof Error ? e.message : "CSV export failed."); }
        finally { setExporting(false); }
      }}>Export CSV</button>
      {chartTypes.length > 0 && <>
        <label>Chart type
          <select aria-label="Chart type" value={chartTypes.includes(chartType) ? chartType : chartTypes[0]}
            onChange={(e) => setChartType(e.target.value as ScienceChartType)}>
            {chartTypes.map((type) =>
              <option key={type} value={type}>{type === "boxplot" ? "Box plot" : type === "line" ? "Fitted line with 95% confidence envelope" : type === "heatmap" ? "Heatmap" : "Scatter"}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => onChart(chartTypes.includes(chartType) ? chartType : chartTypes[0])}>Chart this</button>
      </>}
    </div>
    {charts.length > 0 && <section aria-label="Linked charts">
      <strong>Linked charts</strong>
      {charts.map((chart) => <div key={chart.id} className="science-actions">
        <span>{chart.title} · {chart.chartType} · <span role="status">{chart.stale ? "Source changed · refresh to update" : "Input up to date"}</span></span>
        <small title={chart.receiptHash}>Receipt {chart.receiptHash.slice(0, 12)}…</small>
        <button type="button" disabled={!chart.stale} onClick={() => onRefresh(chart)}>Refresh chart</button>
      </div>)}
    </section>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function FieldEditor({ field, value, onEdit, options, onImport, imported, errorId, stale, chosenUnit, unitSetter, columnOptions }: {
  field: ScienceField; value: string; onEdit: (value: string) => void;
  options: SourceOption[]; onImport: (option: SourceOption) => void; imported?: string; errorId?: string; stale: boolean;
  columnOptions: Array<{ index: number; label: string; type: ScientificColumnType }>;
  /** The unit typed into this field's unit-setting sibling (field.unit_field) and that sibling's label. */
  chosenUnit?: string; unitSetter?: string;
}) {
  const unitOverridden = !!chosenUnit && chosenUnit !== field.default_unit;
  const dimensioned = !["text", "choice", "table", "column"].includes(field.kind);
  const id = useId();
  const description = [field.description && id + "-hint", dimensioned && id + "-units",
    imported && id + "-source", errorId].filter(Boolean).join(" ");
  const common = { id, name: field.key, value, "aria-invalid": errorId || stale ? true as const : undefined,
    "aria-describedby": description || undefined };
  return <div className="science-field">
    <label htmlFor={id}>{dimensioned ? `${field.label} (${field.column_units?.length ? field.unit : chosenUnit || field.default_unit || field.unit || "1"})` : field.label}</label>
    {field.kind === "choice"
      ? <select {...common} onChange={(e) => onEdit(e.target.value)}>
        {field.choices?.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
      </select>
      : field.kind === "column"
      ? <select {...common} onChange={(e) => onEdit(e.target.value)}>
        <option value="">Select a column…</option>
        {columnOptions.map((col) => <option key={col.index} value={col.index}>{col.label} · {col.type}</option>)}
      </select>
      : field.kind === "table"
      ? <input {...common} type="text" value={value ? "Connected typed table selected" : "Select a connected typed table below"} readOnly />
      : field.kind === "number" || field.kind === "integer"
      ? <input {...common} type="text" inputMode="text"
        onChange={(e) => onEdit(e.target.value)} />
      : <textarea {...common} rows={field.kind === "matrix" || field.kind === "text" ? 3 : 2}
        onChange={(e) => onEdit(e.target.value)} spellCheck={field.kind === "text"} />}
    {field.description && <small id={id + "-hint"}>{field.description}</small>}
    {dimensioned && unitOverridden && <small id={id + "-units"}>
      Unit: {chosenUnit}, set by {unitSetter ?? field.unit_field}.
    </small>}
    {dimensioned && !unitOverridden && <small id={id + "-units"}>
      Default unit: {field.column_units?.length
        ? field.column_units.map((unit, i) => `column ${i + 1}: ${unit}`).join("; ")
        : field.default_unit || field.unit || "1"} · {field.dimension || "dimensionless"}.
      {field.kind === "vector" || field.kind === "matrix"
        ? ' Use JSON arrays; quote values with unit suffixes, e.g. ["25 mm", "2 cm"].'
        : " You may append a compatible unit, e.g. 25 mm."}
    </small>}
    {options.length > 0 && <select aria-label={`Import ${field.label} from a connected result`} value=""
      onChange={(e) => { if (e.target.value) onImport(options[Number(e.target.value)]); }}>
      <option value="">Use connected result…</option>
      {options.map((option, i) => <option key={i} value={i}>{option.label}</option>)}
    </select>}
    {imported && <small id={id + "-source"}>Source · {imported}</small>}
  </div>;
}

// Reuse the established plugin transport without broadening sceneStore's API.
function tableIntent(store: SceneStore, pluginId: string, name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const endpoint = store as unknown as { scientificIntent: (id: string, action: string, input: Record<string, unknown>) => Promise<Record<string, unknown>> };
  return endpoint.scientificIntent(pluginId, name, args);
}

type FetchTableColumn = (option: SourceOption) => Promise<{ values: number[]; reference: SourceReference }>;

function binaryBase64(bytes: Uint8Array): string {
  const chunks: string[] = [];
  for (let i = 0; i < bytes.length; i += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(i, i + 32768)));
  }
  return btoa(chunks.join(""));
}

function TableEditor({ state, nodeId, pluginId, store }: {
  state: ScienceState; nodeId: string; pluginId: string; store: SceneStore;
}) {
  const [source, setSource] = useState<"paste" | "file">("paste");
  const [format, setFormat] = useState<"csv" | "tsv">("csv");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("Name,Temperature (degC),Observed\nA,22.5,true\nB,NA,false");
  const [missing, setMissing] = useState("NA,N/A,null");
  const [overrides, setOverrides] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const table = state.table;
  const rowsLimit = state.limits?.tableRows ?? 100000;
  const cellsLimit = state.limits?.tableCells ?? 1000000;
  const fileLimit = state.limits?.tableSourceBytes ?? 64 * 1024 * 1024;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const parsed: unknown = JSON.parse(overrides);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) ||
          Object.values(parsed).some((value) => !["numeric", "text", "categorical", "datetime", "boolean"].includes(String(value))))
        throw new Error("Column types must map headers to numeric, text, categorical, datetime or boolean.");
      const args: Record<string, unknown> = {
        node_id: nodeId, format, missing_values: ["", ...missing.split(",").map((v) => v.trim()).filter(Boolean)],
        column_types: parsed, ...(state.runToken && { expected_run: state.runToken }),
      };
      if (source === "file") {
        if (!file) throw new Error("Choose a CSV or TSV file.");
        if (file.size > fileLimit) throw new Error("The selected file exceeds the import size limit.");
        args.filename = file.name;
        args.encoded_file = binaryBase64(new Uint8Array(await file.arrayBuffer()));
      } else {
        // Pasted UTF-8 data has the same byte cap as a selected file.
        if (text.length > fileLimit || new TextEncoder().encode(text).byteLength > fileLimit)
          throw new Error("Pasted table data exceeds the import size limit.");
        args.text = text;
      }
      await tableIntent(store, pluginId, "import_table", args);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not import the table.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="science-data-table" aria-label="Typed data table">
    <h4>Typed data table</h4>
    <p>Import mixed data and missing values. Tables support {rowsLimit.toLocaleString()} rows and {cellsLimit.toLocaleString()} cells, stored outside the graph.</p>
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
        <label>Data source
          <select aria-label="Table data source" value={source} onChange={(e) => setSource(e.target.value as "paste" | "file")}>
            <option value="paste">Paste CSV / TSV</option><option value="file">Choose a file</option>
          </select>
        </label>
        <label>Format
          <select aria-label="Table format" value={format} onChange={(e) => setFormat(e.target.value as "csv" | "tsv")}>
            <option value="csv">CSV (comma-separated)</option><option value="tsv">TSV (tab-separated)</option>
          </select>
        </label>
        {source === "file" ?
          <label>CSV or TSV file
            <input type="file" aria-label="CSV or TSV file" accept=".csv,.tsv,text/csv,text/tab-separated-values"
              onChange={(e) => {
                const next = e.target.files?.[0] ?? null;
                setFile(next);
                if (next?.name.toLowerCase().endsWith(".tsv")) setFormat("tsv");
                if (next?.name.toLowerCase().endsWith(".csv")) setFormat("csv");
              }} />
          </label> :
          <label>Pasted table text
            <textarea rows={4} aria-label="Pasted table text" value={text} onChange={(e) => setText(e.target.value)} />
          </label>}
        <label>Missing-value markers (comma-separated; blank always missing)
          <input type="text" aria-label="Missing-value markers" value={missing} onChange={(e) => setMissing(e.target.value)} />
        </label>
        <label>Column type overrides (JSON, optional)
          <textarea rows={2} aria-label="Column type overrides" value={overrides} onChange={(e) => setOverrides(e.target.value)} />
        </label>
        <button type="submit" className="science-run">{busy ? "Importing…" : "Import typed table"}</button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
    </form>
    {table && <div className="science-imported">
      <p role="status">{table.sourceName} · {table.rowCount.toLocaleString()} rows · {table.columnCount} columns</p>
      <div className="science-table">
        <table aria-label="Imported typed table preview">
          <thead><tr>{table.columns.slice(0, table.previewColumns).map((col, i) =>
            <th scope="col" key={i} title={col.type + ", " + col.missingCount + " missing"}>{col.label}<small>{col.type}{col.missingCount ? " · " + col.missingCount + " missing" : ""}</small></th>)}</tr></thead>
          <tbody>{table.preview.map((row, i) => <tr key={i}>{row.map((cell, j) =>
            <td key={j}>{cell === null ? "—" : display(cell)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <small>Preview: {table.preview.length} rows, {table.previewColumns} columns. Connect this workbench to another to use the complete typed table with text, categorical and numeric column pickers, or select a bounded numeric vector.</small>
      <button type="button" disabled={exporting} onClick={async () => {
        setError("");
        setExporting(true);
        try {
          downloadBlob(await store.exportScientificTableCsv(pluginId, nodeId, "import", state.runToken),
            `${table.sourceName.replace(/[^a-zA-Z0-9._-]/g, "_")}.csv`);
        } catch (e) { setError(e instanceof Error ? e.message : "CSV export failed."); }
        finally { setExporting(false); }
      }}>Export full table CSV</button>
      <details><summary>Import receipt and parsing options</summary>
        <pre>{JSON.stringify(table.provenance, null, 2)}</pre>
      </details>
    </div>}
  </section>;
}

type Run = (operationId: string, inputs: Record<string, unknown>, sources: Sources, expectedRun?: string,
  method?: "auto" | "gum" | "monte_carlo") => Promise<void>;
type Cancel = (operationId: string) => Promise<void>;
function restoreDraft(op: ScienceOperation, result: ScienceResult | null, revision: string,
  options: SourceOption[] = [], configured?: ScienceRunnerConfiguration | null): EditorDraft {
  const runner = configured?.operationId === op.id ? configured : null;
  const sources = runner?.sources ?? result?.provenance.sources ?? {};
  const values = draftInputs(op, runner?.inputs ?? result?.inputs ?? op.example);
  // Restore the linked scalar in its source units instead of reusing the
  // normalized solver value and losing the original input-unit provenance.
  for (const field of op.fields) {
    const source = sources[field.key];
    const option = source && options.find((candidate) => matchesSource(candidate, source));
    if (option && field.kind === "number" && option.unit) {
      values[field.key] = connectedInputText(field, option);
    }
  }
  return { values, sources, revision, dirty: false };
}

function OperationEditor({ session, op, result, configured, revision, runToken, limits, options, run, configure, cancel, fetchTableColumn, busy, setBusy }: {
  session: ScienceEditorSession; op: ScienceOperation; result: ScienceResult | null;
  configured?: ScienceRunnerConfiguration | null; revision: string; runToken?: string; limits?: ScienceLimits;
  options: SourceOption[]; run: Run; configure: Run; cancel: Cancel; fetchTableColumn: FetchTableColumn;
  busy: boolean; setBusy: (busy: boolean) => void;
}) {
  const [draft, setDraft] = useState(() => session.drafts.get(op.id) ?? restoreDraft(op, result, revision, options, configured));
  useLayoutEffect(() => {
    if (draft.dirty) session.drafts.set(op.id, draft);
    else session.drafts.delete(op.id);
  }, [session, op.id, draft]);
  const [error, setError] = useState<{ message: string; field?: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [configuring, setConfiguring] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [uncertaintyMethod, setUncertaintyMethod] = useState<"auto" | "gum" | "monte_carlo">("auto");
  const errorId = useId();
  const changed = draft.revision !== revision;
  if (changed && !draft.dirty) {
    setDraft(restoreDraft(op, result, revision, options, configured));
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
    setRunning(true);
    setBusy(true);
    try {
      await run(op.id, inputs, draft.sources, runToken, uncertaintyMethod);
      setDraft((old) => ({ ...old, dirty: false }));
    } catch (e) { setError({ message: e instanceof Error ? e.message : "The calculation failed." }); }
    finally { pending.current = false; setRunning(false); setBusy(false); }
  }

  async function saveForRunner() {
    if (pending.current || conflict || stale) return;
    setError(null);
    let inputs: Record<string, unknown>;
    try { inputs = parseInputs(op, draft.values, limits); }
    catch (e) {
      setError({ message: e instanceof Error ? e.message : "Check the inputs.",
        field: e instanceof FieldError ? e.key : undefined });
      return;
    }
    pending.current = true;
    setConfiguring(true);
    setBusy(true);
    try {
      await configure(op.id, inputs, draft.sources, runToken);
      setDraft((old) => ({ ...old, dirty: false }));
    } catch (e) { setError({ message: e instanceof Error ? e.message : "Could not save the runner configuration." }); }
    finally { pending.current = false; setConfiguring(false); setBusy(false); }
  }

  return <form onSubmit={submit}>
    <p>{op.description}</p>
    {(op.methods || op.citations?.length) && <details className="science-methods">
      <summary>Methods and citations</summary>
      {op.methods && <p>{op.methods}</p>}
      {!!op.citations?.length && <ul>
        {op.citations.map((citation) => <li key={citation.url + citation.label}>
          <a href={citation.url} target="_blank" rel="noopener noreferrer">{citation.label}</a>
        </li>)}
      </ul>}
    </details>}
    <fieldset disabled={busy}>
      {op.fields.map((field) => {
        const available = compatibleOptions(field, options, limits);
        const source = draft.sources[field.key];
        const previous = source && available.find((option) => matchesSource(option, source));
        const chosenUnit = field.unit_field ? draft.values[field.unit_field]?.trim() : undefined;
        const unitSetter = op.fields.find((other) => other.key === field.unit_field)?.label;
        return <FieldEditor key={field.key} field={field} value={draft.values[field.key] ?? ""} options={available}
          columnOptions={columnChoices(field, draft.values)}
          chosenUnit={chosenUnit || undefined} unitSetter={unitSetter} stale={!!source && !previous} errorId={error?.field === field.key ? errorId : undefined}
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
            if (option.tableColumn) {
              if (pending.current) return;
              pending.current = true;
              setBusy(true);
              setError(null);
              void fetchTableColumn(option).then(({ values, reference }) => {
                setDraft((old) => ({ ...old, dirty: true, values: { ...old.values, [field.key]: fieldText(field, values) },
                  sources: { ...old.sources, [field.key]: reference } }));
              }).catch((e) => {
                setError({ message: e instanceof Error ? e.message : "Could not read the table column.", field: field.key });
              }).finally(() => { pending.current = false; setBusy(false); });
              return;
            }
            setDraft((old) => {
              const values = { ...old.values, [field.key]: connectedInputText(field, option) };
              // The selected table determines the meaning of every dependent
              // column index. Force explicit re-selection when it changes.
              if (field.kind === "table") for (const dependent of op.fields) {
                if (dependent.kind === "column" && dependent.table_field === field.key) values[dependent.key] = "";
              }
              return { ...old, dirty: true, values,
                sources: { ...old.sources, [field.key]: option.reference } };
            });
            setError(null);
          }} />;
      })}
      {bound.some(([, ref]) => options.some((option) => matchesSource(option, ref) && option.uncertainty)) &&
        <label>Uncertainty propagation method
          <select aria-label="Uncertainty propagation method" value={uncertaintyMethod}
            onChange={(event) => setUncertaintyMethod(event.target.value as "auto" | "gum" | "monte_carlo")}>
            <option value="auto">Automatic · first-order GUM or Monte Carlo for strong nonlinearity</option>
            <option value="gum">First-order GUM</option>
            <option value="monte_carlo">Monte Carlo · JCGM 101 (Gaussian input model)</option>
          </select>
        </label>}
      {conflict && <div className="science-conflict">
        <p role="alert">The saved run changed while you were editing. Your inputs are still here.</p>
        <div className="science-actions">
          <button type="button" onClick={() => { setDraft({ ...draft, revision }); setError(null); }}>Keep my inputs</button>
          <button type="button" onClick={() => { setDraft(restoreDraft(op, result, revision, options, configured)); setError(null); }}>
            {result ? "Load saved inputs" : "Reset to example"}
          </button>
        </div>
      </div>}
      <div className="science-actions">
        <button className="science-run" type="submit" disabled={busy || conflict || stale}>Run calculation</button>
        <button type="button" disabled={busy || conflict || stale} onClick={() => void saveForRunner()}>
          Save for Graph Runner
        </button>
        <button type="button" onClick={() => {
          setDraft({ ...restoreDraft(op, null, revision), dirty: true }); setError(null);
        }}>Load example</button>
      </div>
      {configured?.operationId === op.id && <small role="status">Saved as an explicit Graph Runner step.</small>}
    </fieldset>
    {busy && <p role="status">{running ? "Running calculation…" : configuring ? "Saving Graph Runner configuration…" : "Loading selected table column…"}</p>}
    {/* Outside the disabled fieldset; only real calculations can be cancelled, not table-column imports. */}
    {running && <div className="science-actions">
      <button type="button" className="science-cancel" disabled={cancelling} onClick={async () => {
        setCancelling(true);
        try { await cancel(op.id); }
        catch (e) { setError({ message: e instanceof Error ? e.message : "Unable to cancel the calculation." }); }
        finally { setCancelling(false); }
      }}>{cancelling ? "Stopping…" : "Cancel calculation"}</button>
    </div>}
    {error && <p id={errorId} role="alert">{error.message}</p>}
  </form>;
}

function Workbench({ session, state, options, run, configure, cancel, fetchTableColumn, exportJson, exportCsv, charts, onChart, onRefresh, tableEditor }: {
  session: ScienceEditorSession; state: ScienceState; options: SourceOption[]; run: Run; configure: Run; cancel: Cancel; fetchTableColumn: FetchTableColumn;
  exportJson: () => Promise<string>; exportCsv: () => Promise<Blob>; tableEditor: ReactNode;
  charts: ScienceChartLink[]; onChart: (type: ScienceChartType) => void; onRefresh: (chart: ScienceChartLink) => void;
}) {
  const [selected, setSelected] = useState(session.selected ?? state.runner?.operationId ?? state.latest?.operationId ?? state.operations[0].id);
  const [busy, setBusy] = useState(false);
  const op = state.operations.find((candidate) => candidate.id === selected) ?? state.operations[0];
  const result = state.latest?.operationId === op.id ? state.latest : null;
  const revision = (state.runToken ?? JSON.stringify(state.latest?.provenance ?? null)) + "|" + JSON.stringify(state.runner ?? null);
  return <>
    <label>Operation<select value={op.id} disabled={busy} onChange={(e) => { session.selectOperation(e.target.value); setSelected(e.target.value); }}>
      {state.operations.map((operation) => <option key={operation.id} value={operation.id}>{operation.title}</option>)}
    </select></label>
    <OperationEditor key={op.id} session={session} op={op} result={result} configured={state.runner}
      revision={revision} runToken={state.runToken} limits={state.limits}
      options={options} run={run} configure={configure} cancel={cancel} fetchTableColumn={fetchTableColumn}
      busy={busy} setBusy={setBusy} />
    {tableEditor}
    {state.latest && <Results key={revision} result={state.latest} exportJson={exportJson} exportCsv={exportCsv}
      charts={charts} onChart={onChart} onRefresh={onRefresh} title={
      state.operations.find((candidate) => candidate.id === state.latest?.operationId)?.title ?? state.latest.operationId
    } />}
  </>;
}

export const ScientificNodeView = memo(function ScientificNodeView({ data, id, selected }: NodeProps<ScientificFlowNode>) {
  const state = useMemo(() => readScienceState(data.raw), [data.raw]);
  const options = useMemo(() => sourceOptions(data.parents, state?.limits?.points), [data.parents, state?.limits?.points]);
  const collapsed = useLodVisibility();
  return <NodeShell kindClassName="scientific-node" selected={!!selected} collapsed={collapsed}
    header={<NodeHeading glyph="chart">{data.title}</NodeHeading>} bodyClassName="science-body nodrag nopan nowheel">
    {!data.raw ? <p role="status">Enable {data.title} in Settings → Plugins to open this workbench.</p>
      : state?.error ? <p role="alert">{state.error}</p> : state && data.pluginId === "science_" + state.packId
      ? <Workbench key={state.nodeKey ?? id} session={data.store.scientificEditor(id, state.nodeKey)} state={state} options={options}
        exportJson={() => data.store.exportScientificResult(data.pluginId, id, state.runToken)}
        exportCsv={() => data.store.exportScientificTableCsv(data.pluginId, id, "result", state.runToken)}
        tableEditor={<TableEditor state={state} nodeId={id} pluginId={data.pluginId} store={data.store} />}
        fetchTableColumn={async (option) => {
          if (!option.tableColumn) throw new Error("Choose a typed table column.");
          const { nodeId, column, policy, start, end } = option.tableColumn;
          const answer = await tableIntent(data.store, data.pluginId, "table_column", {
            node_id: id, source_node_id: nodeId, column, policy, start, end,
            table_source: option.tableColumn.source ?? "import",
            expected_hash: option.reference.resultHash, expected_receipt: option.reference.receiptHash,
          });
          if (!Array.isArray(answer.values) || !answer.values.every((n) => typeof n === "number" && Number.isFinite(n))
              || typeof answer.reference !== "object" || answer.reference === null)
            throw new Error("The table column response was not valid.");
          return { values: answer.values as number[], reference: answer.reference as SourceReference };
        }}
        charts={data.charts ?? []}
        onChart={(chartType) => data.store.generateChart(id, chartType)}
        onRefresh={(chart) => data.store.generateChart(chart.id, chart.chartType)}
        run={(operationId, inputs, sources, token, method) => data.store.runScientificOperation(data.pluginId, id, operationId, inputs, sources, token, method)}
        configure={(operationId, inputs, sources, token) => data.store.configureScientificRunner(data.pluginId, id, operationId, inputs, sources, token)}
        cancel={(operationId) => data.store.cancelScientificOperation(data.pluginId, id, operationId)} />
      : <p role="alert">This workbench version could not be loaded. Check the plugin in Settings.</p>}
  </NodeShell>;
});
