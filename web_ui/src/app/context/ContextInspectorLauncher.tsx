import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { ComposerStore } from "../chrome/composerStore";
import type { WsTransport } from "../../lib/ws/transport";
import "./ContextInspector.css";
import type { ContextPreview } from "./ContextInspectorPanel";

const LazyContextInspectorPanel = lazy(() =>
  import("./ContextInspectorPanel").then((m) => ({ default: m.ContextInspectorPanel })),
);

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
  const [response, setResponse] = useState<{ key: string; preview: ContextPreview | null; error: string | null } | null>(null);

  const selected = scene.nodes.find((node) => node.id === selectedId && node.kind === "chat");
  const replyTarget = scene.nodes.find((node) => node.id === replyTargetId && node.kind === "chat");
  const lastChat = scene.nodes.filter((node) => node.kind === "chat").slice(-1)[0];
  const nodeId = replyTarget?.id ?? selected?.id ?? lastChat?.id ?? null;

  const requestKey = `${nodeId ?? ""}:${revision}`;
  const currentResponse = response?.key === requestKey ? response : null;
  const preview = currentResponse?.preview;
  const error = currentResponse?.error;
  const loading = isOpen && !!nodeId && !currentResponse;

  useEffect(() => {
    if (!isOpen || !nodeId) return;
    let disposed = false;
    // Snapshot draft at request time, rather than requerying on every
    // keystroke. Refresh is explicit to avoid a flood of network requests.
    const draft = composerStore.getComposer().draft.text;
    void transport.request("scene", "inspectChatContext", [nodeId, draft]).then(
      (result) => {
        if (disposed) return;
        setResponse({ key: requestKey, preview: result as ContextPreview, error: null });
      },
      (cause: unknown) => {
        if (disposed) return;
        setResponse({ key: requestKey, preview: null, error: cause instanceof Error ? cause.message : "Context inspection failed" });
      },
    );
    return () => { disposed = true; };
  }, [isOpen, nodeId, requestKey, transport, composerStore]);

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
        <Suspense fallback={
          <aside id="context-inspector-panel" className="context-inspector-panel" aria-label="Next chat context" role="region">
            <p role="status">Loading context inspector…</p>
          </aside>
        }>
          <LazyContextInspectorPanel
            preview={preview} error={error} loading={loading}
            onRefresh={() => setRevision((n) => n + 1)}
            onClose={() => setIsOpen(false)}
          />
        </Suspense>
      )}
    </div>
  );
}
