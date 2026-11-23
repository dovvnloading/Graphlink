import { useReactFlow } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { Popover } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { TextField } from "../ui/TextField";
import { TextArea } from "../ui/TextArea";
import { EmptyState } from "../ui/EmptyState";
import "./PinOverlay.css";

const MAX_PIN_TITLE_LENGTH = 120;
const MAX_PIN_NOTE_LENGTH = 4000;

export function PinOverlay({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const { setCenter } = useReactFlow();
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const editRowRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (editingId) editRowRef.current?.querySelector<HTMLInputElement>("input")?.focus();
  }, [editingId]);

  const filtered = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    return term ? scene.pins.filter((pin) =>
      pin.title.toLocaleLowerCase().includes(term) || pin.note.toLocaleLowerCase().includes(term)) : scene.pins;
  }, [query, scene.pins]);

  const addPinHere = useCallback(() => {
    const { x, y } = store.getSpawnPoint();
    store.addPin(`Pin ${scene.pins.length + 1}`, x, y);
  }, [scene.pins.length, store]);

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
  function onEditKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      cancelEditing();
    }
  }
  function saveEditing() {
    const trimmedTitle = draftTitle.trim();
    if (!trimmedTitle) return setDraftError("A title is required");
    if (trimmedTitle.length > MAX_PIN_TITLE_LENGTH) return setDraftError("The title is too long");
    if (draftNote.trim().length > MAX_PIN_NOTE_LENGTH) return setDraftError("The note is too long");
    if (editingId) store.updatePin(editingId, trimmedTitle, draftNote);
    cancelEditing();
  }

  return (
    <Popover name="pins" label="Navigation pins" className="pins-popover" anchored>
      <div className="pins-header">
        <h2 className="pins-title">Pins</h2>
        <Button size="sm" variant="secondary" iconStart="plus" onClick={addPinHere}>
          Pin current view
        </Button>
      </div>
      {scene.pins.length > 0 && (
        <TextField label="Search pins" size="sm" type="search" placeholder="Search pins…"
          value={query} onValueChange={setQuery} />
      )}
      {scene.pins.length === 0 ? (
        <EmptyState icon="pin" size="sm" {...{ title: "No pins yet." }}
          body="Pins save a place on the canvas. Pin the current view to jump back to it later." />
      ) : filtered.length === 0 ? (
        <p className="pins-empty">No pins match “{query}”.</p>
      ) : (
        <ul className="pins-list">
          {filtered.map((pin) => editingId === pin.id ? (
            <li key={pin.id} ref={editRowRef} className="pins-edit-row">
              <TextField label="Pin title" size="sm" value={draftTitle}
                maxLength={MAX_PIN_TITLE_LENGTH + 1} onValueChange={setDraftTitle} onKeyDown={onEditKeyDown} error={draftError ?? undefined} />
              <TextArea label="Pin note" value={draftNote} onValueChange={setDraftNote} onKeyDown={onEditKeyDown} minRows={2} maxRows={4} />
              <div className="pins-edit-actions">
                <Button variant="ghost" size="sm" onClick={cancelEditing}>Cancel</Button>
                <Button variant="primary" size="sm" onClick={saveEditing}>Save</Button>
              </div>
            </li>
          ) : (
            <li key={pin.id} className="pins-row">
              <button type="button" className="pins-jump"
                onClick={() => setCenter(pin.x, pin.y, { zoom: 1, duration: motionDuration(300) })}>
                {pin.title}
              </button>
              <IconButton icon="edit" label={`Edit ${pin.title}`} size="sm"
                onClick={() => startEditing(pin.id, pin.title, pin.note)} />
              <IconButton icon="trash" label={`Delete ${pin.title}`} size="sm"
                onClick={() => store.removePin(pin.id)} />
            </li>
          ))}
        </ul>
      )}
    </Popover>
  );
}
