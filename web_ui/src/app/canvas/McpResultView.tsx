import { useMemo, useState } from "react";
import { Badge, StatusPill } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Disclosure } from "../ui/Disclosure";
import { Select } from "../ui/Select";
import { Tabs, type TabItem } from "../ui/Tabs";
import { formatCount, formatDuration } from "../ui/format";
import { NodeMarkdown } from "./NodeMarkdown";
import { displayMcpValue, resultTable, type McpResult } from "./mcpConnect";
import { McpTableActions } from "./McpTableActions";
import type { SceneStore } from "./sceneStore";

function ResultReceipt({ result }: { result: McpResult }) {
  const receipt = result.provenance;
  const elapsed = (Date.parse(receipt.finishedAt) - Date.parse(receipt.startedAt)) / 1000;
  return <dl className="mcp-receipt">
    <dt>Server</dt><dd>{receipt.serverName}</dd>
    <dt>Tool</dt><dd><code>{receipt.toolName}</code></dd>
    <dt>Finished</dt><dd>{receipt.finishedAt}</dd>
    {Number.isFinite(elapsed) && <><dt>Elapsed</dt><dd>{formatDuration(elapsed)}</dd></>}
    <dt>Request</dt><dd><code>{receipt.requestId}</code></dd>
    <dt>Input fingerprint</dt><dd><code>{receipt.inputHash}</code></dd>
    <dt>Configuration</dt><dd><code>{receipt.configHash}</code></dd>
    <dt>Result fingerprint</dt><dd><code>{receipt.resultHash}</code></dd>
    <dt>Receipt fingerprint</dt><dd><code>{receipt.receiptHash}</code></dd>
    <dt>Connected inputs</dt><dd>{receipt.sources.length ? <ul>{receipt.sources.map(source => <li key={source.argument}>
      <code>{source.argument}</code> from {source.sourceKind} · {source.output}{source.pointer && ` ${source.pointer}`}
      <code className="mcp-source-hash">{source.valueHash}</code>
    </li>)}</ul> : "Literal arguments"}</dd>
  </dl>;
}

function ResultContent({ result }: { result: McpResult }) {
  const table = useMemo(() => resultTable(result.structuredContent), [result.structuredContent]);
  const items: TabItem[] = [
    { id: "text", label: "Text", content: result.text ? <div className="mcp-text-output"><NodeMarkdown content={result.text} /></div>
      : <p className="mcp-help">This tool returned no text. Check the structured output or content blocks.</p> },
    { id: "json", label: "JSON", content: <pre className="mcp-json-output">{JSON.stringify(result.structuredContent, null, 2)}</pre> },
  ];
  if (table) items.push({ id: "table", label: "Table", content: <>
      <div className="mcp-table-scroll"><table className="mcp-result-table"><caption>Tool output</caption>
        <thead><tr>{table.columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
        <tbody>{table.rows.map((row, i) => <tr key={i}>{table.columns.map((column, j) =>
          <td key={column}>{displayMcpValue(row[j] ?? null)}</td>)}</tr>)}</tbody>
      </table></div>
      <p className="mcp-help">Showing {formatCount(table.rows.length)} of {formatCount(table.total)} rows, up to 20 columns. The saved JSON contains the complete result.</p>
    </> });
  items.push({ id: "receipt", label: "Provenance", content: <ResultReceipt result={result} /> });
  return <>
    <Tabs label="Tool result views" items={items} defaultValue={table ? "table" : result.text ? "text" : "json"} />
    {result.content.some(block => block.type !== "text") && <Disclosure title="Additional content blocks">
      <p className="mcp-help">Media and resource blocks are preserved with the result. External resources are not opened automatically.</p>
      <pre className="mcp-json-output">{JSON.stringify(result.content.filter(block => block.type !== "text"), null, 2)}</pre>
    </Disclosure>}
  </>;
}

export function McpResultView({ latest, history, stale, nodeId, store }: {
  latest: McpResult; history: McpResult[]; stale: boolean; nodeId: string; store: SceneStore;
}) {
  const [selected, setSelected] = useState("");
  const [notice, setNotice] = useState("");
  const versions = [latest, ...history.filter(result => result.provenance.receiptHash !== latest.provenance.receiptHash)];
  const result = versions.find(item => item.provenance.receiptHash === selected) ?? latest;
  async function copy() {
    try { await navigator.clipboard.writeText(JSON.stringify(result, null, 2)); setNotice("Result and provenance copied."); }
    catch { setNotice("The result could not be copied. Select the JSON text to copy it manually."); }
  }
  return <section className="mcp-saved-result" aria-label="Saved tool result">
    <div className="mcp-section-heading"><strong>Saved result</strong><Badge>Version {formatCount(result.version)}</Badge>
      <StatusPill status={result.isError ? "Failed" : "Done"} /></div>
    {(stale || result !== latest) && <p role="status" className="mcp-help">{result !== latest
      ? "You are viewing a previous result. Downstream nodes use the latest saved result."
      : "Inputs or configuration have changed. This is the previous saved result; preview a new run to refresh it."}</p>}
    {result.isError && <p role="alert">The tool returned an error. Review its output before changing the inputs or running it again.</p>}
    {versions.length > 1 && <Select label="Result version" value={result.provenance.receiptHash} onChange={setSelected}
      options={versions.map(item => ({ value: item.provenance.receiptHash,
        label: `Version ${formatCount(item.version)} · ${item.provenance.finishedAt}` }))} />}
    <ResultContent key={result.provenance.receiptHash} result={result} />
    {result === latest && !result.isError && <McpTableActions nodeId={nodeId} value={result.structuredContent} store={store} disabled={stale} />}
    <Button size="sm" iconStart="copy" onClick={() => { void copy(); }}>Copy result with provenance</Button>
    {notice && <p role="status" className="mcp-help">{notice}</p>}
  </section>;
}
