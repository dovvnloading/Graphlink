import { useEffect, useMemo, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppChatLibraryState } from "../../lib/bridge-core/generated/app-chat-library-state";
import { Dialog, useOverlays } from "../overlays/overlays";

/**
 * The chat library dialog (Qt-removal plan R2.5e + R6.4) - chat-library
 * island's SPA successor. List/search/rename/delete/load are all real
 * (backend/chat_library.py reads/writes the same ~/.graphlink/chats.db the
 * legacy app uses; R6.4 wired Load Chat to a real backend/session_load.py
 * restore). New Chat has no backend counterpart yet - there is no session
 * SAVE primitive until R6.5, so "start a new session" would have nothing to
 * offer the user beyond clearing the canvas - rendered disabled with an
 * explicit label rather than silently no-op'ing a click.
 */

const initialState: AppChatLibraryState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  rows: [],
  notice: null,
};

export function ChatLibraryDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const [state, setState] = useState<AppChatLibraryState>(initialState);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return transport.subscribe("app-chat-library", (payload) => {
      const validated = TOPIC_VALIDATORS["app-chat-library"](payload);
      if (validated.ok) setState(validated.value as AppChatLibraryState);
      else console.error("[app-chat-library] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  useEffect(() => {
    if (renamingId !== null) {
      renameRef.current?.focus();
      renameRef.current?.select();
    }
  }, [renamingId]);

  // A republish after delete/rename can drop the row a pending confirm/
  // rename targeted - reset-during-render on a revision change, same
  // pattern as CommandPalette's wasOpen tracking, so neither can point at
  // a gone row.
  const [seenRevision, setSeenRevision] = useState(state.revision);
  if (seenRevision !== state.revision) {
    setSeenRevision(state.revision);
    setConfirmingDeleteId(null);
    setRenamingId(null);
  }

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return state.rows;
    return state.rows.filter((row) =>
      `${row.title} ${row.updatedLabel} ${row.createdLabel}`.toLowerCase().includes(term),
    );
  }, [query, state.rows]);

  const effectiveSelected = filtered.find((row) => row.id === selectedId) ?? filtered[0] ?? null;

  function selectRow(id: number) {
    setSelectedId(id);
    setConfirmingDeleteId(null);
    setRenamingId(null);
  }

  function startRename() {
    if (!effectiveSelected) return;
    setRenamingId(effectiveSelected.id);
    setRenameDraft(effectiveSelected.title);
    setConfirmingDeleteId(null);
  }

  function commitRename() {
    const title = renameDraft.trim();
    if (renamingId === null || !title) return;
    transport.intent("app-chat-library", "renameChat", [renamingId, title]);
    setRenamingId(null);
  }

  function startDelete() {
    if (!effectiveSelected) return;
    setConfirmingDeleteId(effectiveSelected.id);
    setRenamingId(null);
  }

  function confirmDelete() {
    if (confirmingDeleteId === null) return;
    transport.intent("app-chat-library", "deleteChat", [confirmingDeleteId]);
    setConfirmingDeleteId(null);
  }

  function loadChat() {
    if (!effectiveSelected) return;
    // Fire-and-forget, same posture as rename/delete above - the backend's
    // own loadChat intent (backend/chat_library.py) reports success/failure
    // via a real "notification" publish, not a return value here. Closing
    // the dialog immediately (rather than waiting on that notification)
    // matches legacy's own ChatLibraryDialog, which closed itself the
    // moment Load Chat was clicked.
    transport.intent("app-chat-library", "loadChat", [effectiveSelected.id]);
    overlays.close();
  }

  function onRenameKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitRename();
    } else if (event.key === "Escape") {
      event.preventDefault();
      setRenamingId(null);
    }
  }

  const total = state.rows.length;
  const statusText =
    total === 0
      ? "No saved chats yet."
      : filtered.length !== total
        ? `Showing ${filtered.length} of ${total} saved ${total === 1 ? "chat" : "chats"}.`
        : `${total} saved ${total === 1 ? "chat" : "chats"}.`;

  return (
    <Dialog name="library" title="Chat Library" className="library-dialog">
      <div className="library-shell">
        <input
          className="library-search-input"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search chats..."
          aria-label="Search chats"
          autoComplete="off"
          spellCheck={false}
        />

        <div className="library-toolbar">
          {renamingId !== null ? (
            <>
              <input
                ref={renameRef}
                className="library-rename-input"
                type="text"
                value={renameDraft}
                onChange={(event) => setRenameDraft(event.target.value)}
                onKeyDown={onRenameKeyDown}
                aria-label="New chat title"
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                className="library-button library-button-primary"
                onClick={commitRename}
                disabled={renameDraft.trim().length === 0}
              >
                Save
              </button>
              <button type="button" className="library-button" onClick={() => setRenamingId(null)}>
                Cancel
              </button>
            </>
          ) : confirmingDeleteId !== null ? (
            <>
              <span className="library-confirm-text" role="status">
                Delete this chat? This cannot be undone.
              </span>
              <button type="button" className="library-button library-button-danger" onClick={confirmDelete}>
                Confirm Delete
              </button>
              <button type="button" className="library-button" onClick={() => setConfirmingDeleteId(null)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button type="button" className="library-button" disabled title="Session save lands in R6.5">
                New Chat
              </button>
              <button
                type="button"
                className="library-button library-button-primary"
                onClick={loadChat}
                disabled={!effectiveSelected}
              >
                Load Chat
              </button>
              <button type="button" className="library-button" onClick={startRename} disabled={!effectiveSelected}>
                Rename
              </button>
              <button
                type="button"
                className="library-button library-button-danger"
                onClick={startDelete}
                disabled={!effectiveSelected}
              >
                Delete
              </button>
            </>
          )}
        </div>

        {state.notice && (
          <p className="library-notice" role="status">
            {state.notice}
          </p>
        )}

        <ul className="library-list" role="listbox" aria-label="Saved chats">
          {total > 0 && filtered.length === 0 && <li className="library-empty">No chats match your search.</li>}
          {filtered.map((row) => (
            <li
              key={row.id}
              role="option"
              aria-selected={row.id === effectiveSelected?.id}
              className={"library-row" + (row.id === effectiveSelected?.id ? " selected" : "")}
              onClick={() => selectRow(row.id)}
            >
              <p className="library-row-title">{row.title}</p>
              <p className="library-row-meta">
                Updated {row.updatedLabel} · Created {row.createdLabel}
              </p>
            </li>
          ))}
        </ul>

        <p className="library-status" role="status">
          {statusText}
        </p>
      </div>
    </Dialog>
  );
}
