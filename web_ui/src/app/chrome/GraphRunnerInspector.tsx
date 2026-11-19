import type { GraphRunTrace } from "./graphRunTrace";
import { serializeGraphRunTrace } from "./graphRunTrace";
import { downloadBlob } from "../canvas/downloadTextFile";
import { Disclosure } from "./RunnerDisclosure";

// Only the expanded trace accordion imports this detailed timeline UI.
function traceTime(value: string | null): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Not recorded" : date.toLocaleString();
}

function traceDuration(value: number | null): string {
  return value === null ? "Not recorded" : `${value.toLocaleString()} ms`;
}

export function GraphRunnerInspectorContent({ trace, names, onSelect, statuses, operations }: {
  trace: GraphRunTrace;
  names: Map<string, string>;
  onSelect: (nodeId: string) => void;
  statuses: Record<string, string>;
  operations: Record<string, string>;
}) {
  const nodeName = (id: string) => names.get(id) || id || "Unavailable";
  const hash = (value: string | null) => value ?? "Not recorded";
  return <>
    <div className="runner-commit">
      <p>Started: {traceTime(trace.startedAt)} · Finished: {traceTime(trace.finishedAt)} · Duration: {traceDuration(trace.durationMs)}</p>
      <button type="button" className="appbar-btn" onClick={() => downloadBlob(
        new Blob([serializeGraphRunTrace(trace)], { type: "application/json" }),
        `graph-run-${trace.runId}.trace.json`,
      )}>Export trace JSON</button>
    </div>
    {trace.steps.length ? <ol className="runner-steps runner-timeline">
      {trace.steps.map((step) => <li key={step.index}>
        <Disclosure className="runner-trace-entry"
          label={`Step ${step.index} · ${nodeName(step.destinationNodeId)} · ${statuses[step.status] ?? step.status}`}
          onToggle={() => onSelect(step.destinationNodeId)}>
          <div>Source: <strong>{nodeName(step.sourceNodeId)}</strong> <code>{step.sourceNodeId || "Unavailable"}</code></div>
          {step.kind === "scientific" && !!step.sourceNodeIds?.length && <div>
            Connected typed inputs: {step.sourceNodeIds.map(nodeName).join(", ")}
          </div>}
          <div>Destination: <strong>{nodeName(step.destinationNodeId)}</strong> <code>{step.destinationNodeId || "Unavailable"}</code></div>
          <div>Operation: {operations[step.operation] ?? step.operation}</div>
          <div>Model: {step.model ? `${step.provider ?? "Provider not recorded"} · ${step.model}` : "Not recorded"}</div>
          <div>Status: <span data-status={step.status}>{statuses[step.status] ?? step.status}</span></div>
          {step.blockedCause && <p>Blocked cause: {step.blockedCause}</p>}
          <div className="runner-fingerprints">
            <div>Input SHA-256: <code>{hash(step.inputFingerprints.input)}</code></div>
            <div>Configuration SHA-256: <code>{hash(step.inputFingerprints.configuration)}</code></div>
            <div>Destination before SHA-256: <code>{hash(step.inputFingerprints.destinationBefore)}</code></div>
            <div>Endpoint SHA-256: <code>{hash(step.inputFingerprints.endpoint)}</code></div>
            <div>Output version SHA-256: <code>{hash(step.outputVersion.sha256)}</code></div>
            <div>Output revision: {step.outputVersion.number ?? "Not recorded"}</div>
          </div>
          <div>Started: {traceTime(step.startedAt)}</div>
          <div>Finished: {traceTime(step.finishedAt)}</div>
          <div>Duration: {traceDuration(step.durationMs)}</div>
          {step.kind === "scientific" && <>
            <div>Scientific worker elapsed: {traceDuration(step.elapsedMs ?? null)}</div>
            {step.errorType && <div>Typed error: {step.errorType}</div>}
            {step.scientificResult && <div className="runner-fingerprints">
              <div>Result SHA-256: <code>{hash(step.scientificResult.resultHash)}</code></div>
              <div>Scientific receipt SHA-256: <code>{hash(step.scientificResult.receiptHash)}</code></div>
            </div>}
          </>}
          <div className="runner-attempts">
            <strong>Attempt history ({step.attempts.length})</strong>
            {step.attempts.length ? <ol>
              {step.attempts.map((attempt) => <li key={attempt.requestId}>
                <div>Request ID: <code>{attempt.requestId}</code></div>
                <div>Outcome: {statuses[attempt.phase] ?? attempt.phase} (saved phase: {attempt.persistedPhase})</div>
                <div>{attempt.kind === "model" ? "Model" : "Tool"}: {attempt.provider ?? "Unknown provider"} · {attempt.model || attempt.tool || "Unknown"}</div>
                <div>Input SHA-256: <code>{hash(attempt.inputFingerprint)}</code></div>
                <div>Dispatched: {traceTime(attempt.dispatchedAt)}</div>
                <div>Resolved: {traceTime(attempt.resolvedAt)}</div>
              </li>)}
            </ol> : <p>No external request was recorded for this step.</p>}
          </div>
        </Disclosure>
      </li>)}
    </ol> : <p>No saved steps in this run.</p>}
  </>;
}
