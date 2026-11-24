import { useCallback, useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { useConfirm } from "../ui/ConfirmDialog";
import type { SceneStore } from "./sceneStore";
import type { McpAttempt } from "./mcpConnect";

export function McpReconciliation({ store, nodeId, onReconciled }: {
  store: SceneStore; nodeId: string; onReconciled: () => void;
}) {
  const askConfirm = useConfirm();
  const [attempts, setAttempts] = useState<McpAttempt[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try { setAttempts(await store.getMcpToolAttempts(nodeId)); setError(""); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Request receipts could not be loaded. Check the connection and refresh."); }
  }, [nodeId, store]);
  useEffect(() => {
    let current = true;
    void store.getMcpToolAttempts(nodeId).then(value => { if (current) setAttempts(value); })
      .catch(failure => { if (current) setError(failure instanceof Error ? failure.message : "Request receipts could not be loaded. Check the connection and refresh."); });
    return () => { current = false; };
  }, [nodeId, store]);
  const unresolved = attempts.filter(attempt => attempt.phase === "reconciliation-required" || attempt.status === "reconciliation-required");
  async function reconcile(attempt: McpAttempt) {
    if (!await askConfirm({ title: "Record the checked outcome?", confirmLabel: "I checked the outcome",
      body: `Confirm you checked the recorded operation and time in the connected service. ${attempt.requestId} is Graphlink’s local audit ID, not a remote idempotency key. This acknowledges the uncertain request; it does not undo remote changes. A future run is a separate action and is never started automatically.` })) return;
    setBusy(true);
    try {
      await store.reconcileMcpTool(nodeId, attempt.requestId);
      await refresh();
      if (unresolved.length <= 1) onReconciled();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The acknowledgement could not be saved. Check the connection and try again."); }
    finally { setBusy(false); }
  }
  return <section className="mcp-approval" aria-label="External request reconciliation">
    {unresolved.map(attempt => <div key={attempt.requestId}>
      <p><strong>{attempt.toolName}</strong> · {attempt.detail}</p>
      <p className="mcp-help">Request ID: <code>{attempt.requestId}</code></p>
      <Button disabled={busy} onClick={() => { void reconcile(attempt); }}>I checked the outcome</Button>
    </div>)}
    {!unresolved.length && <>
      <p className="mcp-help">No unresolved request is currently recorded here. A fresh preview checks the saved graph again before another tool call can be sent.</p>
      <Button disabled={busy} onClick={onReconciled}>Return to tool preview</Button>
    </>}
    <Button size="sm" iconStart="refresh" disabled={busy} onClick={() => { void refresh(); }}>Refresh request receipts</Button>
    {error && <p role="alert">{error}</p>}
  </section>;
}
