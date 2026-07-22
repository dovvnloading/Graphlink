import { useReactFlow } from "@xyflow/react";
import { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover } from "../overlays/overlays";

// Mirrors graphlink_navigation_pins.py's MAX_PIN_TITLE_LENGTH/
// MAX_PIN_NOTE_LENGTH - compile-time constants on both sides, not worth a
// wire round-trip for values that never change at runtime.
const MAX_PIN_TITLE_LENGTH = 120;
const MAX_PIN_NOTE_LENGTH = 4000;

/**
 * The pin overlay (Qt-removal plan R2.4) - pin-overlay island's successor.
 *
 * Real feature parity with the legacy island: search filter, rename + note
 * editing (backend/canvas.py's new `updatePin` intent), jump-to, remove.
 * Simpler than the original's async draft flow, though: that existed
 * because a QWebEngineView popup needed a request/signal round-trip to
 * open an editor. In one SPA process, "start editing" is just local
 * component state - no draft handshake needed - until Save sends the
 * final title/note in a single intent.
 */
export function PinOverlay({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const { getViewport, setCenter } = useReactFlow();
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return scene.pins;
    return scene.pins.filter(
      (pin) => pin.title.toLowerCase().includes(term) || pin.note.toLowerCase().includes(term),
    );
  }, [query, scene.pins]);

  const addPinHere = useCallback(() => {
    // Pin the current view center - the R1/R2 equivalent of the Qt canvas's
    // "Pin this location" context-menu verb.
    const viewport = getViewport();
    const centerX = (window.innerWidth / 2 - viewport.x) / viewport.zoom;
    const centerY = (window.innerHeight / 2 - viewport.y) / viewport.zoom;
    store.addPin(`Pin ${scene.pins.length + 1}`, centerX, centerY);
  }, [getViewport, scene.pins.length, store]);

  function startEditing(pinId: string, title: string, note: string) {
    setEditingId(pinId);
    setDraftTitle(title);
    setDraftNote(note);
    setDraftError(null);
  }

  function cancelEditing() {
    setEditingId(null);
    setDraftError(null);
  }

  function saveEditing() {
    const trimmedTitle = draftTitle.trim();
    if (!trimmedTitle) {
      setDraftError("A title is required");
      return;
    }
    if (trimmedTitle.length > MAX_PIN_TITLE_LENGTH) {
      setDraftError("The title is too long");
      return;
    }
    if (draftNote.trim().length > MAX_PIN_NOTE_LENGTH) {
      setDraftError("The note is too long");
      return;
    }
    if (editingId) store.updatePin(editingId, trimmedTitle, draftNote);
    setEditingId(null);
    setDraftError(null);
  }

  return (
    <Popover name="pins" className="pins-popover">
      <div className="pins-header">
        <span className="pins-title">PINS</span>
        <button type="button" className="pins-add" onClick={addPinHere}>
          + Pin view
        </button>
      </div>

      {scene.pins.length > 0 && (
        <input
          type="text"
          className="pins-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search pins…"
          aria-label="Search pins"
        />
      )}

      {scene.pins.length === 0 ? (
        <p className="pins-empty">No pins yet.</p>
      ) : filtered.length === 0 ? (
        <p className="pins-empty">No pins match “{query}”.</p>
      ) : (
        <ul className="pins-list">
          {filtered.map((pin) =>
            editingId === pin.id ? (
              <li key={pin.id} className="pins-edit-row">
                <input
                  type="text"
                  className="pins-edit-title"
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  aria-label="Pin title"
                  autoFocus
                />
                <textarea
                  className="pins-edit-note"
                  value={draftNote}
                  onChange={(e) => setDraftNote(e.target.value)}
                  aria-label="Pin note"
                  rows={2}
                />
                {draftError && <p className="pins-edit-error">{draftError}</p>}
                <div className="pins-edit-actions">
                  <button type="button" onClick={cancelEditing}>
                    Cancel
                  </button>
                  <button type="button" className="pins-edit-save" onClick={saveEditing}>
                    Save
                  </button>
                </div>
              </li>
            ) : (
              <li key={pin.id} className="pins-row">
                <button
                  type="button"
                  className="pins-jump"
                  onClick={() => setCenter(pin.x, pin.y, { zoom: 1, duration: 300 })}
                  title={pin.note || pin.title}
                >
                  {pin.title}
                </button>
                <button
                  type="button"
                  className="pins-edit-button"
                  aria-label={`Edit ${pin.title}`}
                  onClick={() => startEditing(pin.id, pin.title, pin.note)}
                >
                  ✎
                </button>
                <button
                  type="button"
                  className="pins-remove"
                  aria-label={`Remove ${pin.title}`}
                  onClick={() => store.removePin(pin.id)}
                >
                  ×
                </button>
              </li>
            ),
          )}
        </ul>
      )}
    </Popover>
  );
}
