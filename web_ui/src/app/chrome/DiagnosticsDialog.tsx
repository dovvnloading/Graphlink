import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { DiagnosticsState } from "../../lib/bridge-core/generated/diagnostics-state";
import { Dialog } from "../overlays/overlays";

/**
 * ADR-016 stage 16.3: the in-app diagnostics dialog - recent run outcomes,
 * publish size/rate, session count, and recent provider errors, all
 * computed by backend/diagnostics.py and pushed live over the "diagnostics"
 * topic. Local-only, in-memory, bounded (see that module's docstring); this
 * is field visibility for the maintainer/a curious user, not a metrics
 * pipeline. Mirrors AboutDialog.tsx's "no client store class needed for a
 * single read-only, never-mutated topic" subscribe pattern.
 */

const initialState: DiagnosticsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  recentRuns: [],
  publishCount: 0,
  publishBytesTotal: 0,
  lastPublishBytes: null,
  lastPublishTopic: null,
  publishBytesPerSecond: 0,
  sessionCount: null,
  providerErrors: [],
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTimestamp(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

export function DiagnosticsDialog({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<DiagnosticsState>(initialState);

  useEffect(() => {
    return transport.subscribe("diagnostics", (payload) => {
      const validated = TOPIC_VALIDATORS["diagnostics"](payload);
      if (validated.ok) setState(validated.value as DiagnosticsState);
      else console.error("[diagnostics] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  return (
    <Dialog name="diagnostics" title="Diagnostics" className="diagnostics-dialog">
      <div className="diagnostics-stats">
        <div className="diagnostics-stat">
          <span className="diagnostics-stat-label">Sessions</span>
          <span className="diagnostics-stat-value">{state.sessionCount ?? "—"}</span>
        </div>
        <div className="diagnostics-stat">
          <span className="diagnostics-stat-label">Publishes</span>
          <span className="diagnostics-stat-value">{state.publishCount}</span>
        </div>
        <div className="diagnostics-stat">
          <span className="diagnostics-stat-label">Total published</span>
          <span className="diagnostics-stat-value">{formatBytes(state.publishBytesTotal)}</span>
        </div>
        <div className="diagnostics-stat">
          <span className="diagnostics-stat-label">Publish rate</span>
          <span className="diagnostics-stat-value">{formatBytes(state.publishBytesPerSecond)}/s</span>
        </div>
        <div className="diagnostics-stat">
          <span className="diagnostics-stat-label">Last publish</span>
          <span className="diagnostics-stat-value">
            {state.lastPublishTopic != null
              ? `${state.lastPublishTopic} · ${formatBytes(state.lastPublishBytes ?? 0)}`
              : "—"}
          </span>
        </div>
      </div>

      <h2 className="diagnostics-section-title">Recent runs</h2>
      {state.recentRuns.length === 0 ? (
        <p className="diagnostics-empty">No runs yet this session.</p>
      ) : (
        <table className="diagnostics-table">
          <thead>
            <tr>
              <th>Kind</th>
              <th>Node</th>
              <th>Outcome</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {state.recentRuns.map((run) => (
              <tr key={run.runId}>
                <td>{run.kind}</td>
                <td>{run.nodeId ?? "—"}</td>
                <td className={`diagnostics-outcome diagnostics-outcome-${run.outcome}`}>{run.outcome}</td>
                <td>{run.durationSeconds != null ? `${run.durationSeconds.toFixed(2)}s` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2 className="diagnostics-section-title">Provider errors</h2>
      {state.providerErrors.length === 0 ? (
        <p className="diagnostics-empty">No provider errors this session.</p>
      ) : (
        <ul className="diagnostics-error-list">
          {state.providerErrors.map((err, index) => (
            <li key={`${err.at}-${index}`} className="diagnostics-error-item">
              <span className="diagnostics-error-provider">{err.provider}</span>
              <span className="diagnostics-error-time">{formatTimestamp(err.at)}</span>
              <span className="diagnostics-error-message">{err.message}</span>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
