import type { SceneStore } from "./sceneStore";

export function EmptyGraph({ store }: { store: SceneStore }) {
  return (
    <section className="empty-graph" aria-labelledby="empty-graph-title">
      <h1 id="empty-graph-title">Start a graph</h1>
      <div className="empty-graph-actions">
        <button type="button" onClick={() => document.getElementById("composer-message-input")?.focus()}>
          Write a message
        </button>
        <button type="button" onClick={() => {
          const { x, y } = store.getSpawnPoint();
          store.addNote(x, y);
        }}>Add a note</button>
      </div>
    </section>
  );
}
