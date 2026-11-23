import type { SceneStore } from "./sceneStore";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import "./EmptyGraph.css";

export function EmptyGraph({ store }: { store: SceneStore }) {
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
    </section>
  );
}
