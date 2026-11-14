import { useState } from "react";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import { TableContributors } from "./TableContributors";
import { artifactDiffPreview } from "./artifactProposalDiff";
import type { SceneStore } from "./sceneStore";
import "./TableWorkflow.css";

export interface TableOutputBinding {
  node: SceneNodeRow;
  store: SceneStore;
}

interface Snapshot {
  version: number;
  sourceHash: string;
  markdown?: string;
  chartData?: { labels: string[]; values: number[] };
}

function isSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== "object" || !("version" in value) || !Number.isSafeInteger(value.version) ||
      !("sourceHash" in value) || typeof value.sourceHash !== "string") return false;
  if ("markdown" in value) return typeof value.markdown === "string";
  if (!("chartData" in value) || !value.chartData || typeof value.chartData !== "object") return false;
  const chart = value.chartData;
  return "labels" in chart && "values" in chart && Array.isArray(chart.labels) && Array.isArray(chart.values)
    && chart.labels.length === chart.values.length && chart.labels.every((l) => typeof l === "string")
    && chart.values.every((n) => typeof n === "number" && Number.isFinite(n));
}

interface Comparison { previous: Snapshot; current: Snapshot; revisionToken: string }

function validateHistory(value: unknown): Comparison {
  if (!value || typeof value !== "object" || !("previous" in value) || !isSnapshot(value.previous)
    || !("current" in value) || !isSnapshot(value.current) || !("revisionToken" in value) || typeof value.revisionToken !== "string") {
    throw new Error("The saved versions could not be read. Reopen the comparison to refresh it.");
  }
  return { previous: value.previous, current: value.current, revisionToken: value.revisionToken };
}

function ComparisonView({ previous, current }: Comparison) {
  if (previous.markdown !== undefined && current.markdown !== undefined) {
    const diff = artifactDiffPreview(previous.markdown, current.markdown);
    if (!diff.changed) return <p>Both versions contain the same report.</p>;
    return <div className="table-version-report">
      <div><b>Version {previous.version}</b><pre>{diff.before || "(empty)"}</pre>
        {(diff.omittedBeforeLines > 0 || diff.clippedBeforeChars) && <small>Preview truncated</small>}</div>
      <div><b>Version {current.version}</b><pre>{diff.after || "(empty)"}</pre>
        {(diff.omittedAfterLines > 0 || diff.clippedAfterChars) && <small>Preview truncated</small>}</div>
    </div>;
  }
  const before = previous.chartData;
  const after = current.chartData;
  if (!before || !after) return <p>These versions have different output types.</p>;
  const prev = new Map(before.labels.map((label, i) => [label, before.values[i]]));
  const next = new Map(after.labels.map((label, i) => [label, after.values[i]]));
  const groups = Array.from(new Set([...prev.keys(), ...next.keys()]));
  const changes = groups.filter((group) => prev.get(group) !== next.get(group));
  if (!changes.length) return <p>Both versions have the same group totals.</p>;
  return <div className="table-workflow-scroll"><table aria-label="Changes in group totals">
    <thead><tr><th scope="col">Group</th><th scope="col">Change</th><th scope="col">Version {previous.version}</th><th scope="col">Version {current.version}</th></tr></thead>
    <tbody>{changes.map((g) => <tr key={g}><th scope="row">{g}</th><td>{!prev.has(g) ? "Added" : !next.has(g) ? "Removed" : "Changed"}</td><td>{prev.get(g) ?? "—"}</td><td>{next.get(g) ?? "—"}</td></tr>)}</tbody>
  </table></div>;
}

export default function TableOutput({ node, store, editing = false }: TableOutputBinding & { editing?: boolean }) {
  const [history, setHistory] = useState<Comparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const protectedOutput = editing || !!node.pendingRequestId || !!node.artifactProposalId || node.tableOutputEdited;
  const compare = history && history.current.version === node.tableOutputVersion && history.current.sourceHash === node.tableSourceHash ? history : null;

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await work(); }
    catch (e) { setError(e instanceof Error ? e.message : "The action failed."); }
    finally { setBusy(false); }
  }

  const statusText = node.tableInputStatus === "stale" ? "Source changed · rerun to update"
    : node.tableInputStatus === "current" ? "Input up to date" : "Source removed · last generated result";

  async function openHistory() {
    const result = validateHistory(await store.getTableOutputHistory(node.id));
    if (result.current.version !== node.tableOutputVersion || result.current.sourceHash !== node.tableSourceHash) {
      throw new Error("The output changed. Reopen the comparison to view its latest versions.");
    }
    setHistory(result);
  }

  return <section className="table-workflow table-output nodrag nopan nowheel" aria-label="Table output">
    <div className="table-output-summary">
      <strong>{node.tableValueColumn} by {node.tableLabelColumn}</strong>
      <span>Version {node.tableOutputVersion}{node.tableInputSourceId && ` · Table ${node.tableInputSourceId}`}</span>
    </div>
    <span role="status" data-state={node.tableInputStatus}>{statusText}</span>
    {(node.tableOutputEdited || editing || node.pendingRequestId || node.artifactProposalId) && <p>
      {node.tableOutputEdited ? "This result has manual edits. Create a new output from the table to keep both."
        : editing ? "Finish editing the document to update from the table." : "Finish the Drafter request or proposal before updating."}
    </p>}
    <div className="table-workflow-actions">
      <button type="button" disabled={busy || !node.tableInputSourceId || protectedOutput}
        onClick={() => void action(async () => { await store.runTableOutput(node.id); setHistory(null); })}>Rerun from table</button>
      {node.tableHasPrevious && <button type="button" aria-expanded={!!compare} disabled={busy}
        onClick={() => {
          if (compare) { setHistory(null); return; }
          void action(openHistory);
        }}>{compare ? "Close comparison" : "Compare versions"}</button>}
    </div>
    {compare && <div className="table-version-comparison">
      <ComparisonView {...compare} />
      <button type="button" disabled={busy || protectedOutput} onClick={() => void action(async () => {
        setHistory(null);
        await store.restoreTableOutput(node.id, compare.revisionToken);
      })}>Restore previous version</button>
    </div>}
    <TableContributors node={node} store={store} />
    {error && <p role="alert">{error}</p>}
  </section>;
}
