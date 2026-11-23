import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { DiagnosticsState } from "../../lib/bridge-core/generated/diagnostics-state";
import { Dialog } from "../overlays/overlays";
import { validateAppAboutState } from "../../lib/bridge-core/generated/app-about-state";
import { validateAppComposerState, type AppComposerRoute } from "../../lib/bridge-core/generated/app-composer-state";
import type { ConnectionStatus } from "../../lib/ws/transport";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Disclosure } from "../ui/Disclosure";
import { InlineAlert } from "../ui/InlineAlert";
import { StatusPill } from "../ui/Badge";
import { formatBytes, formatCount, formatDuration, formatRelativeTime } from "../ui/format";
import "./DiagnosticsDialog.css";

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
  const [version, setVersion] = useState("");
  const [connection, setConnection] = useState<ConnectionStatus>(
    typeof transport.getStatus === "function" ? transport.getStatus() : "connecting");
  const [route, setRoute] = useState<AppComposerRoute | null>(null);
  const [storage, setStorage] = useState<{ usage: number; quota: number } | null>(null);

  useEffect(() => {
    const offAbout = transport.subscribe("app-about", (payload) => {
      const checked = validateAppAboutState(payload);
      if (checked.ok) setVersion(checked.value.appVersion);
    });
    const offComposer = transport.subscribe("app-composer", (payload) => {
      const checked = validateAppComposerState(payload);
      if (checked.ok) setRoute(checked.value.route);
    });
    const offStatus = typeof transport.onStatus === "function"
      ? transport.onStatus(setConnection) : () => {};
    return () => { offAbout(); offComposer(); offStatus(); };
  }, [transport]);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.storage?.estimate) return;
    let active = true;
    void navigator.storage.estimate().then(({ usage, quota }) => {
      if (active && typeof usage === "number" && typeof quota === "number") setStorage({ usage, quota });
    }).catch(() => { /* Browser storage telemetry is optional. */ });
    return () => { active = false; };
  }, []);


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
    navigator.clipboard
      .writeText(JSON.stringify(exportResult.bundle, null, 2))
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      // See NodeMarkdown.tsx's identical handler for why this .catch exists
      // (ADR-011 stage 11.6's clipboard sweep missed four sites; this is one).
      // Worth noting here specifically: this is the diagnostic-bundle copy, so
      // a silent failure would strand a user who is already trying to report a
      // problem.
      .catch((error: unknown) => {
        console.error("Failed to copy the diagnostic bundle to clipboard", error);
      });
  }

  return (
    <Dialog name="diagnostics" {...{ title: "Diagnostics" }} size="md" className="diagnostics-dialog">
      <Card {...{ title: "Health" }} status={connection === "open" ? "Done" : connection === "connecting" || connection === "reconnecting" ? "Running" : "Failed"}>
        <dl className="diagnostics-health-grid">
          <div><dt>App version</dt><dd>{version || "Not reported"}</dd></div>
          <div><dt>Connection</dt><dd>{connection === "open" ? "Connected" : connection}</dd></div>
          <div><dt>Active provider</dt><dd>{route?.provider || "Not configured"}</dd></div>
          <div><dt>Model status</dt><dd>{route
            ? <><StatusPill status={route.available ? "Done" : "Failed"} /> {route.modelLabel || route.modelId || "No model selected"}</>
            : "Checking…"}</dd></div>
          <div><dt>Data folder</dt><dd><code>~/.graphlink</code> (default)</dd></div>
          <div><dt>Browser storage</dt><dd>{storage
            ? `${formatBytes(storage.usage)} of ${formatBytes(storage.quota)} estimated`
            : "Not available"}</dd></div>
        </dl>
        <div className="diagnostics-actions">
          <Button variant="secondary" iconStart="folder" onClick={openLogFolder}>Open log folder</Button>
          <Button variant="primary" iconStart="download" onClick={exportDiagnosticBundle}
            loading={exporting} disabled={exporting}>Export diagnostic bundle</Button>
        </div>
      </Card>
      {exportError && <InlineAlert variant="error" {...{ title: "Couldn't export diagnostics" }} body={exportError} />}
      {exportResult && <div className="diagnostics-bundle-preview-wrap">
        <Button size="sm" variant="secondary" iconStart="copy" onClick={copyBundleToClipboard}>
          {copied ? "Copied" : "Copy to clipboard"}
        </Button>
        <pre className="diagnostics-bundle-preview">{JSON.stringify(exportResult.bundle, null, 2)}</pre>
        <p className="diagnostics-bundle-path">Also written to {exportResult.path}</p>
      </div>}
      <Disclosure {...{ title: "Advanced" }} className="diagnostics-advanced">
        <h3 className="diagnostics-section-title">Publishing metrics</h3>
        <dl className="diagnostics-stats">
          <div><dt>Sessions</dt><dd>{state.sessionCount == null ? "—" : formatCount(state.sessionCount)}</dd></div>
          <div><dt>Publishes</dt><dd>{formatCount(state.publishCount)}</dd></div>
          <div><dt>Total published</dt><dd>{formatBytes(state.publishBytesTotal)}</dd></div>
          <div><dt>Publish rate</dt><dd>{formatBytes(state.publishBytesPerSecond)}/s</dd></div>
          <div><dt>Last publish</dt><dd>{state.lastPublishTopic != null
            ? `${state.lastPublishTopic} · ${formatBytes(state.lastPublishBytes ?? 0)}` : "—"}</dd></div>
        </dl>
        <h3 className="diagnostics-section-title">Recent runs</h3>
        {state.recentRuns.length === 0 ? <p className="diagnostics-empty">No runs yet this session.</p> :
          <div className="diagnostics-table-wrap"><table className="diagnostics-table">
            <thead><tr><th>Kind</th><th>Node</th><th>Outcome</th><th>Duration</th></tr></thead>
            <tbody>{state.recentRuns.map((run) => <tr key={run.runId}>
              <td>{run.kind}</td><td>{run.nodeId ?? "—"}</td>
              <td>{run.outcome}</td>
              <td>{run.durationSeconds == null ? "—" : formatDuration(run.durationSeconds)}</td>
            </tr>)}</tbody>
          </table></div>}
        <h3 className="diagnostics-section-title">Provider errors</h3>
        {state.providerErrors.length === 0 ? <p className="diagnostics-empty">No provider errors this session.</p> :
          <ul className="diagnostics-error-list">{state.providerErrors.map((entry, index) => (
            <li key={`${entry.at}-${index}`} className="diagnostics-error-item">
              <strong>{entry.provider}</strong>
              <span>{formatRelativeTime(entry.at * 1000)}</span>
              <p>{entry.message}</p>
            </li>
          ))}</ul>}
      </Disclosure>
    </Dialog>
  );
}
