import { useState } from "react";
import { pastedArray } from "./scientificArrayPaste";
import { parseInputs, type ScienceField, type ScienceLimits } from "./scientificWorkbench";

const jsonToken = /"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

function preview(raw: string, field: ScienceField, limits?: ScienceLimits) {
  try {
    parseInputs({ id: "", title: "", description: "", fields: [field], example: {} }, { [field.key]: raw }, limits);
    // Quote numeric tokens before preview parsing to preserve every entered digit.
    const values = JSON.parse(raw.replace(jsonToken, (v) => v.startsWith('"') ? v : JSON.stringify(v)));
    return field.kind === "matrix" ? values as string[][] : (values as string[]).map((v) => [v]);
  } catch { return null; }
}

export function ScienceArrayInput({ value, field, limits, onEdit }: {
  value: string; field: ScienceField; limits?: ScienceLimits; onEdit: (raw: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [paste, setPaste] = useState("");
  const [error, setError] = useState("");
  const cells = open ? preview(value, field, limits) : null;
  return <details className="science-array-editor" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Paste or inspect {field.label}</summary>
    {open && <>
      <label>Spreadsheet cells for {field.label}
        <textarea value={paste} rows={3} spellCheck={false} onChange={(event) => { setPaste(event.target.value); setError(""); }} />
      </label>
      <small>{field.kind === "matrix" ? "Paste a rectangular grid, with one observation per row." : "Paste one row or one column."} Separate cells with tabs or commas. Keep units with their values.</small>
      <button type="button" disabled={!paste.trim()} onClick={() => {
        try { onEdit(pastedArray(paste, field, limits)); setError(""); setPaste(""); }
        catch (reason) { setError(reason instanceof Error ? reason.message : "Check the pasted cells."); }
      }}>Apply pasted cells</button>
      {error && <p role="alert">{error}</p>}
      {cells && <div className="science-table">
        <table aria-label={`${field.label} input preview`}><caption>{cells.length} rows × {cells[0].length} columns. Preview shows the first {Math.min(8, cells.length)} rows and {Math.min(8, cells[0].length)} columns.</caption>
          <tbody>{cells.slice(0, 8).map((row, r) => <tr key={r}><th scope="row">{r + 1}</th>{row.slice(0, 8).map((cell, c) => <td key={c}>{cell}</td>)}</tr>)}</tbody>
        </table>
      </div>}
    </>}
  </details>;
}
