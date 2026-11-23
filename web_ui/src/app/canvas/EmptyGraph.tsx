import { useCallback, useSyncExternalStore } from "react";
import type { SceneStore } from "./sceneStore";
import { useComposerModelStore } from "../context/ModelCapabilityContext";
import { useOverlays } from "../overlays/overlays";
import { InlineAlert } from "../ui/InlineAlert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import "./EmptyGraph.css";

const EMPTY_ROUTE = { provider: "", modelId: "" };
export function EmptyGraph({ store }: { store: SceneStore }) {
  const composerStore = useComposerModelStore();
  const overlays = useOverlays();
  const subscribeRoute = useCallback(
    (notify: () => void) => composerStore?.subscribe(notify) ?? (() => {}),
    [composerStore],
  );
  const readRoute = useCallback(() => composerStore?.getComposer().route ?? EMPTY_ROUTE, [composerStore]);
  const route = useSyncExternalStore(subscribeRoute, readRoute, readRoute);
  const noModelReady = composerStore !== null && (!route.provider || !route.modelId);
  return (
    <section className="empty-graph" aria-label="Empty canvas">
      <EmptyState icon="brand" size="lg" title="Start a graph"
        body="Write a message, add a note, or open a saved chat."
        actions={
          <>
            <Button variant="primary" onClick={() => document.getElementById("composer-message-input")?.focus()}>
              Write a message
            </Button>
            <Button onClick={() => {
              const { x, y } = store.getSpawnPoint();
              store.addNote(x, y);
            }}>Add a note</Button>
            <Button onClick={() => store.addTable()}>Add a table</Button>
          </>
        } />
      <div className="empty-graph-secondary">
        <Button variant="ghost" size="sm" iconStart="workspace" onClick={() => store.loadSampleWorkspace()}>
          Open sample workspace
        </Button>
      </div>
      {noModelReady && <InlineAlert className="empty-graph-model-alert" variant="warning"
        title="No model selected" body="Set up a model to start a conversation."
        action={{ label: "Set up a model", onClick: () => {
          store.openModelTaskSettings("general");
          overlays.open("settings", "dialog");
        } }} />}
    </section>
  );
}
