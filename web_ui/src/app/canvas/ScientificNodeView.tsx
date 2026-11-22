import { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { OverlayProviderIfMissing, useOverlays } from "../overlays/overlays";
import { scientificFocusName } from "./scientificFocus";
import { ScientificWorkspace, useScientificSlots, type ScienceEditorProps } from "./ScientificWorkspace";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import type { SceneStore, ScienceEditorSession } from "./sceneStore";
import { downloadBlob } from "./downloadTextFile";
import { writeTableCsv } from "./tableCsv";
import { createLinearScale } from "./charts/chartScales";
import { ScienceTableSource } from "./ScienceTableSource";
import { ScienceArrayInput } from "./ScienceArrayInput";
import { ScienceColumnBinding, type FetchTableColumn } from "./ScienceColumnBinding";
import { ScientificResultViews } from "./ScientificResultViews";
import {
  isImportedTable, type ScientificImportedTable, chartableScienceTypes, columnChoices, compatibleOptions, connectedInputText, currentSource, draftInputs, FieldError, fieldText, formatMeasurement, formatScienceNumber, formatScienceRange, formatScienceSummary, formatScienceValue, matchesSource, parseInputs, readableScienceKey, readScienceState, resultMeasurement, sourceOptions,
  type EditorDraft, type ScienceAttempt, type ScienceField, type ScienceLimits, type ScienceOperation, type ScienceParent, type SciencePlot, type ScienceResult,
  type ScienceChartLink, type ScienceChartType, type ScienceState, type ScienceRunnerConfiguration, type ScientificColumnType, type Sources, type SourceOption, type SourceReference,
} from "./scientificWorkbench";
import "./ScientificNodeView.css";

export type ScientificFlowNode = Node<{
  title: string; raw: string; pluginId: string; store: SceneStore; parents: ScienceParent[]; charts?: ScienceChartLink[];
}, "scientific">;

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
    <span>{plot.yLabel} · {formatScienceRange(y.low, y.high)}</span>
    <svg viewBox="0 0 320 110" role="img" aria-label={`${plot.title}. ${plot.yLabel} versus ${plot.xLabel}, ${plot.x.length} points.`}>
      <path d="M10 8V98H310" fill="none" stroke="currentColor" opacity=".4" />
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.8" />
      {plot.x.length <= 48 && plot.x.map((v, i) =>
        <circle key={i} cx={x.at(v)} cy={y.at(plot.y[i])} r="2.4" fill="currentColor"><title>{v}, {plot.y[i]}</title></circle>)}
    </svg>
    <span>{plot.xLabel} · {formatScienceRange(x.low, x.high)}</span>
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

/** The operation's declared headline wins; otherwise favor an actual result over
 * bookkeeping fields. Never put vectors or objects into the headline of a 390px
 * canvas node. The complete values remain below. */
function headlineScienceValue(result: ScienceResult, declared?: string): [string, unknown] | undefined {
  const scalars = Object.entries(result.values).filter(([, value]) =>
    (typeof value === "number" && Number.isFinite(value)) ||
    (typeof value === "string" && value.length > 0 && value.length <= 80) ||
    typeof value === "boolean");
  const preferred = declared ? scalars.find(([name]) => name === declared) : undefined;
  if (preferred) return preferred;
  const numeric = scalars.filter(([, value]) => typeof value === "number");
  const choices = numeric.length ? numeric : scalars;
  const rank = (name: string) =>
    /^(?:estimate|mean|median|result|value|distance|total)(?=$|[_ (])/i.test(name) ? 0 :
    /^(?:n|count|sample_count|iterations|steps|coverage_|standard_error|standard_uncertainty)/i.test(name) ? 2 : 1;
  return choices.reduce<[string, unknown] | undefined>((best, entry) =>
    !best || rank(entry[0]) < rank(best[0]) ? entry : best, undefined);
}

/** Closed disclosures do not mount wide plots, tables or thousands of cells. */
function ResultDisclosure({ label, children, className = "", onClose, defaultOpen = false }: {
  label: string; children: ReactNode; className?: string; onClose?: () => void; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return <details className={`science-result-disclosure ${className}`.trim()} open={open}>
    <summary onClick={(event) => { event.preventDefault(); if (open) onClose?.(); setOpen((previous) => !previous); }}>{label}</summary>
    {open && children}
  </details>;
}

function Results({ result, title, headlineKey, exportJson, exportCsv, exportMethods, onChart, onRefresh, onDiscuss, charts }: {
  result: ScienceResult; title: string; headlineKey?: string; exportJson: () => Promise<string>; exportCsv: () => Promise<Blob>;
  exportMethods: () => Promise<string>;
  onChart: (type: ScienceChartType) => void; onRefresh: (chart: ScienceChartLink) => void;
  onDiscuss: (result: ScienceResult) => void; charts: ScienceChartLink[];
}) {
  const [exporting, setExporting] = useState(false);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const { table, tableAsset, plot, provenance } = result;
  const chartTypes = chartableScienceTypes(result);
  const [chartType, setChartType] = useState<ScienceChartType>(chartTypes[0] ?? "scatter");
  const baseName = `${provenance.packId}-${result.operationId}`;
  const csv = table ?? (plot ? { columns: [plot.xLabel, plot.yLabel], rows: plot.x.map((v, i) => [v, plot.y[i]]) } : { columns: ["Metric", "Value"], rows: Object.entries(result.values) });
  const values = Object.entries(result.values);
  const headline = headlineScienceValue(result, headlineKey);
  const measurement = headline && resultMeasurement(result, headline[0]);
  const uncertaintyUnavailable = headline && result.uncertainty?.unsupportedOutputs?.[headline[0]];
  const formattedSummary = formatScienceSummary(result.summary);
  const longSummary = formattedSummary.length > 170;
  const tableRows = tableAsset?.rowCount ?? table?.rows.length ?? 0;
  return <section className="science-results" aria-label="Latest result">
    <div className="science-result-head">
      <h4>Last successful run · {title}</h4>
      {headline ? <div className="science-result-highlight">
        <span title={headline[0]}>{readableScienceKey(headline[0])}</span>
        <strong>{uncertaintyUnavailable ? `${formatScienceValue(headline[1])} · uncertainty unavailable`
          : measurement ? formatMeasurement(measurement) : formatScienceValue(headline[1])}</strong>
      </div> : (table || tableAsset) && <div className="science-result-highlight">
        <span>Result table</span>
        <strong>{tableRows.toLocaleString()} {tableRows === 1 ? "row" : "rows"}</strong>
      </div>}
      <p role="status" className={`science-result-summary${longSummary ? " science-result-summary--clamped" : ""}`}>
        {formattedSummary}
      </p>
    </div>
    <div className="science-result-sections">
      {longSummary && <ResultDisclosure label="Read full summary">
        <p>{formattedSummary}</p>
      </ResultDisclosure>}
      {values.length > 0 && <ResultDisclosure label={`All values · ${values.length}`}>
        <dl className="science-result-values">{values.map(([label, value]) => {
          const measured = resultMeasurement(result, label);
          const unpropagatable = result.uncertainty?.unsupportedOutputs?.[label];
          return <div key={label}><dt>{label}</dt><dd>{unpropagatable ? `${formatScienceValue(value)} · uncertainty unavailable` : measured ? formatMeasurement(measured) : formatScienceValue(value)}</dd></div>;
        })}</dl>
      </ResultDisclosure>}
      {result.uncertainty && <ResultDisclosure className="science-uncertainty-budget"
        label={`Propagated uncertainty budget · ${result.uncertainty.method === "gum" ? "first-order GUM" : "Monte Carlo"}`}>
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
      </ResultDisclosure>}
      {(!!result.views?.length || !!plot) && <ResultDisclosure label="Plots & views">
        {result.views && <ScientificResultViews views={result.views} />}
        {plot && !result.views?.some((view) => view.type === "survival_curve") && <Plot plot={plot} />}
      </ResultDisclosure>}
      {(table || tableAsset) && <ResultDisclosure label={`Tables · ${tableRows.toLocaleString()} ${tableRows === 1 ? "row" : "rows"}`}>
        <div className="science-result-table-content">
          {table && <div className="science-table">
      <table aria-label="Result table"><thead><tr>{table.columns.map((col, i) => <th key={i} scope="col">{col}</th>)}</tr></thead>
        <tbody>{table.rows.slice(0, PREVIEW_ROWS).map((row, i) =>
          <tr key={i}>{row.map((cell, j) => <td key={j}>{formatScienceValue(cell)}</td>)}</tr>)}</tbody>
      </table>
      {table.rows.length > PREVIEW_ROWS && <small>Showing {PREVIEW_ROWS} of {table.rows.length} rows. Export CSV for all rows.</small>}
          </div>}
          {tableAsset && <div className="science-table">
      <p role="status">Saved result table · {tableAsset.rowCount.toLocaleString()} rows · {tableAsset.columnCount} typed columns</p>
      <table aria-label="Generated result table preview">
        <thead><tr>{tableAsset.columns.slice(0, tableAsset.previewColumns).map((col, i) =>
          <th key={i} scope="col" title={col.type + ", " + col.missingCount + " missing"}>{col.label}<small>{col.type}{col.missingCount ? " · " + col.missingCount + " missing" : ""}</small></th>)}</tr></thead>
        <tbody>{tableAsset.preview.map((row, i) =>
          <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? "—" : formatScienceValue(cell)}</td>)}</tr>)}</tbody>
      </table>
      <small>Preview: {tableAsset.preview.length} of {tableAsset.rowCount.toLocaleString()} rows. Export CSV for the full table or connect this node to use its typed columns.</small>
          </div>}
        </div>
      </ResultDisclosure>}
      {provenance.inputUnits != null && <details className="science-result-disclosure"><summary>Units used</summary>
      <pre>{JSON.stringify(provenance.inputUnits, null, 2)}</pre>
    </details>}
      <details className="science-result-disclosure"><summary>Inputs and provenance</summary>
      <pre>{JSON.stringify({ inputs: result.inputs, provenance }, null, 2)}</pre>
    </details>
      <ResultDisclosure label="Actions & exports">
        <div className="science-actions">
      <button type="button" disabled={copying} onClick={async () => {
        setError("");
        setCopied(false);
        setCopying(true);
        try {
          const methods = await exportMethods();
          if (!navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable.");
          await navigator.clipboard.writeText(methods);
          setCopied(true);
        } catch (e) {
          setError(e instanceof Error ? e.message : "Could not copy methods.");
        } finally {
          setCopying(false);
        }
      }}>{copying ? "Copying…" : copied ? "Copied methods" : "Copy methods"}</button>
      <button type="button" onClick={() => onDiscuss(result)}>Discuss in chat</button>
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
        {/* Inline with Chart this; the select carries its own accessible name. */}
        <select aria-label="Chart type" value={chartTypes.includes(chartType) ? chartType : chartTypes[0]}
          onChange={(e) => setChartType(e.target.value as ScienceChartType)}>
          {chartTypes.map((type) =>
            <option key={type} value={type}>{type === "boxplot" ? "Box plot" : type === "line" ? "Fitted line with 95% confidence envelope" : type === "heatmap" ? "Heatmap" : "Scatter"}</option>)}
        </select>
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
      </ResultDisclosure>
    </div>
  </section>;
}

function FieldEditor({ field, value, onEdit, options, onImport, imported, errorId, stale, chosenUnit, unitSetter, columnOptions, limits }: {
  field: ScienceField; value: string; onEdit: (value: string) => void;
  options: SourceOption[]; onImport: (option: SourceOption) => void; imported?: string; errorId?: string; stale: boolean;
  columnOptions: Array<{ index: number; label: string; type: ScientificColumnType }>;
  /** The unit typed into this field's unit-setting sibling (field.unit_field) and that sibling's label. */
  chosenUnit?: string; unitSetter?: string; limits?: ScienceLimits;
}) {
  const [helpOpen, setHelpOpen] = useState(false);
  const unitOverridden = !!chosenUnit && chosenUnit !== (field.default_unit || field.unit || "1");
  const dimensioned = !["text", "choice", "table", "column"].includes(field.kind);
  const unit = field.column_units?.length ? field.unit || "mixed" : chosenUnit || field.default_unit || field.unit || "1";
  const compact = (field.kind === "number" || field.kind === "integer") && !options.length && !imported;
  const id = useId();
  const description = [helpOpen && id + "-help", imported && id + "-source", errorId].filter(Boolean).join(" ");
  const common = { id, name: field.key, value, "aria-invalid": errorId || stale ? true as const : undefined,
    "aria-describedby": description || undefined };
  return <div className={"science-field" + (compact ? " science-field--compact" : "")}>
    <div className="science-field-heading">
      {/* A bare dimensionless unit ("1") reads like a count or footnote in a
          label ("Observations (1)"); the help still states it. */}
      <label htmlFor={id}>{field.label}{dimensioned && unit !== "1" && <> <span className="science-field-unit">({unit})</span></>}</label>
      {(field.description || dimensioned) && <button type="button" className="science-help-toggle"
        aria-label={"Input and unit help for " + field.label} aria-expanded={helpOpen}
        aria-controls={helpOpen ? id + "-help" : undefined} onClick={() => setHelpOpen(!helpOpen)}>?</button>}
    </div>
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
      ? <input {...common} type="text" value={value ? "Connected typed table selected" : options.length ? "Select a connected typed table below" : "Connect a source table to choose columns"} readOnly />
      : field.kind === "number" || field.kind === "integer"
      ? <input {...common} type="text" inputMode="text"
        onChange={(e) => onEdit(e.target.value)} />
      : <textarea {...common} rows={field.kind === "matrix" || field.kind === "text" ? 3 : 2}
        onChange={(e) => onEdit(e.target.value)} spellCheck={field.kind === "text"} />}
    {(field.kind === "vector" || field.kind === "matrix") && <ScienceArrayInput
      field={field} limits={limits} value={value} onEdit={onEdit} />}
    {helpOpen && <div id={id + "-help"} className="science-field-help">
      {field.description && <p>{field.description}</p>}
      {dimensioned && unitOverridden && <p>Unit: {chosenUnit}, set by {unitSetter ?? field.unit_field}.
        {field.kind === "vector" || field.kind === "matrix"
          ? ' Use JSON arrays; quote values with unit suffixes, e.g. ["25 mm", "2 cm"].'
          : " You may append a compatible unit, e.g. 25 mm."}</p>}
      {dimensioned && !unitOverridden && <p>
        Default unit: {field.column_units?.length
          ? field.column_units.map((columnUnit, i) => `column ${i + 1}: ${columnUnit}`).join("; ")
          : field.default_unit || field.unit || "1"} · {field.dimension || "dimensionless"}.
        {field.kind === "vector" || field.kind === "matrix"
          ? ' Use JSON arrays; quote values with unit suffixes, e.g. ["25 mm", "2 cm"].'
          : " You may append a compatible unit, e.g. 25 mm."}
      </p>}
    </div>}
    {options.length > 0 && <select aria-label={`Import ${field.label} from a connected result`} value=""
      onChange={(e) => { if (e.target.value) onImport(options[Number(e.target.value)]); }}>
      <option value="">Use connected result…</option>
      {options.map((option, i) => <option key={i} value={i}>{option.label}</option>)}
    </select>}
    {imported && <small id={id + "-source"}>Source · {imported}</small>}
  </div>;
}

function binaryBase64(bytes: Uint8Array): string {
  const chunks: string[] = [];
  for (let i = 0; i < bytes.length; i += 32768) {
    chunks.push(String.fromCharCode(...bytes.subarray(i, i + 32768)));
  }
  return btoa(chunks.join(""));
}

function TableEditor({ state, nodeId, pluginId, store, defaultOpen = false }: {
  state: ScienceState; nodeId: string; pluginId: string; store: SceneStore; defaultOpen?: boolean;
}) {
  const [source, setSource] = useState<"paste" | "file">("paste");
  const [format, setFormat] = useState<"csv" | "tsv">("csv");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("Name,Temperature (degC),Observed\nA,22.5,true\nB,NA,false");
  const [missing, setMissing] = useState("NA,N/A,null");
  const [overrides, setOverrides] = useState("{}");
  const columnTypes = useMemo((): Record<string, string> | undefined => {
    try {
      const parsed: unknown = JSON.parse(overrides);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && Object.values(parsed).every(
        value => ["numeric", "text", "categorical", "datetime", "boolean"].includes(value))) {
        return parsed as Record<string, string>;
      }
    } catch { /* Incomplete JSON stays editable until the next preview. */ }
    return undefined;
  }, [overrides]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const pending = useRef(false);
  const [schema, setSchema] = useState<ScientificImportedTable | null>(null);
  const [review, setReview] = useState<{ table: ScientificImportedTable; runToken: string;
    args: Record<string, unknown>; previousToken: string | undefined } | null>(null);
  const validReview = review?.previousToken === state.runToken ? review : null;
  function invalidate(clearSchema = false) {
    generation.current++;
    setReview(null);
    setError("");
    if (clearSchema) setSchema(null);
  }
  const [exporting, setExporting] = useState(false);
  const table = state.table;
  const rowsLimit = state.limits?.tableRows ?? 100000;
  const cellsLimit = state.limits?.tableCells ?? 1000000;
  const fileLimit = state.limits?.tableSourceBytes ?? 64 * 1024 * 1024;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    pending.current = true;
    const version = generation.current;
    setError("");
    setBusy(true);
    try {
      if (validReview) {
        await store.scientificTableIntent(pluginId, "import_table", { ...validReview.args,
          expected_run: validReview.runToken, expected_preview: validReview.table.provenance.receiptHash });
        if (generation.current === version) invalidate(true);
        return;
      }
      if (!columnTypes)
        throw new Error("Column types must map headers to numeric, text, categorical, datetime or boolean.");
      const args: Record<string, unknown> = {
        node_id: nodeId, format, missing_values: ["", ...missing.split(",").map((v) => v.trim()).filter(Boolean)],
        column_types: columnTypes, ...(state.runToken && { expected_run: state.runToken }),
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
      if (generation.current !== version) return;
      const response = await store.scientificTableIntent(pluginId, "preview_table", args);
      if (generation.current !== version) return;
      if (!isImportedTable(response.table) || typeof response.runToken !== "string" || !response.runToken)
        throw new Error("The table preview could not be verified. Preview the source again.");
      setSchema(response.table);
      setReview({ table: response.table, runToken: response.runToken, args, previousToken: state.runToken });
    } catch (e) {
      if (generation.current === version) {
        setReview(null);
        setError(e instanceof Error ? e.message : "Could not import the table.");
      }
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  // The importer is rarely needed and tall, so it stays collapsed like the
  // result sections; an imported table shows one status line by default.
  return <section className="science-data-table" aria-label="Typed data table">
    {table && <p role="status">{table.sourceName} · {table.rowCount.toLocaleString()} rows · {table.columnCount} columns</p>}
    <ResultDisclosure defaultOpen={defaultOpen} label={table ? "Replace data table" : "Import a data table"} onClose={() => { invalidate(true); setFile(null); }}>
    <p>Import mixed data and missing values. Tables support {rowsLimit.toLocaleString()} rows and {cellsLimit.toLocaleString()} cells, stored outside the graph.</p>
    <form onSubmit={submit} onChange={() => invalidate()}>
      <fieldset disabled={busy}>
        <label>Data source
          <select aria-label="Table data source" value={source} onChange={(e) => { invalidate(true); setSource(e.target.value as "paste" | "file"); setFile(null); }}>
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
                invalidate(true); setFile(next);
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
        {schema && <div className="science-table">
          <table aria-label="Review column types"><thead><tr>
            <th scope="col">Column</th><th scope="col">Type</th><th scope="col">Unit</th><th scope="col">Missing</th>
          </tr></thead><tbody>{schema.columns.map((col) => <tr key={col.label}>
            <th scope="row">{col.label}</th><td><select aria-label={`Type for ${col.label}`}
              value={columnTypes?.[col.label] ?? ""} disabled={!columnTypes}
              onChange={(e) => {
                const next = { ...columnTypes };
                if (e.target.value) next[col.label] = e.target.value;
                else delete next[col.label];
                setOverrides(JSON.stringify(next));
              }}>
              <option value="">Infer from data</option>
              {["numeric", "text", "categorical", "datetime", "boolean"].map((kind) => <option key={kind}>{kind}</option>)}
            </select>{validReview && <small>{col.type}</small>}</td>
            <td>{col.unit || "—"}</td><td>{col.missingCount}</td>
          </tr>)}</tbody></table>
        </div>}
        <details><summary>Advanced column overrides</summary>
          <label>Column type overrides (JSON)
            <textarea rows={2} aria-label="Column type overrides" value={overrides} onChange={(e) => setOverrides(e.target.value)} />
          </label>
        </details>
        {validReview && <div className="science-table">
          <p role="status">{validReview.table.sourceName} · {validReview.table.rowCount} rows · {validReview.table.columnCount} columns.
            Showing the first {validReview.table.preview.length} rows and {validReview.table.previewColumns} columns.</p>
          <table aria-label="Pending import preview"><thead><tr>
            {validReview.table.columns.slice(0, validReview.table.previewColumns).map((col) =>
              <th scope="col" key={col.label}>{col.label}</th>)}
          </tr></thead><tbody>{validReview.table.preview.map((row, i) =>
            <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? "Missing" : formatScienceValue(cell)}</td>)}</tr>)}
          </tbody></table>
        </div>}
        <button type="submit" className="science-run">{validReview
          ? (busy ? "Applying…" : "Apply reviewed table") : (busy ? "Previewing…" : "Preview table")}</button>
      </fieldset>
    </form>
    </ResultDisclosure>
    {error && <p role="alert">{error}</p>}
    {table && <ResultDisclosure label="Table preview and export"><div className="science-imported">
      <div className="science-table">
        <table aria-label="Imported typed table preview">
          <thead><tr>{table.columns.slice(0, table.previewColumns).map((col, i) =>
            <th scope="col" key={i} title={col.type + ", " + col.missingCount + " missing"}>{col.label}<small>{col.type}{col.missingCount ? " · " + col.missingCount + " missing" : ""}</small></th>)}</tr></thead>
          <tbody>{table.preview.map((row, i) => <tr key={i}>{row.map((cell, j) =>
            <td key={j}>{cell === null ? "—" : formatScienceValue(cell)}</td>)}</tr>)}</tbody>
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
    </div></ResultDisclosure>}
  </section>;
}

type Run = (operationId: string, inputs: Record<string, unknown>, sources: Sources, expectedRun?: string,
  method?: "auto" | "gum" | "monte_carlo", attemptId?: string) => Promise<void>;
type Cancel = (operationId: string, attemptId?: string) => Promise<void>;
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
  const method = runner ? runner.uncertaintyMethod ?? "auto" : result?.provenance.requestedUncertaintyMethod ?? "auto";
  return { values, sources, method, revision, dirty: false };
}

function AttemptProgress({ attempt, title }: { attempt: ScienceAttempt; title: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <p><span role="status">{attempt.status === "cancelling" ? "Stopping" : "Running"} {title}…</span>{" "}
    <span aria-label="Elapsed time">{Math.max(0, Math.floor((now - attempt.startedAtMs) / 1000))} s</span></p>;
}

function OperationEditor({ session, op, result, configured, revision, runToken, limits, options, run, configure, cancel, fetchTableColumn, busy, setBusy, attemptProtocol, attempt, attemptTitle }: {
  session: ScienceEditorSession; op: ScienceOperation; result: ScienceResult | null;
  configured?: ScienceRunnerConfiguration | null; revision: string; runToken?: string; limits?: ScienceLimits;
  options: SourceOption[]; run: Run; configure: Run; cancel: Cancel; fetchTableColumn: FetchTableColumn;
  busy: boolean; setBusy: (busy: boolean) => void; attemptProtocol?: 1; attempt?: ScienceAttempt; attemptTitle?: string;
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
  const uncertaintyMethod = draft.method ?? "auto";
  const savedInputs = useMemo(() => configured?.operationId === op.id
    ? restoreDraft(op, null, revision, options, configured) : null, [op, configured, revision, options]);
  const savedMatches = !!savedInputs && uncertaintyMethod === savedInputs.method &&
    op.fields.every(({ key }) => draft.values[key] === savedInputs.values[key] &&
      JSON.stringify(draft.sources[key]) === JSON.stringify(savedInputs.sources[key]));
  const formRef = useRef<HTMLFormElement>(null);
  const errorId = useId();
  const activeAttempt = attempt?.status === "running" || attempt?.status === "cancelling" ? attempt : undefined;
  const submission = useSyncExternalStore(session.onSubmission, session.submission);
  const completedOwnDraft = attempt?.status === "succeeded" && attempt.attemptId === submission?.id
    && submission.draft === draft && result != null
    && attempt.receiptHash === (result.provenance.receiptHash ?? result.provenance.resultHash);
  const changed = draft.revision !== revision;
  if (completedOwnDraft || (changed && !draft.dirty)) {
    setDraft(restoreDraft(op, result, revision, options, configured));
    setError(null);
  }
  const conflict = changed && draft.dirty;
  const bound = Object.entries(draft.sources);
  const stale = bound.some(([, ref]) => !currentSource(options, ref));
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
      const attemptId = attemptProtocol ? crypto.randomUUID().replaceAll("-", "") : undefined;
      if (attemptId) session.beginSubmission(attemptId, op.id, draft);
      await run(op.id, inputs, draft.sources, runToken, uncertaintyMethod, attemptId);
      if (session.drafts.get(op.id) === draft) session.drafts.delete(op.id);
      setDraft((old) => old === draft ? { ...old, dirty: false } : old);
    } catch (e) { setError({ message: e instanceof Error ? e.message : "The calculation failed." }); }
    finally { pending.current = false; setRunning(false); setBusy(false); }
  }

  async function saveForRunner() {
    if (pending.current || conflict || stale) return;
    setError(null);
    let inputs: Record<string, unknown>;
    try { inputs = parseInputs(op, draft.values, limits); }
    catch (e) {
      const field = e instanceof FieldError ? e.key : undefined;
      setError({ message: e instanceof Error ? e.message : "Check the inputs.", field });
      if (field) (formRef.current?.elements.namedItem(field) as HTMLElement | null)?.focus();
      return;
    }
    pending.current = true;
    setConfiguring(true);
    setBusy(true);
    try {
      await configure(op.id, inputs, draft.sources, runToken, uncertaintyMethod);
      setDraft((old) => ({ ...old, dirty: false }));
    } catch (e) { setError({ message: e instanceof Error ? e.message : "Could not save the runner configuration." }); }
    finally { pending.current = false; setConfiguring(false); setBusy(false); }
  }

  return <form ref={formRef} className="science-operation-editor" onSubmit={submit}>
    <div className="science-operation-context">
      {op.description && <p className="science-operation-preview" title={op.description}>{op.description}</p>}
      {(op.description || op.methods || op.citations?.length) && <details className="science-methods">
        <summary>About &amp; methods</summary>
        <div className="science-methods-content">
          {op.description && <p>{op.description}</p>}
          {op.methods && <p>{op.methods}</p>}
          {!!op.citations?.length && <ul>
            {op.citations.map((citation) => <li key={citation.url + citation.label}>
              <a href={citation.url} target="_blank" rel="noopener noreferrer">{citation.label}</a>
            </li>)}
          </ul>}
        </div>
      </details>}
    </div>
    <fieldset disabled={busy}>
      <div className="science-inputs" role="group" aria-label="Inputs">
        <div className="science-inputs-heading">
          <strong>Inputs</strong><small>{op.fields.length} {op.fields.length === 1 ? "field" : "fields"}</small>
        </div>
        <div className="science-input-grid">
          {op.fields.map((field) => {
            const available = compatibleOptions(field, options, limits);
            const source = draft.sources[field.key];
            const previous = source && currentSource(available, source);
            const chosenUnit = field.unit_field ? draft.values[field.unit_field]?.trim() : undefined;
            const unitSetter = op.fields.find((other) => other.key === field.unit_field)?.label;
            return <FieldEditor key={field.key} field={field} value={draft.values[field.key] ?? ""} options={available}
              columnOptions={columnChoices(field, draft.values)} limits={limits}
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
                  const unitInputs = Object.fromEntries(op.fields.filter((item) => item.kind === "text" && (
                    item.key.endsWith("_unit") || item.key === "from_scale")).map((item) => [item.key, draft.values[item.key]]));
                  void fetchTableColumn(option, op, field, unitInputs).then(({ values, reference }) => {
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
        </div>
      </div>
      <ScienceColumnBinding options={options} op={op} values={draft.values} maxPoints={limits?.points ?? 1024}
        fetchColumn={fetchTableColumn} apply={(columns) => {
          setDraft((old) => {
            const values = { ...old.values }, sources = { ...old.sources };
            for (const field of op.fields) {
              const column = columns[field.key];
              if (column) { values[field.key] = fieldText(field, column.values); sources[field.key] = column.reference; }
            }
            return { ...old, dirty: true, values, sources };
          });
          setError(null);
        }} />
      {bound.some(([, ref]) => options.some((option) => matchesSource(option, ref) && option.uncertainty)) &&
        <label>Uncertainty propagation method
          <select aria-label="Uncertainty propagation method" value={uncertaintyMethod}
            onChange={(event) => {
              const method = event.target.value as "auto" | "gum" | "monte_carlo";
              setDraft((old) => ({ ...old, method, dirty: true }));
            }}>
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
          const example = { ...restoreDraft(op, null, revision), dirty: true };
          if (session.replacedExamples.get(op.id)?.example !== draft) session.replacedExamples.set(op.id, { before: draft, example });
          else return;
          setDraft(example); setError(null);
        }}>Load example</button>
        {session.replacedExamples.get(op.id)?.example === draft && <button type="button" onClick={() => {
          const previous = session.replacedExamples.get(op.id);
          if (previous) setDraft(previous.before);
          session.replacedExamples.delete(op.id); setError(null);
        }}>Undo load example</button>}
      </div>
      {configured && <small role="status">{savedMatches ? "Saved as an explicit Graph Runner step."
        : "Changes not saved to Graph Runner."}</small>}
    </fieldset>
    {submission?.phase === "checking" ? <p role="status">Checking run status…</p>
      : activeAttempt ? <AttemptProgress attempt={activeAttempt} title={attemptTitle ?? activeAttempt.operationId} />
      : busy && <p role="status">{running || submission?.phase === "submitting" ? "Running calculation…" : configuring ? "Saving Graph Runner configuration…" : "Loading selected table column…"}</p>}
    {/* Outside the disabled fieldset; only real calculations can be cancelled, not table-column imports. */}
    {attemptProtocol && (running || activeAttempt || (submission && submission.phase !== "finished")) && <div className="science-actions">
      <button type="button" className="science-cancel" disabled={cancelling || activeAttempt?.status === "cancelling"} onClick={async () => {
        setCancelling(true);
        try { await cancel(activeAttempt?.operationId ?? op.id, activeAttempt?.attemptId ?? (attemptProtocol ? submission?.id : undefined)); }
        catch (e) { setError({ message: e instanceof Error ? e.message : "Unable to cancel the calculation." }); }
        finally { setCancelling(false); }
      }}>{cancelling || activeAttempt?.status === "cancelling" ? "Stopping…" : "Cancel calculation"}</button>
    </div>}
    {error ? <p id={errorId} role="alert">{error.message}</p>
      : (attempt?.status === "cancelled" || attempt?.status === "failed") && <p role="status">{attempt.error}</p>}
  </form>;
}

function Workbench({ requestedOperation, session, state, options, run, configure, cancel, fetchTableColumn, exportJson, exportCsv, exportMethods, charts, onChart, onRefresh, onDiscuss, tableEditor, tableSource }: {
  requestedOperation?: string; session: ScienceEditorSession; state: ScienceState; options: SourceOption[]; run: Run; configure: Run; cancel: Cancel; fetchTableColumn: FetchTableColumn;
  exportJson: () => Promise<string>; exportCsv: () => Promise<Blob>; exportMethods: () => Promise<string>; tableEditor: ReactNode; tableSource: ReactNode;
  charts: ScienceChartLink[]; onChart: (type: ScienceChartType) => void; onRefresh: (chart: ScienceChartLink) => void;
  onDiscuss: (result: ScienceResult) => void;
}) {
  const [selected, setSelected] = useState(session.selected ?? state.runner?.operationId ?? state.latest?.operationId ?? state.operations[0].id);
  const launchSelection = state.operations.find(candidate => candidate.id === requestedOperation)?.id;
  const [lastLaunch, setLastLaunch] = useState<string>();
  if (lastLaunch !== launchSelection) {
    setLastLaunch(launchSelection);
    if (launchSelection) setSelected(launchSelection);
  }
  useLayoutEffect(() => {
    if (launchSelection) session.selectOperation(launchSelection);
  }, [launchSelection, session]);
  const [localBusy, setBusy] = useState(false);
  const submission = useSyncExternalStore(session.onSubmission, session.submission);
  const busy = localBusy || (submission !== undefined && submission.phase !== "finished")
    || state.attempt?.status === "running" || state.attempt?.status === "cancelling";
  const operationId = useId();
  const op = state.operations.find((candidate) => candidate.id === selected) ?? state.operations[0];
  const result = state.latest?.operationId === op.id ? state.latest : null;
  const latestOperation = state.operations.find((candidate) => candidate.id === state.latest?.operationId);
  const revision = (state.runToken ?? JSON.stringify(state.latest?.provenance ?? null)) + "|" + JSON.stringify(state.runner ?? null);
  return <>
    <div className="science-operation-picker">
      <label htmlFor={operationId}>Operation</label>
      <select id={operationId} value={op.id} disabled={busy} onChange={(e) => { session.selectOperation(e.target.value); setSelected(e.target.value); }}>
        {state.operations.map((operation) => <option key={operation.id} value={operation.id}>{operation.title}</option>)}
      </select>
    </div>
    {op.fields.some((field) => field.kind === "table") && !options.some((item) =>
      ["typed_table", "result_table"].includes(String(item.reference.selector[0]))) && tableSource}
    <OperationEditor key={op.id} session={session} op={op} result={result} configured={state.runner}
      revision={revision} runToken={state.runToken} limits={state.limits}
      options={options} run={run} configure={configure} cancel={cancel} fetchTableColumn={fetchTableColumn}
      busy={busy} setBusy={setBusy} attemptProtocol={state.attemptProtocol} attempt={state.attempt}
      attemptTitle={state.operations.find((item) => item.id === state.attempt?.operationId)?.title} />
    {tableEditor}
    {state.latest && <Results key={revision} result={state.latest} exportJson={exportJson} exportCsv={exportCsv} exportMethods={exportMethods}
      charts={charts} onChart={onChart} onRefresh={onRefresh} onDiscuss={onDiscuss}
      title={latestOperation?.title ?? state.latest.operationId} headlineKey={latestOperation?.headline} />}
  </>;
}

export function ScientificEditor({ data, id, requestedOperation }: ScienceEditorProps) {
  const overlays = useOverlays();
  const state = useMemo(() => readScienceState(data.raw), [data.raw]);
  const options = useMemo(() => sourceOptions(data.parents, state?.limits?.points), [data.parents, state?.limits?.points]);
  const openSettings = () => {
    data.store.openModelTaskSettings("plugins");
    overlays.open("settings", "dialog");
  };
  return <>{!data.raw ? <><p role="status">Enable {data.title} in Settings → Plugins to open this workbench.</p>
    <button type="button" onClick={openSettings}>Open plugin settings</button></>
      : state?.error ? <p role="alert">{state.error}</p> : state && data.pluginId === "science_" + state.packId
      ? <Workbench requestedOperation={requestedOperation} key={state.nodeKey ?? id} session={data.store.scientificEditor(id, state.nodeKey)} state={state} options={options}
        exportJson={() => data.store.exportScientificResult(data.pluginId, id, state.runToken)}
        exportMethods={() => data.store.exportScientificMethods(data.pluginId, id, state.runToken)}
        exportCsv={() => data.store.exportScientificTableCsv(data.pluginId, id, "result", state.runToken)}
        tableSource={<ScienceTableSource targetId={id} packTitle={state.packTitle ?? data.title} store={data.store}
          renderImporter={(source) => <TableEditor key={source.id} defaultOpen state={source.state}
            nodeId={source.id} pluginId={source.pluginId} store={data.store} />} />}
        tableEditor={<TableEditor state={state} nodeId={id} pluginId={data.pluginId} store={data.store} />}
        fetchTableColumn={async (option, operation, field, unitInputs, selection) => {
          if (!option.tableColumn) throw new Error("Choose a typed table column.");
          const { nodeId, column, policy, start, end } = option.tableColumn;
          const answer = await data.store.scientificTableIntent(data.pluginId, "table_column", {
            node_id: id, source_node_id: nodeId, column, policy, start, end,
            table_source: option.tableColumn.source ?? "import",
            operation_id: operation.id, field_key: field.key, unit_inputs: unitInputs,
            ...(selection?.completeColumns && { complete_columns: selection.completeColumns }),
            ...(selection?.assumedUnit && { assumed_unit: selection.assumedUnit }),
            expected_hash: option.reference.resultHash, expected_receipt: option.reference.receiptHash,
          });
          if (!Array.isArray(answer.values) || !answer.values.every((n) => typeof n === "number" && Number.isFinite(n))
              || typeof answer.reference !== "object" || answer.reference === null)
            throw new Error("The table column response was not valid.");
          const literals = answer.inputs ?? answer.values;
          if (!Array.isArray(literals) || literals.length !== answer.values.length || !literals.every(
            (item) => typeof item === "string" || typeof item === "number" && Number.isFinite(item),
          )) throw new Error("The table column input units could not be verified.");
          return { values: literals as Array<number | string>, reference: answer.reference as SourceReference,
            normalizedPreview: Array.isArray(answer.normalizedPreview) && answer.normalizedPreview.every((v) =>
              typeof v === "number" && Number.isFinite(v)) ? answer.normalizedPreview as number[] : undefined };
        }}
        charts={data.charts ?? []}
        onChart={(chartType) => data.store.generateChart(id, chartType)}
        onRefresh={(chart) => data.store.generateChart(chart.id, chart.chartType)}
        onDiscuss={(result) => data.store.discussScientificResult(
          id, result.provenance.resultHash, result.provenance.receiptHash ?? result.provenance.resultHash,
        )}
        run={(operationId, inputs, sources, token, method, attemptId) => data.store.runScientificOperation(data.pluginId, id, operationId, inputs, sources, token, method, attemptId)}
        configure={(operationId, inputs, sources, token, method) => data.store.configureScientificRunner(data.pluginId, id, operationId, inputs, sources, token, method)}
        cancel={(operationId, attemptId) => data.store.cancelScientificOperation(data.pluginId, id, operationId, attemptId)} />
      : <p role="alert">This workbench version could not be loaded. Check the plugin in Settings.</p>}
  </>;
}

function ScientificCard({ data, id, selected }: NodeProps<ScientificFlowNode>) {
  const register = useScientificSlots();
  const overlays = useOverlays();
  const collapsed = useLodVisibility();
  const attach = useCallback((element: HTMLDivElement | null) => {
    register?.(id, element ? { element, data } : null);
  }, [id, data, register]);
  return <NodeShell kindClassName="scientific-node" selected={!!selected} collapsed={collapsed}
    header={<><NodeHeading glyph="chart">{data.title}</NodeHeading>
      <button type="button" className="science-focus-trigger nodrag" aria-haspopup="dialog"
        aria-label={"Open focused workbench for " + data.title}
        onClick={() => overlays.open(scientificFocusName(id), "dialog")}>Focus</button></>}
    bodyClassName="science-body nodrag nopan nowheel"><div ref={attach} /></NodeShell>;
}

export const ScientificNodeView = memo(function ScientificNodeView(props: NodeProps<ScientificFlowNode>) {
  const slots = useScientificSlots();
  if (slots) return <ScientificCard {...props} />;
  return <OverlayProviderIfMissing><ScientificWorkspace Editor={ScientificEditor}>
    <ScientificCard {...props} />
  </ScientificWorkspace></OverlayProviderIfMissing>;
});
