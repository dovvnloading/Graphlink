import { useEffect, useMemo, useRef, useState } from "react";
import { PinOverlayState, initialPinOverlayState } from "./bridgeTypes";
import { BridgeRejection, PinOverlayBridge, createPinOverlayBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

// Mirrors graphlink_navigation_pins.py's MAX_PIN_TITLE_LENGTH/
// MAX_PIN_NOTE_LENGTH exactly - compile-time constants on both sides, not
// worth a wire round-trip for values that never change at runtime.
const MAX_PIN_TITLE_LENGTH = 120;
const MAX_PIN_NOTE_LENGTH = 4000;

function validateDraft(title: string, note: string): string | null {
  const trimmedTitle = title.trim();
  if (!trimmedTitle) return "A title is required";
  if (trimmedTitle.length > MAX_PIN_TITLE_LENGTH) return "The title is too long";
  if (note.trim().length > MAX_PIN_NOTE_LENGTH) return "The note is too long";
  return null;
}

function App() {
  const [state, setState] = useState<PinOverlayState>(initialPinOverlayState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [query, setQuery] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [draftPinId, setDraftPinId] = useState<string | null>(null);
  const [localDraftError, setLocalDraftError] = useState<string | null>(null);
  const bridgeRef = useRef<PinOverlayBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const draftTitleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const bridge = createPinOverlayBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  // Content-driven height negotiation, matching the legacy panel's own
  // _resize_for_content() - the host bounds this to [MIN_HEIGHT, MAX_HEIGHT]
  // on the Python side (PinOverlayBridge.resize).
  useEffect(() => {
    const element = shellRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height;
      if (height) bridgeRef.current?.resize(Math.ceil(height));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Sync the editable draft fields whenever a genuinely NEW draft begins
  // (keyed on pinId changing), during render rather than an effect -
  // matches this island family's established set-state-in-effect lint fix
  // (see command-palette's identical wasVisible pattern). Python never
  // round-trips what the user is typing - only the draft's STARTING values,
  // once, when the draft opens.
  if (state.draft && state.draft.pinId !== draftPinId) {
    setDraftPinId(state.draft.pinId);
    setDraftTitle(state.draft.title);
    setDraftNote(state.draft.note);
    setLocalDraftError(null);
  } else if (!state.draft && draftPinId !== null) {
    setDraftPinId(null);
  }

  const draftPinIdFromState = state.draft?.pinId;
  useEffect(() => {
    if (draftPinIdFromState) draftTitleRef.current?.focus();
  }, [draftPinIdFromState]);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return state.rows;
    return state.rows.filter(
      (row) => row.title.toLowerCase().includes(term) || row.note.toLowerCase().includes(term),
    );
  }, [query, state.rows]);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Navigation pins are unavailable"
        rejection={rejection}
        className="pin-overlay-shell bridge-error"
      />
    );
  }

  function commitDraft() {
    const validationError = validateDraft(draftTitle, draftNote);
    if (validationError) {
      setLocalDraftError(validationError);
      return;
    }
    setLocalDraftError(null);
    bridgeRef.current?.commitDraft(draftTitle.trim(), draftNote.trim());
  }

  function cancelDraft() {
    setLocalDraftError(null);
    bridgeRef.current?.discardDraft();
  }

  function onDraftTitleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelDraft();
    } else if (event.key === "Enter") {
      event.preventDefault();
      commitDraft();
    }
  }

  function onDraftNoteKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelDraft();
    }
    // Enter is left alone here - a note is a multi-line field, so Enter
    // should insert a newline, not commit (matching the legacy QTextEdit's
    // own behavior in NavigationPinEditor).
  }

  if (state.draft) {
    const isNew = state.draft.isNew;
    const displayError = localDraftError ?? state.error;
    return (
      <div className="pin-overlay-shell" ref={shellRef}>
        <div className="pin-overlay-header">
          <div className="pin-overlay-heading">
            <p className="pin-overlay-title">{isNew ? "Add navigation pin" : "Edit navigation pin"}</p>
            <p className="pin-overlay-meta">
              {isNew ? "Name this canvas location" : "Update this canvas location"}
            </p>
          </div>
        </div>

        <div className="pin-overlay-editor">
          <label className="pin-overlay-editor-label" htmlFor="pin-overlay-title-input">
            Title
          </label>
          <input
            id="pin-overlay-title-input"
            ref={draftTitleRef}
            className="pin-overlay-editor-input"
            type="text"
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            onKeyDown={onDraftTitleKeyDown}
            maxLength={MAX_PIN_TITLE_LENGTH}
            placeholder="e.g. Research checkpoint"
            aria-label="Navigation pin title"
            autoComplete="off"
            spellCheck={false}
          />

          <label className="pin-overlay-editor-label" htmlFor="pin-overlay-note-input">
            Note (optional)
          </label>
          <textarea
            id="pin-overlay-note-input"
            className="pin-overlay-editor-textarea"
            value={draftNote}
            onChange={(event) => setDraftNote(event.target.value)}
            onKeyDown={onDraftNoteKeyDown}
            maxLength={MAX_PIN_NOTE_LENGTH}
            aria-label="Navigation pin note"
            spellCheck={false}
          />

          {displayError && (
            <p className="pin-overlay-editor-error" role="alert">
              {displayError}
            </p>
          )}
        </div>

        <div className="pin-overlay-footer">
          <button type="button" className="pin-overlay-button" onClick={cancelDraft}>
            Cancel
          </button>
          <button
            type="button"
            className="pin-overlay-button primary"
            onClick={commitDraft}
            disabled={draftTitle.trim().length === 0}
          >
            Save
          </button>
        </div>
      </div>
    );
  }

  function onSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      bridgeRef.current?.close();
    }
  }

  const total = state.rows.length;
  const countText =
    total === 0
      ? "No saved locations"
      : filtered.length !== total
        ? `Showing ${filtered.length} of ${total} saved location${total === 1 ? "" : "s"}`
        : `${total} saved location${total === 1 ? "" : "s"}`;

  return (
    <div className="pin-overlay-shell" ref={shellRef}>
      <div className="pin-overlay-header">
        <div className="pin-overlay-heading">
          <p className="pin-overlay-title">Navigation pins</p>
          <p className="pin-overlay-meta">Revisit saved canvas locations</p>
        </div>
        <button type="button" className="pin-overlay-close-btn" onClick={() => bridgeRef.current?.close()}>
          Close
        </button>
      </div>

      <input
        ref={searchRef}
        className="pin-overlay-search-input"
        type="text"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={onSearchKeyDown}
        placeholder="Search pins..."
        aria-label="Search navigation pins"
        autoComplete="off"
        spellCheck={false}
      />

      <ul className="pin-overlay-list" role="listbox" aria-label="Saved navigation pins">
        {filtered.length === 0 && (
          <li className="pin-overlay-empty">
            {total === 0 ? "No saved locations yet." : "No pins match your search."}
          </li>
        )}
        {filtered.map((row) => (
          <li
            key={row.id}
            role="option"
            aria-selected={row.id === state.selectedPinId}
            className={"pin-overlay-row" + (row.id === state.selectedPinId ? " selected" : "")}
          >
            <button
              type="button"
              className="pin-overlay-row-main"
              onClick={() => bridgeRef.current?.selectPin(row.id)}
            >
              <p className="pin-overlay-row-title">{row.title}</p>
              {row.note && <p className="pin-overlay-row-note">{row.note}</p>}
            </button>
            <div className="pin-overlay-row-actions">
              <button
                type="button"
                className="pin-overlay-row-action"
                onClick={() => bridgeRef.current?.editPin(row.id)}
                aria-label={`Edit ${row.title}`}
              >
                Edit
              </button>
              <button
                type="button"
                className="pin-overlay-row-action danger"
                onClick={() => bridgeRef.current?.deletePin(row.id)}
                aria-label={`Delete ${row.title}`}
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>

      <div className="pin-overlay-footer">
        <p className="pin-overlay-count">{countText}</p>
        <button
          type="button"
          className="pin-overlay-add-btn"
          onClick={() => bridgeRef.current?.createPin()}
          disabled={total >= 100}
        >
          Add pin here
        </button>
      </div>
    </div>
  );
}

export default App;
