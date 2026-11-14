import { useEffect, useId, useMemo, useState } from "react";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import type { SceneStore } from "./sceneStore";
import { readTableCsv } from "./tableCsv";

const PAGE_SIZE = 50;

interface Contribution {
  rowNumber: number;
  value: string;
}

interface Page {
  rows: Contribution[];
  totalRows: number;
}

type Result = { state: "ready"; page: Page } | { state: "error"; message: string };

function isContribution(value: unknown): value is Contribution {
  return typeof value === "object" && value !== null &&
    "rowNumber" in value && typeof value.rowNumber === "number" && Number.isSafeInteger(value.rowNumber) &&
    value.rowNumber > 0 && "value" in value && typeof value.value === "string";
}

function readPage(value: unknown, hash: string, group: string, offset: number): Page {
  if (value === null || typeof value !== "object" || !("sourceHash" in value) || value.sourceHash !== hash) {
    throw new Error("This result changed. Reopen its source rows.");
  }
  if (!("group" in value) || value.group !== group || !("offset" in value) || value.offset !== offset ||
      !("totalRows" in value) || typeof value.totalRows !== "number" || !Number.isSafeInteger(value.totalRows) ||
      value.totalRows <= offset || !("rows" in value) || !Array.isArray(value.rows) ||
      value.rows.length !== Math.min(PAGE_SIZE, value.totalRows - offset) || !value.rows.every(isContribution)) {
    throw new Error("The source rows could not be read.");
  }
  return { rows: value.rows, totalRows: value.totalRows };
}

function Rows({ store, node, group, offset, setOffset }: {
  store: SceneStore; node: SceneNodeRow; group: string; offset: number; setOffset: (offset: number) => void;
}) {
  const [result, setResult] = useState<Result | null>(null);
  const { id, tableSourceHash, tableValueColumn } = node;
  useEffect(() => {
    let active = true;
    void store.getTableContributors(id, group, offset)
      .then((value) => readPage(value, tableSourceHash, group, offset))
      .then((page) => { if (active) setResult({ state: "ready", page }); })
      .catch((error: unknown) => {
        if (active) setResult({ state: "error", message: error instanceof Error ? error.message : "Could not read source rows." });
      });
    return () => { active = false; };
  }, [store, id, tableSourceHash, group, offset]);

  if (!result) return <p>Loading source rows…</p>;
  if (result.state === "error") return <p role="alert">{result.message}</p>;
  const { rows, totalRows } = result.page;
  return <>
    <div className="table-workflow-scroll">
      <table aria-label={`Source rows for ${group}`}>
        <thead><tr><th scope="col">Data row</th><th scope="col">{tableValueColumn}</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.rowNumber}><td>{row.rowNumber}</td><td>{row.value}</td></tr>)}</tbody>
      </table>
    </div>
    {totalRows > PAGE_SIZE && <div className="table-workflow-actions">
      <button type="button" disabled={!offset} onClick={() => setOffset(offset - PAGE_SIZE)}>Previous source rows</button>
      <span>{offset + 1}–{offset + rows.length} of {totalRows}</span>
      <button type="button" disabled={offset + rows.length >= totalRows}
        onClick={() => setOffset(offset + PAGE_SIZE)}>Next source rows</button>
    </div>}
  </>;
}

function GroupPicker({ store, node }: { store: SceneStore; node: SceneNodeRow }) {
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const source = store.getScene().nodes.find((row) => row.id === node.tableInputSourceId);
  const groups = useMemo(() => {
    if (node.kind === "chart" && node.chartData.labels) return node.chartData.labels;
    if (!source) return [];
    const [headers, ...data] = readTableCsv(source.content);
    const index = headers?.indexOf(node.tableLabelColumn) ?? -1;
    if (index < 0) return [];
    // Python str.split treats the four C0 separator characters as whitespace too.
    // eslint-disable-next-line no-control-regex
    const whitespace = /[\p{White_Space}\u001c-\u001f]+/gu;
    return [...new Set(data.map((row) => row[index].split(whitespace).filter(Boolean).join(" ")))];
  }, [node.kind, node.chartData.labels, node.tableLabelColumn, source]);
  const group = groups.includes(selected) ? selected : groups[0];
  if (!group) return <p>No groups available.</p>;
  return <>
    <label>Inspect group<select value={group} onChange={(event) => { setSelected(event.target.value); setOffset(0); }}>
      {groups.map((name) => <option key={name}>{name}</option>)}
    </select></label>
    <Rows key={JSON.stringify([group, offset])} store={store} node={node} group={group} offset={offset} setOffset={setOffset} />
  </>;
}

export function TableContributors({ node, store }: { node: SceneNodeRow; store: SceneStore }) {
  const [open, setOpen] = useState(false);
  const region = useId();
  const current = node.tableInputStatus === "current" && !node.tableOutputEdited;
  return <div>
    <button type="button" disabled={!current} aria-expanded={current && open} aria-controls={region}
      onClick={() => setOpen(!open)}>Inspect source rows</button>
    <div id={region} hidden={!current || !open}>
      {current && open && <GroupPicker key={JSON.stringify([node.tableInputSourceId, node.tableSourceHash])} node={node} store={store} />}
    </div>
  </div>;
}
