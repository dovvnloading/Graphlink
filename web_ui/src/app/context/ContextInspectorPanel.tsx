type ContextSource = {
  sourceId: string;
  origin: string;
  kind: string;
  trust: "system" | "user" | "generated" | "retrieved";
  tokenCount: number;
  sentTokenCount: number;
  included: boolean;
  truncated: boolean;
  leavesDevice: boolean;
  reason: string;
};

export type ContextPreview = {
  nodeId: string;
  model: string;
  provider: string;
  contextWindow?: number;
  promptBudget?: number;
  outputReserve?: number;
  promptTokens?: number;
  leavesDevice: boolean;
  draftIncluded?: boolean;
  notice?: string;
  error?: string;
  sources: ContextSource[];
};


export function ContextInspectorPanel({ preview, error, loading, onRefresh, onClose }: {
  preview: ContextPreview | null | undefined;
  error: string | null | undefined;
  loading: boolean;
  onRefresh: () => void;
  onClose: () => void;
}) {
  return (
        <aside id="context-inspector-panel" className="context-inspector-panel" aria-label="Next chat context" role="region">
          <header className="context-inspector-heading">
            <strong>Next send: Context Inspector</strong>
            <div>
              <button type="button" onClick={onRefresh} aria-label="Refresh context">Refresh</button>
              <button type="button" onClick={onClose} aria-label="Close context inspector">Close</button>
            </div>
          </header>
          {loading && <p role="status">Inspecting context…</p>}
          {error && <p role="alert">{error}</p>}
          {preview && (
            <>
              <p className="context-inspector-meta">
                {preview.provider} · {preview.model || "Unspecified model"} · Node {preview.nodeId}
              </p>
              {preview.promptBudget !== undefined && (
                <p className="context-inspector-meta">
                  {preview.promptTokens} / {preview.promptBudget} prompt tokens
                  ({preview.contextWindow} window, {preview.outputReserve} reserved for output)
                </p>
              )}
              <p className="context-inspector-meta">
                {preview.error ? "No context was dispatched: the required sources cannot fit." : preview.leavesDevice ? "Included content leaves the device for the selected provider." : "Local model route; included context stays on the device."}
              </p>
              {preview.error && <p role="alert">{preview.error}</p>}
              {preview.notice && <p className="context-inspector-note">{preview.notice}</p>}
              <ul className="context-inspector-sources">
                {preview.sources.map((source) => (
                  <li key={source.sourceId} className={source.included ? "included" : "excluded"}>
                    <div className="context-inspector-source-title">
                      <strong>{source.included ? "Included" : "Excluded"} · {source.kind.replaceAll("_", " ")}</strong>
                      <span>{source.tokenCount} tokens</span>
                    </div>
                    <div className="context-inspector-source-origin">{source.origin} · {source.trust} trust</div>
                    <div>{source.reason}{source.truncated ? " (truncated)" : ""}</div>
                    <div>
                      {source.leavesDevice ? "Leaves device" : "Does not leave device"}
                      {source.included && source.sentTokenCount !== source.tokenCount && ` · Sent: ${source.sentTokenCount} tokens`}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>
  );
}
