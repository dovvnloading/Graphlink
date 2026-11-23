import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Spinner } from "../ui/Spinner";
import { Badge } from "../ui/Badge";

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
  citationId?: string;
  documentTitle?: string;
  sourceFile?: string;
  section?: string;
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
  useKnowledge?: boolean;
  notice?: string;
  error?: string;
  sources: ContextSource[];
  lastReplyCitations?: {
    replyNodeId: string;
    sources: {
      citationId: string;
      documentTitle: string;
      sourceFile: string;
      section: string;
      cited: boolean;
    }[];
    unverifiedIds: string[];
  };
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
              <Button size="sm" variant="ghost" iconStart="refresh" onClick={onRefresh}
                aria-label="Refresh context">Refresh</Button>
              <IconButton size="sm" icon="close" label="Close context inspector" onClick={onClose} />
            </div>
          </header>
          {loading && <p role="status"><Spinner size={14} decorative /> Inspecting context…</p>}
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
              <section aria-label="Included knowledge hits" className="context-inspector-citations">
                <strong>Next send · included knowledge hits</strong>
                <p className="context-inspector-note">
                  {preview.useKnowledge === false
                    ? "Use knowledge is off. No knowledge passages will be retrieved for this request."
                    : "Only these passages fit the next request's prompt budget."}
                </p>
                {preview.sources.filter((source) => source.kind === "knowledge_hit" && source.included).length === 0 && (
                  <p>No knowledge passages are included.</p>
                )}
                <ul>
                  {preview.sources.filter((source) => source.kind === "knowledge_hit" && source.included).map((source) => (
                    <li key={source.sourceId}>
                      <strong>{source.documentTitle || "Untitled source"}</strong> · {source.section || "Unsectioned"}
                      <div>{source.citationId} · {source.sourceFile || source.origin}</div>
                    </li>
                  ))}
                </ul>
              </section>
              <ul className="context-inspector-sources">
                {preview.sources.map((source) => (
                  <li key={source.sourceId} className={source.included ? "included" : "excluded"}>
                    <div className="context-inspector-source-title">
                      <strong>{source.included ? "Included" : "Excluded"} · {source.kind.replaceAll("_", " ")}</strong>
                      <Badge>{source.tokenCount} tokens</Badge>
                    </div>
                    <div className="context-inspector-source-origin">{source.origin} · {source.trust} trust</div>
                    {source.citationId && <div>{source.citationId} · {source.documentTitle} · {source.section}</div>}
                    <div>{source.reason}{source.truncated ? " (truncated)" : ""}</div>
                    <div>
                      {source.leavesDevice ? "Leaves device" : "Does not leave device"}
                      {source.included && source.sentTokenCount !== source.tokenCount && ` · Sent: ${source.sentTokenCount} tokens`}
                    </div>
                  </li>
                ))}
              </ul>
              {preview.lastReplyCitations && (
                <section aria-label="Latest reply citation audit" className="context-inspector-citations">
                  <strong>Latest reply · cited knowledge passages</strong>
                  <p className="context-inspector-note">Sources below were sent with that reply. Only those marked Cited appeared in its answer.</p>
                  {preview.lastReplyCitations.sources.length === 0 && (
                    <p>No knowledge passages were cited from that reply.</p>
                  )}
                  <ul>
                    {preview.lastReplyCitations.sources.map((source) => (
                      <li key={source.citationId}>
                        <strong>{source.cited ? "Cited" : "Not cited"}</strong> · {source.documentTitle} · {source.section}
                        <div>{source.citationId} · {source.sourceFile}</div>
                      </li>
                    ))}
                    {preview.lastReplyCitations.unverifiedIds.map((citationId) => (
                      <li key={citationId}><strong>Unverified</strong> · {citationId} (not in the sent knowledge context)</li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </aside>
  );
}
