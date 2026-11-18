import { useEffect, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { ComposerStore } from "../chrome/composerStore";
import type { WsTransport } from "../../lib/ws/transport";
import "./ContextInspector.css";

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

type ContextPreview = {
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

/** The composer affordance is mounted once, not inside every virtualized node.
 * It reads the selected chat node (or reply target/latest chat) from SceneStore.
 * The server is authoritative: no source-selection or token math runs here.
 */
export function ContextInspectorLauncher({
  sceneStore,
  composerStore,
  transport,
}: {
  sceneStore: SceneStore;
  composerStore: ComposerStore;
  transport: WsTransport;
}) {
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getScene);
  const selectedId = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSelectedNodeId);
  const replyTargetId = useSyncExternalStore(sceneStore.subscribe, sceneStore.getReplyTargetNodeId);
  const [isOpen, setIsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [preview, setPreview] = useState<ContextPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const selected = scene.nodes.find((node) => node.id === selectedId && node.kind === "chat");
  const replyTarget = scene.nodes.find((node) => node.id === replyTargetId && node.kind === "chat");
  const lastChat = scene.nodes.filter((node) => node.kind === "chat").slice(-1)[0];
  const nodeId = replyTarget?.id ?? selected?.id ?? lastChat?.id ?? null;

  useEffect(() => {
    if (!isOpen || !nodeId) return;
    let disposed = false;
    setLoading(true);
    setError(null);
    setPreview(null);
    // Snapshot draft at request time, rather than requerying on every
    // keystroke. Refresh is explicit to avoid a flood of network requests.
    const draft = composerStore.getComposer().draft.text;
    void transport.request("scene", "inspectChatContext", [nodeId, draft]).then(
      (result) => {
        if (disposed) return;
        setPreview(result as ContextPreview);
        setLoading(false);
      },
      (cause: unknown) => {
        if (disposed) return;
        setError(cause instanceof Error ? cause.message : "Context inspection failed");
        setLoading(false);
      },
    );
    return () => { disposed = true; };
  }, [isOpen, nodeId, revision, transport, composerStore]);

  return (
    <div className="context-inspector-launcher">
      <button
        type="button"
        className="context-inspector-toggle"
        disabled={!nodeId}
        aria-expanded={isOpen}
        aria-controls="context-inspector-panel"
        onClick={() => setIsOpen((open) => !open)}
        title={nodeId ? "Preview sources for the next chat send" : "Add a chat message to inspect its context"}
      >
        Context Inspector
      </button>
      {isOpen && nodeId && (
        <aside id="context-inspector-panel" className="context-inspector-panel" aria-label="Next chat context" role="region">
          <header className="context-inspector-heading">
            <strong>Next send: Context Inspector</strong>
            <div>
              <button type="button" onClick={() => setRevision((n) => n + 1)} aria-label="Refresh context">Refresh</button>
              <button type="button" onClick={() => setIsOpen(false)} aria-label="Close context inspector">Close</button>
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
                {preview.leavesDevice ? "Included content leaves the device for the selected provider." : "Local model route; included context stays on the device."}
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
      )}
    </div>
  );
}
