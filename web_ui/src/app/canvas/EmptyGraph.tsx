import type { SceneStore } from "./sceneStore";

export function EmptyGraph({ store }: { store: SceneStore }) {
  return (
    <section className="empty-graph" aria-labelledby="empty-graph-title">
      <svg viewBox="0 0 204 86" className="empty-graph-diagram" aria-hidden="true">
        <g className="diagram-links" fill="none">
          <path d="M102 26v9q0 7-8 7H54q-8 0-8 8v9m56-24q0 7 8 7h40q8 0 8 8v9" />
        </g>
        <g className="diagram-card diagram-chat" transform="translate(77 3)">
          <rect width="50" height="23" rx="6" />
          <path d="M8 8h9v6h-5l-3 2v-2H8zm16 2h17m-17 4h11" />
        </g>
        <g className="diagram-card diagram-content" transform="translate(21 59)">
          <rect width="50" height="23" rx="6" />
          <path d="M9 7h7v9H9zm14 3h18m-18 4h12" />
        </g>
        <g className="diagram-card diagram-tool" transform="translate(133 59)">
          <rect width="50" height="23" rx="6" />
          <path d="m8 9 3 3-3 3m5 0h4m6-5h18m-18 4h12" />
        </g>
      </svg>
      <h1 id="empty-graph-title">Start a graph</h1>
      <p>Begin with a message or place a note on the canvas.</p>
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
