import { useEffect, useMemo, useRef, useState } from "react";
import { ChatLibraryState, initialChatLibraryState } from "./bridgeTypes";
import { BridgeRejection, ChatLibraryBridge, createChatLibraryBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<ChatLibraryState>(initialChatLibraryState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const bridgeRef = useRef<ChatLibraryBridge | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const bridge = createChatLibraryBridge(setState, setRejection);
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

  useEffect(() => {
    if (renamingId !== null) {
      renameRef.current?.focus();
      renameRef.current?.select();
    }
  }, [renamingId]);

  // The notice is shown once per Python revision, then cleared locally the
  // moment the user acts again - same pattern as command-palette (Python has
  // no way to know the user moved on, so JS owns dismissing its own copy).
  const [visibleNotice, setVisibleNotice] = useState<string | null>(null);
  const [noticeRevision, setNoticeRevision] = useState(state.revision);
  if (noticeRevision !== state.revision) {
    setNoticeRevision(state.revision);
    setVisibleNotice(state.notice ?? null);
    // A republish after delete/rename can drop the row a pending confirm/
    // rename targeted; clear both so they can't point at a gone row.
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

  if (rejection) {
    return (
      <BridgeErrorState
        title="Chat library unavailable"
        rejection={rejection}
        className="library-shell bridge-error"
      />
    );
  }

  function selectRow(id: number) {
    setSelectedId(id);
    setConfirmingDeleteId(null);
    setRenamingId(null);
    setVisibleNotice(null);
  }

  function load(id: number) {
    bridgeRef.current?.loadChat(id);
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
    bridgeRef.current?.renameChat(renamingId, title);
    setRenamingId(null);
  }

  function startDelete() {
    if (!effectiveSelected) return;
    setConfirmingDeleteId(effectiveSelected.id);
    setRenamingId(null);
  }

  function confirmDelete() {
    if (confirmingDeleteId === null) return;
    bridgeRef.current?.deleteChat(confirmingDeleteId);
    setConfirmingDeleteId(null);
  }

  function onSearchChange(event: React.ChangeEvent<HTMLInputElement>) {
    setQuery(event.target.value);
    setVisibleNotice(null);
    setConfirmingDeleteId(null);
  }

  function onSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (filtered.length === 0) return;
      const index = filtered.findIndex((row) => row.id === effectiveSelected?.id);
      setSelectedId(filtered[Math.min(index + 1, filtered.length - 1)].id);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (filtered.length === 0) return;
      const index = filtered.findIndex((row) => row.id === effectiveSelected?.id);
      setSelectedId(filtered[Math.max(index - 1, 0)].id);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (effectiveSelected) load(effectiveSelected.id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (confirmingDeleteId !== null) {
        setConfirmingDeleteId(null);
      } else {
        bridgeRef.current?.close();
      }
    }
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
    <div className="library-shell">
      <input
        ref={searchRef}
        className="library-search-input"
        type="text"
        value={query}
        onChange={onSearchChange}
        onKeyDown={onSearchKeyDown}
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
            <button
              type="button"
              className="library-button library-button-danger"
              onClick={confirmDelete}
            >
              Confirm Delete
            </button>
            <button
              type="button"
              className="library-button"
              onClick={() => setConfirmingDeleteId(null)}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="library-button"
              onClick={() => bridgeRef.current?.newChat()}
            >
              New Chat
            </button>
            <button
              type="button"
              className="library-button"
              onClick={startRename}
              disabled={!effectiveSelected}
            >
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

      {visibleNotice && (
        <p className="library-notice" role="status">
          {visibleNotice}
        </p>
      )}

      <ul className="library-list" role="listbox" aria-label="Saved chats">
        {total > 0 && filtered.length === 0 && (
          <li className="library-empty">No chats match your search.</li>
        )}
        {filtered.map((row) => (
          <li
            key={row.id}
            role="option"
            aria-selected={row.id === effectiveSelected?.id}
            className={"library-row" + (row.id === effectiveSelected?.id ? " selected" : "")}
            onClick={() => selectRow(row.id)}
            onDoubleClick={() => load(row.id)}
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
  );
}

export default App;
