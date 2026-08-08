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
 *
 * ADR-016 stage 16.4: adds the action row - "Open log folder" (fire-and-
 * forget openLogFolder) and "Export diagnostic bundle" (exportDiagnosticBundle,
 * a real request/reply since the point is the returned bundle+path - see
 * exportDiagnosticBundle() below for why that is NOT fireIntent). Both
 * intents live on the same "diagnostics" topic (backend/api/
 * intents_diagnostics.py) and are read-only/non-undoable.
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

/** ADR-016 stage 16.4: the shape `exportDiagnosticBundle` replies with -
 * `bundle` is intentionally untyped here (a free-form redacted snapshot of
 * appVersion/os/nodeCounts/the diagnostics topic's own fields) since it
 * exists to be pretty-printed and copied, not read field-by-field by this
 * component. */
interface DiagnosticBundleResult {
  bundle: unknown;
  path: string;
}

export function DiagnosticsDialog({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<DiagnosticsState>(initialState);
  const [exportResult, setExportResult] = useState<DiagnosticBundleResult | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    return transport.subscribe("diagnostics", (payload) => {
      const validated = TOPIC_VALIDATORS["diagnostics"](payload);
      if (validated.ok) setState(validated.value as DiagnosticsState);
      else console.error("[diagnostics] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  // Fire-and-forget, matching every other "trigger a native OS action, no
  // reply worth waiting on" call site (e.g. SettingsDialog's Ollama/Llama.cpp
  // "Scan Folder..." buttons) - the backend's `{opened: boolean}` reply
  // exists for its own test coverage, not for this UI to branch on.
  function openLogFolder() {
    transport.fireIntent("diagnostics", "openLogFolder", []);
  }

  // Unlike openLogFolder, this call's whole point is its reply (the bundle
  // to preview/copy and the path it was written to) - fireIntent()/intent()
  // are both declared `: void` (see transport.ts's own doc), so this goes
  // through transport.request() directly instead, the same primitive
  // sceneStore.ts's fetchGitlinkRepositories/fetchGitlinkContext already use
  // for exactly this "I need the actual return value" case.
  function exportDiagnosticBundle() {
    setExporting(true);
    setExportError(null);
    transport
      .request("diagnostics", "exportDiagnosticBundle", [])
      .then((value) => setExportResult(value as DiagnosticBundleResult))
      .catch(() => setExportError("Could not export the diagnostic bundle."))
      .finally(() => setExporting(false));
  }

  // Matches ChatNodeView/DocumentViewPanel's own quick-copy pattern: a
  // transient "Copied" flash rather than a persistent state change.
  function copyBundleToClipboard() {
    if (!exportResult) return;
    navigator.clipboard.writeText(JSON.stringify(exportResult.bundle, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

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

      <div className="diagnostics-actions">
        <button type="button" className="diagnostics-action-button" onClick={openLogFolder}>
          Open log folder
        </button>
        <button
          type="button"
          className="diagnostics-action-button"
          onClick={exportDiagnosticBundle}
          disabled={exporting}
        >
          {exporting ? "Exporting…" : "Export diagnostic bundle"}
        </button>
      </div>

      {exportError && (
        <p className="diagnostics-empty" role="alert">
          {exportError}
        </p>
      )}

      {exportResult && (
        <div className="diagnostics-bundle-preview-wrap">
          <div className="diagnostics-actions">
            <button type="button" className="diagnostics-action-button" onClick={copyBundleToClipboard}>
              {copied ? "Copied" : "Copy to clipboard"}
            </button>
          </div>
          <pre className="diagnostics-bundle-preview">{JSON.stringify(exportResult.bundle, null, 2)}</pre>
          <p className="diagnostics-bundle-path">Also written to {exportResult.path}</p>
        </div>
      )}
    </Dialog>
  );
}
