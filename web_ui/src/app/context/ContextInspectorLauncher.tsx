import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { ComposerStore } from "../chrome/composerStore";
import type { WsTransport } from "../../lib/ws/transport";
import "./ContextInspector.css";
import { OverlayProviderIfMissing, Popover, useOverlays } from "../overlays/overlays";
import { IconButton } from "../ui/IconButton";
import { Spinner } from "../ui/Spinner";
import { Tooltip } from "../ui/Tooltip";
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
  useKnowledge = null,
  targetNodeId,
}: {
  sceneStore: SceneStore;
  composerStore: ComposerStore;
  transport: WsTransport;
  useKnowledge?: boolean | null;
  targetNodeId?: string | null;
}) {
  return (
    <OverlayProviderIfMissing>
      <ContextInspectorSurface sceneStore={sceneStore} composerStore={composerStore}
        transport={transport} useKnowledge={useKnowledge} targetNodeId={targetNodeId} />
    </OverlayProviderIfMissing>
  );
}

function ContextInspectorSurface({
  sceneStore, composerStore, transport, useKnowledge, targetNodeId,
}: {
  sceneStore: SceneStore; composerStore: ComposerStore; transport: WsTransport;
  useKnowledge: boolean | null; targetNodeId?: string | null;
}) {
  const overlays = useOverlays();
  const name = "context-inspector";
  const isOpen = overlays.isOpen(name);
  const scene = useSyncExternalStore(sceneStore.subscribe, sceneStore.getScene);
  const selectedId = useSyncExternalStore(sceneStore.subscribe, sceneStore.getSelectedNodeId);
  const replyTargetId = useSyncExternalStore(sceneStore.subscribe, sceneStore.getReplyTargetNodeId);
  const [revision, setRevision] = useState(0);
  const [response, setResponse] = useState<{ key: string; preview: ContextPreview | null; error: string | null } | null>(null);

  const selected = scene.nodes.find((node) => node.id === selectedId && node.kind === "chat");
  const replyTarget = scene.nodes.find((node) => node.id === replyTargetId && node.kind === "chat");
  const lastChat = scene.nodes.filter((node) => node.kind === "chat").slice(-1)[0];
  const nodeId = targetNodeId === undefined
    ? replyTarget?.id ?? selected?.id ?? lastChat?.id ?? null
    : targetNodeId;

  const requestKey = `${nodeId ?? ""}:${useKnowledge ?? "auto"}:${revision}`;
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
    const args = useKnowledge === null ? [nodeId, draft] : [nodeId, draft, useKnowledge];
    void transport.request("scene", "inspectChatContext", args).then(
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
  }, [isOpen, nodeId, requestKey, transport, composerStore, useKnowledge]);

  const toggle = (
    <IconButton icon="layers" label="Context Inspector" className="context-inspector-toggle"
      data-overlay-trigger={name} aria-expanded={isOpen} aria-controls="context-inspector-panel"
      disabled={!nodeId} onClick={() => overlays.toggle(name, "popover")} />
  );

  return (
    <div className="context-inspector-launcher">
      {nodeId ? toggle : (
        <Tooltip label="Add a chat message to inspect its context">
          {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- focusable wrapper so keyboard users can reach the disabled control's reason tooltip */}
          <span className="context-inspector-disabled-trigger" tabIndex={0}>{toggle}</span>
        </Tooltip>
      )}
      {nodeId && (
        <Popover anchored name={name} label="Next chat context" className="context-inspector-popover">
          <Suspense fallback={<p role="status"><Spinner size={14} decorative /> Loading context inspector…</p>}>
            <LazyContextInspectorPanel preview={preview} error={error} loading={loading}
              onRefresh={() => setRevision((n) => n + 1)}
              onClose={() => overlays.closeLayer(name)} />
          </Suspense>
        </Popover>
      )}
    </div>
  );
}
