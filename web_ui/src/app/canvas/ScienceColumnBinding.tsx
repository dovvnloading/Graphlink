import { useRef, useState } from "react";
import { isImportedTable, type ScienceField, type ScienceOperation, type ScientificImportedTable,
  type SourceOption, type SourceReference } from "./scientificWorkbench";

export interface BindingSelection { completeColumns?: number[]; assumedUnit?: string }
export interface ReviewedColumn { values: Array<number | string>; reference: SourceReference; normalizedPreview?: number[] }
export type FetchTableColumn = (option: SourceOption, op: ScienceOperation, field: ScienceField,
  unitInputs: Record<string, string>, selection?: BindingSelection) => Promise<ReviewedColumn>;
export type BoundColumns = Record<string, ReviewedColumn>;

function tableId(option: SourceOption) {
  const ref = option.reference;
  return JSON.stringify([ref.sourceKey ?? ref.nodeId, ref.resultHash, ref.receiptHash, ref.selector[0]]);
}

interface ColumnBindingProps {
  options: SourceOption[]; op: ScienceOperation; values: Record<string, string>; maxPoints: number;
  fetchColumn: FetchTableColumn; apply: (columns: BoundColumns) => void;
}

export function ScienceColumnBinding(props: ColumnBindingProps) {
  const [open, setOpen] = useState(false);
  if (!props.op.fields.some((field) => field.kind === "vector") ||
      !props.options.some((option) => ["typed_table", "result_table"].includes(String(option.reference.selector[0])))) return null;
  return <details className="science-binding-review" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Bind table columns</summary>
    {open && <ColumnBindingForm {...props} />}
  </details>;
}

function ColumnBindingForm({ options, op, values, maxPoints, fetchColumn, apply }: ColumnBindingProps) {
  const tables = options.filter((item): item is SourceOption & { value: ScientificImportedTable } =>
    ["typed_table", "result_table"].includes(String(item.reference.selector[0])) && isImportedTable(item.value));
  const fields = op.fields.filter((field) => field.kind === "vector");
  const [selected, setSelected] = useState(() => tables[0] ? tableId(tables[0]) : "");
  const [columns, setColumns] = useState<Record<string, string>>({});
  const [assumptions, setAssumptions] = useState<Record<string, string>>({});
  const [first, setFirst] = useState("1");
  const [last, setLast] = useState("");
  const [policy, setPolicy] = useState<"reject" | "drop" | "complete">("reject");
  const [pending, setPending] = useState(false);
  const active = useRef(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState<{ signature: string; columns: BoundColumns } | null>(null);
  const table = tables.find((item) => tableId(item) === selected);
  const end = last === "" ? Math.min(table?.value.rowCount ?? 0, maxPoints) : Number(last);
  const start = Number(first) - 1;
  const unitInputs = Object.fromEntries(op.fields.filter((f) => f.kind === "text" && (
    f.key.endsWith("_unit") || f.key === "from_scale")).map((f) => [f.key, values[f.key]]));
  const chosen = fields.filter((field) => columns[field.key] !== undefined && columns[field.key] !== "");
  const signature = JSON.stringify([table ? selected : null, columns, assumptions, start, end, policy, unitInputs, values]);
  const reviewed = review?.signature === signature ? review.columns : null;
  if (!fields.length || !tables.length) return null;

  async function inspect() {
    if (!table || !chosen.length || active.current) return;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= end ||
        end > table.value.rowCount || end - start > maxPoints) {
      setError(`Choose a range within rows 1–${table.value.rowCount}, with at most ${maxPoints} rows.`); return;
    }
    active.current = true;
    setPending(true); setReview(null); setError("");
    try {
      const results: BoundColumns = {};
      // Inspect all selected fields before offering a single atomic Apply action.
      for (const field of chosen) {
        const column = Number(columns[field.key]);
        const generated = table.reference.selector[0] === "result_table";
        const mode = policy === "reject" ? "reject" : "drop";
        const option: SourceOption = {
          label: table.value.columns[column].label, value: undefined,
          reference: { ...table.reference, selector: [generated ? "result_table_column" : "table_column", column, mode, start, end] },
          tableColumn: { nodeId: table.reference.nodeId, column, policy: mode, start, end, ...(generated && { source: "result" as const }) },
        };
        const group = op.pairedFields?.find((pair) => pair.includes(field.key));
        const completeColumns = policy === "complete" && group ?
          [...new Set(chosen.filter((member) => group.includes(member.key)).map((member) => Number(columns[member.key])))] : undefined;
        const result = await fetchColumn(option, op, field, unitInputs, {
          completeColumns, assumedUnit: assumptions[field.key]?.trim() || undefined,
        });
        if (!result.reference.binding) throw new Error("This server did not return a verified row and unit preview.");
        results[field.key] = result;
      }
      for (const group of op.pairedFields ?? []) {
        const masks = new Set(group.flatMap((key) => results[key] ? [results[key].reference.binding?.rowDigest] : []));
        if (masks.size > 1) throw new Error("The paired columns retain different rows. Choose complete rows for paired fields.");
      }
      setReview({ signature, columns: results });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The columns could not be verified.");
    } finally { setPending(false); active.current = false; }
  }

  return <>
    <fieldset disabled={pending}>
      <label>Connected table<select value={table ? selected : ""} onChange={(event) => {
        setSelected(event.target.value); setColumns({}); setAssumptions({}); setFirst("1"); setLast(""); setError(""); setReview(null);
      }}>
        {!table && <option value="">Choose a current source table</option>}
        {tables.map((item) => <option key={tableId(item)} value={tableId(item)}>{item.label}</option>)}
      </select></label>
      {table && <>
        <div className="science-window">
          <label>First data row<input type="number" min="1" max={table.value.rowCount} value={first} onChange={(e) => setFirst(e.target.value)} /></label>
          <label>Last data row<input type="number" min="1" max={table.value.rowCount} value={last === "" ? end : last} onChange={(e) => setLast(e.target.value)} /></label>
        </div>
        <small>{table.value.rowCount.toLocaleString()} available rows · up to {maxPoints.toLocaleString()} per vector. Row 1 is the first measurement.</small>
        {fields.map((field) => <div className="science-bind-field" key={field.key}>
          <label>Column for {field.label}<select value={columns[field.key] ?? ""} onChange={(event) => {
            setColumns({ ...columns, [field.key]: event.target.value }); setAssumptions({ ...assumptions, [field.key]: "" });
          }}>
            <option value="">Keep current input</option>
            {table.value.columns.map((col, i) => col.type === "numeric" && <option key={i} value={i}>{col.label}</option>)}
          </select></label>
          {columns[field.key] !== undefined && columns[field.key] !== "" && <label>Source unit assumption for {field.label}
            <input value={assumptions[field.key] ?? ""} placeholder="Only for a missing or unrecognized header unit" onChange={(e) => setAssumptions({ ...assumptions, [field.key]: e.target.value })} />
          </label>}
        </div>)}
        <label>Missing observations<select value={policy} onChange={(e) => setPolicy(e.target.value as typeof policy)}>
          <option value="reject">Reject missing</option><option value="drop">Drop missing in each selected column</option>
          {!!op.pairedFields?.length && <option value="complete">Use complete rows for paired fields</option>}
        </select></label>
        <button type="button" disabled={!chosen.length} onClick={() => void inspect()}>{pending ? "Checking columns…" : "Preview bindings"}</button>
      </>}
    </fieldset>
    {error && <p role="alert">{error}</p>}
    {reviewed && <section aria-label="Verified binding preview">
      {Object.entries(reviewed).map(([key, result]) => {
        const binding = result.reference.binding!;
        return <div key={key}>
          <strong>{fields.find((f) => f.key === key)?.label} · {binding.columnLabel}</strong>
          <p>Rows {start + 1}–{end} · {binding.retained} retained · {binding.omitted} omitted</p>
          <small>{binding.sourceUnit || "No header unit"} → {binding.targetUnit}{binding.assumedUnit ? " (declared by you)" : ""}.
            {result.normalizedPreview?.length ? " First values in target units: " + result.normalizedPreview.join(", ") : ""}</small>
        </div>;
      })}
      <button type="button" onClick={() => { apply(reviewed); setReview(null); }}>Apply reviewed bindings</button>
    </section>}
  </>;
}
