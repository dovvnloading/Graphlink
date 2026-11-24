import { useState } from "react";
import { Button } from "../ui/Button";
import { Disclosure } from "../ui/Disclosure";
import { Select } from "../ui/Select";
import type { SceneStore, TableOutputKind } from "./sceneStore";
import { mcpTableColumns, type McpJson } from "./mcpConnect";

export function McpTableActions({ nodeId, value, store, disabled }: { nodeId: string; value: McpJson; store: SceneStore; disabled: boolean }) {
  const columns = mcpTableColumns(value);
  const [group, setGroup] = useState("");
  const [sum, setSum] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const groupColumn = columns.includes(group) ? group : columns[0] ?? "";
  const valueColumn = columns.includes(sum) && sum !== groupColumn ? sum : columns.find(column => column !== groupColumn) ?? "";
  if (columns.length < 2) return null;
  async function create(kind: TableOutputKind) {
    if (busy || disabled) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await store.addTableOutput(nodeId, kind, groupColumn, valueColumn);
      setNotice(kind === "artifact" ? "Bound report created on the canvas." : kind === "chart" ? "Bound chart created on the canvas." : "Grouped table created on the canvas.");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The output could not be created. Choose a numeric sum column and try again."); }
    finally { setBusy(false); }
  }
  return <Disclosure title="Create a connected table, chart, or report">
    <div className="mcp-arguments">
      <p className="mcp-help">Group and sum the latest saved result. These outputs keep their source connection and can be refreshed through Graph Runner.</p>
      {disabled && <p className="mcp-help">Refresh this tool’s result before creating a connected output.</p>}
      <Select label="Group MCP rows by" value={groupColumn} disabled={busy || disabled}
        options={columns.map(column => ({ value: column, label: column }))} onChange={setGroup} />
      <Select label="Sum MCP column" value={valueColumn} disabled={busy || disabled}
        options={columns.filter(column => column !== groupColumn).map(column => ({ value: column, label: column }))} onChange={setSum} />
      <div className="mcp-actions">
        <Button size="sm" disabled={busy || disabled || !valueColumn} onClick={() => { void create("table"); }}>Create grouped table</Button>
        <Button size="sm" disabled={busy || disabled || !valueColumn} onClick={() => { void create("chart"); }}>Create chart</Button>
        <Button size="sm" disabled={busy || disabled || !valueColumn} onClick={() => { void create("artifact"); }}>Create report</Button>
      </div>
      {notice && <p role="status" className="mcp-help">{notice}</p>}
      {error && <p role="alert" className="mcp-help">{error}</p>}
    </div>
  </Disclosure>;
}
