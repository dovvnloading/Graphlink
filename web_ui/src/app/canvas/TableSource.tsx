import { useReactFlow } from "@xyflow/react";
import { motionDuration } from "../reducedMotion";
import { useMemo, useRef, useState } from "react";
import type { SceneStore } from "./sceneStore";
import { readTableCsv, writeTableCsv } from "./tableCsv";
import "./TableWorkflow.css";

const PAGE_SIZE = 12;

export interface TableSourceProps {
  id: string;
  content: string;
  store: SceneStore;
}

interface TableDraft {
  base: string;
  mode: "grid" | "csv";
  csv: string;
  grid: string[][];
}

export default function TableSource({ id, content, store }: TableSourceProps) {
  const rows = useMemo(() => readTableCsv(content), [content]);
  const headers = rows[0] ?? [];
  const [draft, setDraft] = useState<TableDraft | null>(null);
  const [page, setPage] = useState(0);
  const [group, setGroup] = useState("");
  const [value, setValue] = useState("");
  const [result, setResult] = useState<{ id: string; kind: "chart" | "artifact" } | null>(null);
  const { setCenter } = useReactFlow();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [readingFile, setReadingFile] = useState(false);
  const importSequence = useRef(0);
  const shown = draft?.mode === "grid" ? draft.grid : rows;
  const rowCount = Math.max(0, shown.length - 1);
  const lastPage = Math.max(0, Math.ceil(rowCount / PAGE_SIZE) - 1);
  const currentPage = Math.min(lastPage, page);
  const start = 1 + PAGE_SIZE * currentPage;
  const groupColumn = headers.includes(group) ? group : headers[0] ?? "";
  const suggestedValue = useMemo(() => {
    const headers = rows[0] ?? [];
    const firstRow = rows[1] ?? [];
    const numeric = headers.find((h, i) => h !== groupColumn && firstRow[i]?.trim() && Number.isFinite(Number(firstRow[i])));
    return numeric ?? headers.find((h) => h !== groupColumn) ?? "";
  }, [rows, groupColumn]);
  const valueColumn = headers.includes(value) && value !== groupColumn ? value : suggestedValue;
  const changedElsewhere = !!draft && draft.base !== content;

  function cancelImport() {
    importSequence.current++;
    setReadingFile(false);
  }

  function edit(mode: TableDraft["mode"]) {
    cancelImport();
    setDraft({ mode, base: content, csv: content, grid: rows });
    setError("");
  }

  async function request(action: () => Promise<unknown>, onSuccess?: () => void) {
    setPending(true);
    setError("");
    try {
      await action();
      onSuccess?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The table action failed.");
    } finally {
      setPending(false);
    }
  }

  function createOutput(kind: "chart" | "artifact") {
    void request(async () => {
      const resultId = await store.addTableOutput(id, kind, groupColumn, valueColumn);
      if (typeof resultId === "string") setResult({ id: resultId, kind });
    });
  }

  function showResult() {
    if (!result) return;
    const output = store.getScene().nodes.find((node) => node.id === result.id);
    if (!output) {
      setResult(null);
      setError("This output is no longer on the canvas.");
      return;
    }
    const width = output.kind === "chart" ? output.chartWidth : 360;
    const height = output.kind === "chart" ? output.chartHeight : 250;
    void setCenter(output.x + width / 2, output.y + height / 2, { zoom: 1, duration: motionDuration(220) });
  }

  async function importFile(file?: File) {
    if (!file) return;
    const sequence = ++importSequence.current;
    setReadingFile(true);
    try {
      if (file.size > 1_048_576) throw new Error("Choose a CSV file under 1 MiB.");
      const csv = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      if (sequence !== importSequence.current) return;
      setDraft((d) => d && { ...d, csv });
      setError("");
    } catch (e) {
      if (sequence === importSequence.current) setError(e instanceof Error ? e.message : "Could not read this CSV file.");
    } finally {
      if (sequence === importSequence.current) setReadingFile(false);
    }
  }

  function changeCell(i: number, j: number, text: string) {
    setDraft((d) => d && { ...d, grid: d.grid.map((row, ri) => ri === i ? row.map((c, ci) => ci === j ? text : c) : row) });
  }

  return (
    <section className="table-workflow nodrag nopan nowheel" aria-label="Data table">
      {draft?.mode === "csv" ? <>
        <label>CSV data<textarea value={draft.csv} disabled={pending} aria-label="CSV data" spellCheck={false}
          onChange={(e) => { cancelImport(); setDraft({ ...draft, csv: e.target.value }); }} /></label>
        {readingFile && <span role="status">Reading CSV…</span>}
        <label>Import CSV file<input type="file" accept=".csv,text/csv" disabled={pending}
          onChange={(e) => { void importFile(e.target.files?.[0]); e.target.value = ""; }} /></label>
      </> : <>
        <div className="table-workflow-scroll">
          <table aria-label="Table data">
            <thead><tr>{(shown[0] ?? []).map((h, j) => <th key={j} scope="col">{draft
              ? <input aria-label={`Column ${j + 1} name`} value={h} disabled={pending} onChange={(e) => changeCell(0, j, e.target.value)} />
              : h}</th>)}</tr></thead>
            <tbody>{shown.slice(start, start + PAGE_SIZE).map((row, i) => <tr key={start + i}>
              {row.map((cell, j) => <td key={j}>{draft
                ? <textarea rows={1} aria-label={`${shown[0][j] || `Column ${j + 1}`}, row ${start + i}`} value={cell}
                    disabled={pending} onChange={(e) => changeCell(start + i, j, e.target.value)} />
                : cell}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
        <div className="table-workflow-actions">
          {lastPage > 0 && <button type="button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous rows</button>}
          <span>{lastPage ? `Rows ${start}–${Math.min(start + PAGE_SIZE - 1, rowCount)} of ${rowCount}` : `${rowCount} ${rowCount === 1 ? "row" : "rows"}`}</span>
          {lastPage > 0 && <button type="button" disabled={currentPage >= lastPage} onClick={() => setPage(currentPage + 1)}>Next rows</button>}
        </div>
      </>}
      <div className="table-workflow-actions">
        {draft ? <>
          {draft.mode === "grid" && <button type="button" disabled={pending} onClick={() => {
            setDraft({ ...draft, grid: [...draft.grid, draft.grid[0].map(() => "")] });
            setPage(Math.floor(rowCount / PAGE_SIZE));
          }}>Add row</button>}
          <button type="button" disabled={pending || readingFile || changedElsewhere} onClick={() => {
            importSequence.current++;
            const csv = draft.mode === "grid" ? writeTableCsv(draft.grid) : draft.csv;
            void request(() => store.setTableCsv(id, csv, draft.base), () => setDraft(null));
          }}>Save table</button>
          <button type="button" disabled={pending} onClick={() => { cancelImport(); setDraft(null); setError(""); }}>Cancel</button>
        </> : <>
          <button type="button" disabled={pending} onClick={() => edit("grid")}>Edit table</button>
          <button type="button" disabled={pending} onClick={() => edit("csv")}>Edit CSV</button>
        </>}
      </div>
      {changedElsewhere && <p role="alert">The saved table changed during this edit. Cancel to reopen the latest data.</p>}
      {!draft && <fieldset disabled={pending}>
        <legend>Summarize by group</legend>
        <label>Group by<select value={groupColumn} onChange={(e) => setGroup(e.target.value)}>
          {headers.map((h) => <option key={h}>{h}</option>)}
        </select></label>
        <label>Sum column<select value={valueColumn} onChange={(e) => setValue(e.target.value)}>
          {headers.filter((h) => h !== groupColumn).map((h) => <option key={h}>{h}</option>)}
        </select></label>
        <div className="table-workflow-actions">
          <button type="button" disabled={!valueColumn} onClick={() => createOutput("chart")}>Create chart</button>
          <button type="button" disabled={!valueColumn} onClick={() => createOutput("artifact")}>Create report</button>
        </div>
      </fieldset>}
      {result && !draft && <div className="table-workflow-actions">
        <span role="status">{result.kind === "chart" ? "Chart created" : "Report created"}</span>
        <button type="button" onClick={showResult}>Show {result.kind === "chart" ? "chart" : "report"}</button>
      </div>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
