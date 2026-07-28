import { useEffect, useMemo, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppChatLibraryRow, AppChatLibraryState } from "../../lib/bridge-core/generated/app-chat-library-state";
import { Dialog, useOverlays } from "../overlays/overlays";

/**
 * The chat library dialog (Qt-removal plan R2.5e + R6.4 + R6.5, R8a full
 * redesign). List/search/rename/delete/load/new are all real (backend/
 * chat_library.py reads/writes the same ~/.graphlink/chats.db the legacy
 * app uses).
 *
 * R8a replaced the old select-a-row-then-click-a-shared-toolbar-button
 * model (which is what made this "the worst UI element" - a permanently
 * boxed flat list, no content preview, four buttons that mutated based on
 * "whichever row is currently selected") with a real chat-history list:
 * date-grouped rows (Today/Yesterday/Previous 7 Days/Previous 30 Days/
 * Older), a one-line preview of each chat's last message, and per-row
 * rename/delete that act on THAT row, never a shared selection. Clicking a
 * row's body IS Load Chat now - there is no separate select step. Reuses
 * the composer island's exact "flat/borderless until interaction, one
 * filled accent element" language rather than inventing a new visual
 * system - see styles.css's own comment on the .library-* rules.
 */

const initialState: AppChatLibraryState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  rows: [],
  notice: null,
};

type BucketKey = "Today" | "Yesterday" | "Previous 7 Days" | "Previous 30 Days" | "Older";
const BUCKET_ORDER: BucketKey[] = ["Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older"];
const DAY_MS = 24 * 60 * 60 * 1000;

function startOfLocalDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function effectiveIso(row: AppChatLibraryRow): string | null {
  return row.updatedAtIso ?? row.createdAtIso ?? null;
}

// Rows with no usable timestamp (an old pre-migration row, or a corrupt
// one) always land in Older, sorted after every dated Older row - never
// guessed into Today/Yesterday just because they're undated.
function bucketFor(row: AppChatLibraryRow, todayStart: number): BucketKey {
  const iso = effectiveIso(row);
  if (!iso) return "Older";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "Older";
  const diffDays = Math.round((todayStart - startOfLocalDay(parsed)) / DAY_MS);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays <= 7) return "Previous 7 Days";
  if (diffDays <= 30) return "Previous 30 Days";
  return "Older";
}

function sortWithinBucket(rows: AppChatLibraryRow[]): AppChatLibraryRow[] {
  return [...rows].sort((a, b) => {
    const aIso = effectiveIso(a);
    const bIso = effectiveIso(b);
    if (aIso && bIso) return aIso < bIso ? 1 : aIso > bIso ? -1 : 0;
    if (aIso) return -1;
    if (bIso) return 1;
    return b.id - a.id;
  });
}

function groupRows(rows: AppChatLibraryRow[]): Array<{ key: BucketKey; rows: AppChatLibraryRow[] }> {
  const todayStart = startOfLocalDay(new Date());
  const buckets = new Map<BucketKey, AppChatLibraryRow[]>();
  for (const row of rows) {
    const key = bucketFor(row, todayStart);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(row);
    else buckets.set(key, [row]);
  }
  return BUCKET_ORDER.filter((key) => buckets.has(key)).map((key) => ({
    key,
    rows: sortWithinBucket(buckets.get(key) as AppChatLibraryRow[]),
  }));
}

function Icon({ name }: { name: "search" | "pencil" | "trash" | "check" | "x" | "chat" }) {
  switch (name) {
    case "search":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m20 20-4.8-4.8" />
        </svg>
      );
    case "pencil":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M14.5 4.5 19.5 9.5 8 21H3v-5Z" />
        </svg>
      );
    case "trash":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 7h16" />
          <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
          <path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" />
        </svg>
      );
    case "check":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="m5 13 5 5 9-11" />
        </svg>
      );
    case "x":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M6 6l12 12M18 6 6 18" />
        </svg>
      );
    case "chat":
      return (
        <svg aria-hidden="true" viewBox="0 0 24 24" className="icon">
          <path d="M4 5h16v11H8l-4 4Z" />
        </svg>
      );
  }
}

export function ChatLibraryDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const [state, setState] = useState<AppChatLibraryState>(initialState);
  const [query, setQuery] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<number | null>(null);
  const renameRef = useRef<HTMLInputElement>(null);
  const deleteCancelRef = useRef<HTMLButtonElement>(null);
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);

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

  useEffect(() => {
    if (confirmingDeleteId !== null) deleteCancelRef.current?.focus();
  }, [confirmingDeleteId]);

  // A republish after delete/rename elsewhere can drop the row a pending
  // confirm/rename targeted - reset-during-render on a revision change,
  // same pattern CommandPalette uses for its own wasOpen tracking.
  const [seenRevision, setSeenRevision] = useState(state.revision);
  if (seenRevision !== state.revision) {
    setSeenRevision(state.revision);
    setConfirmingDeleteId(null);
    setRenamingId(null);
  }

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return state.rows;
    return state.rows.filter((row) => `${row.title} ${row.preview}`.toLowerCase().includes(term));
  }, [query, state.rows]);

  const groups = useMemo(() => groupRows(filtered), [filtered]);

  function loadChat(id: number) {
    transport.intent("app-chat-library", "loadChat", [id]);
    overlays.close();
  }

  function newChat() {
    transport.intent("app-chat-library", "newChat", []);
    overlays.close();
  }

  function startRename(row: AppChatLibraryRow, trigger: HTMLButtonElement) {
    lastTriggerRef.current = trigger;
    setRenamingId(row.id);
    setRenameDraft(row.title);
    setConfirmingDeleteId(null);
  }

  function commitRename() {
    const title = renameDraft.trim();
    if (renamingId === null || !title) return;
    transport.intent("app-chat-library", "renameChat", [renamingId, title]);
    setRenamingId(null);
    lastTriggerRef.current?.focus();
  }

  function cancelRename() {
    setRenamingId(null);
    lastTriggerRef.current?.focus();
  }

  function onRenameKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      commitRename();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  }

  function startDelete(row: AppChatLibraryRow, trigger: HTMLButtonElement) {
    lastTriggerRef.current = trigger;
    setConfirmingDeleteId(row.id);
    setRenamingId(null);
  }

  function confirmDelete() {
    if (confirmingDeleteId === null) return;
    transport.intent("app-chat-library", "deleteChat", [confirmingDeleteId]);
    setConfirmingDeleteId(null);
  }

  function cancelDelete() {
    setConfirmingDeleteId(null);
    lastTriggerRef.current?.focus();
  }

  const total = state.rows.length;
  const resultsAnnouncement =
    total === 0 ? "" : filtered.length === 0 ? "No chats match" : `${filtered.length} results`;

  return (
    <Dialog name="library" title="Chat Library" className="library-dialog">
      <div className="library-shell">
        <div className="library-header">
          {total > 0 && (
            <div className="library-search-wrap">
              <Icon name="search" />
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
            </div>
          )}
          <button type="button" className="library-new-chat-button" onClick={newChat}>
            New Chat
          </button>
        </div>

        <div className="library-visually-hidden" role="status" aria-live="polite">
          {resultsAnnouncement}
        </div>

        {state.notice && (
          <p className="library-notice" role="status">
            {state.notice}
          </p>
        )}

        {total === 0 ? (
          <div className="library-empty-state">
            <Icon name="chat" />
            <p className="library-empty-state-title">No saved chats yet</p>
            <p className="library-empty-state-sub">Start a new chat to begin building your library.</p>
            <button type="button" className="library-new-chat-button" onClick={newChat}>
              New Chat
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="library-search-empty">
            <p>No chats match &quot;{query}&quot;.</p>
            <button type="button" className="library-search-empty-clear" onClick={() => setQuery("")}>
              Clear search
            </button>
          </div>
        ) : (
          <div className="library-groups">
            {groups.map((group) => (
              <section key={group.key} className="library-group" aria-label={group.key}>
                <h3 className="library-group-header">{group.key}</h3>
                <ul className="library-group-rows">
                  {group.rows.map((row) => {
                    const isRenaming = renamingId === row.id;
                    const isConfirmingDelete = confirmingDeleteId === row.id;
                    const messageWord = row.messageCount === 1 ? "message" : "messages";
                    return (
                      <li key={row.id} className="library-row">
                        {isRenaming ? (
                          <div className="library-row-primary">
                            <input
                              ref={renameRef}
                              className="library-row-rename-input"
                              type="text"
                              value={renameDraft}
                              onChange={(event) => setRenameDraft(event.target.value)}
                              onKeyDown={onRenameKeyDown}
                              aria-label={`Rename "${row.title}"`}
                              autoComplete="off"
                              spellCheck={false}
                            />
                            <p className="library-row-preview hint">Enter to save · Esc to cancel</p>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="library-row-primary"
                            aria-label={`Open chat "${row.title}", ${row.messageCount} ${messageWord}, updated ${row.updatedLabel}`}
                            onClick={() => loadChat(row.id)}
                          >
                            <span className="library-row-title" title={row.title}>
                              {row.title}
                            </span>
                            {row.preview ? (
                              <span className="library-row-preview">{row.preview}</span>
                            ) : (
                              <span className="library-row-preview placeholder">No messages yet</span>
                            )}
                          </button>
                        )}

                        {!isRenaming && !isConfirmingDelete && row.messageCount > 0 && (
                          <span className="library-row-count">{row.messageCount}</span>
                        )}

                        {isRenaming ? (
                          <div className="library-row-actions">
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={`Save "${row.title}"`}
                              onClick={commitRename}
                              disabled={renameDraft.trim().length === 0}
                            >
                              <Icon name="check" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label="Cancel rename"
                              onClick={cancelRename}
                            >
                              <Icon name="x" />
                            </button>
                          </div>
                        ) : isConfirmingDelete ? (
                          <div
                            className="library-row-confirm"
                            onKeyDown={(event) => {
                              if (event.key === "Escape") {
                                event.preventDefault();
                                cancelDelete();
                              }
                            }}
                          >
                            <span className="library-row-confirm-label">Delete?</span>
                            <button
                              type="button"
                              className="library-icon-button library-icon-button-confirm-delete"
                              aria-label={`Confirm delete "${row.title}"`}
                              onClick={confirmDelete}
                            >
                              <Icon name="check" />
                            </button>
                            <button
                              ref={deleteCancelRef}
                              type="button"
                              className="library-icon-button"
                              aria-label="Cancel delete"
                              onClick={cancelDelete}
                            >
                              <Icon name="x" />
                            </button>
                          </div>
                        ) : (
                          <div className="library-row-actions">
                            <button
                              type="button"
                              className="library-icon-button"
                              aria-label={`Rename "${row.title}"`}
                              onClick={(event) => startRename(row, event.currentTarget)}
                            >
                              <Icon name="pencil" />
                            </button>
                            <button
                              type="button"
                              className="library-icon-button library-icon-button-delete"
                              aria-label={`Delete "${row.title}"`}
                              onClick={(event) => startDelete(row, event.currentTarget)}
                            >
                              <Icon name="trash" />
                            </button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}
