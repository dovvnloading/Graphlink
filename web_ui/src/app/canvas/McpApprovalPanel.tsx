import { useEffect, useRef, useState } from "react";
import { Button } from "../ui/Button";
import { InlineAlert } from "../ui/InlineAlert";
import type { SceneStore } from "./sceneStore";
import type { McpApproval } from "./mcpConnect";
import "./McpToolNodeView.css";

export function McpApprovalPanel({ store, active, nodeId }: { store: SceneStore; active: boolean; nodeId?: string }) {
  const [approvals, setApprovals] = useState<McpApproval[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState("");
  const [decision, setDecision] = useState("");
  const generation = useRef(0);
  const inFlight = useRef(false);
  const summary = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      const version = generation.current;
      try {
        const next = await store.getMcpToolApprovals();
        if (!stopped && !inFlight.current && version === generation.current) { setApprovals(next); setError(""); }
      } catch (failure) {
        if (!stopped) setError(failure instanceof Error ? failure.message : "Pending approvals could not be loaded. Reconnect and check the run.");
      }
      if (!stopped) timer = setTimeout(() => { void poll(); }, 1000);
    }
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [active, store]);
  if (!active) return null;
  const visible = approvals.filter(item => !nodeId || item.nodeId === nodeId);
  async function answer(id: string, approved: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    generation.current++;
    summary.current?.focus();
    setSubmitting(id);
    setDecision("");
    try {
      await store.approveMcpTool(id, approved);
      setApprovals(current => current.filter(item => item.id !== id));
      setDecision(approved ? "Tool approved." : "Tool denied.");
      setError("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The decision could not be saved. Check the connection and try again."); }
    finally { generation.current++; inFlight.current = false; setSubmitting(""); }
  }
  return <div className="mcp-approvals">
    <p ref={summary} tabIndex={-1} role="status">{submitting ? "Submitting tool decision…" : `${decision} ${visible.length} pending tool approvals.`.trim()}</p>
    {error && <InlineAlert variant="error" title="Approval unavailable" body={error} />}
    {visible.map(approval => <section key={approval.id} className="mcp-approval" aria-label={`Approve ${approval.toolName}`}>
      <InlineAlert variant="warning" title="Tool approval required"
        body={<>{approval.serverName} will run <strong>{approval.toolName}</strong> with the resolved inputs below.</>} />
      <pre className="mcp-json-output" tabIndex={0} role="region" aria-label={`Resolved inputs for ${approval.toolName}`}>{JSON.stringify(approval.arguments, null, 2)}</pre>
      {approval.scopes.length > 0 && <p className="mcp-help">Scopes: {approval.scopes.join(", ")}</p>}
      <div className="mcp-actions">
        <Button disabled={!!submitting} onClick={() => { void answer(approval.id, false); }}>Deny tool</Button>
        <Button variant="primary" loading={submitting === approval.id} disabled={!!submitting}
          onClick={() => { void answer(approval.id, true); }}>Approve tool</Button>
      </div>
    </section>)}
  </div>;
}
