import { useEffect, useMemo, useRef, useState } from "react";
import type { AppChatLibraryState } from "../../lib/bridge-core/generated/app-chat-library-state";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { WsTransport } from "../../lib/ws/transport";
import { useOverlays } from "../overlays/overlays";
import { fuzzyFilterAndSort } from "./quickSwitcherFuzzy";

const initialState: AppChatLibraryState = { schemaVersion: 0, revision: 0, rows: [], workspaces: [] };

/**
 * ADR-020 stage 20.5: the quick switcher (`Ctrl+P`-class) - "recent graphs,
 * fuzzy title match, jump," per this stage's own design doc note (ADR-020's
 * own "4. A phrase in a node... is found and focused" section). A direct
 * sibling of CommandPalette.tsx, not CommandPalette itself repurposed: same
 * hand-rolled overlay-scrim + palette-shell/palette-search-input/palette-
 * results/palette-result markup, same reset-on-open-transition pattern, same
 * ArrowUp/ArrowDown/Enter keyboard contract - but the palette filters a
 * fixed, in-memory PaletteCommand[] built fresh every render, while this
 * filters a LIVE server-pushed list of graphs (this app's own "app-chat-
 * library" topic, the same snapshot ChatLibraryDialog.tsx already renders
 * as the real library UI) and its own action is loadChat, not command.run().
 *
 * Subscribes to "app-chat-library" independently of ChatLibraryDialog - see
 * WsTransport.subscribe's own Set<listener>-per-topic shape: multiple
 * independent subscribers to the same topic is the ordinary, supported
 * case (ChatLibraryDialog is usually unmounted anyway, per App.tsx's own
 * LazySurface - a lazy chrome dialog mounts only once its own overlay
 * surface has genuinely been opened, so this component cannot assume
 * ChatLibraryDialog's subscription is even alive to piggyback on).
 *
 * Archived graphs are excluded, matching ChatLibraryDialog's own default
 * (non-archived) view - a quick "jump to a recent graph" surface has no use
 * for a graph the user explicitly archived. `rows` already arrives sorted
 * updated_at DESC (backend/chat_library.py's own get_all_chats), so a blank
 * query - fuzzyFilterAndSort's own "empty query ties everything, stable
 * sort" contract - shows the most-recently-touched graphs first with zero
 * extra sorting here.
 */
export function QuickSwitcherDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const [state, setState] = useState<AppChatLibraryState>(initialState);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLUListElement>(null);
  const isOpen = overlays.isOpen("quick-switcher");

  useEffect(() => {
    const unsubscribe = transport.subscribe("app-chat-library", (payload) => {
      const validated = TOPIC_VALIDATORS["app-chat-library"](payload);
      if (validated.ok) setState(validated.value as AppChatLibraryState);
      else console.error("[app-chat-library] rejected snapshot:", validated.errors);
    });
    // WsTransport.subscribe() only sends a real wire-level "subscribe" (and
    // so only gets a fresh snapshot back) for a topic's first-ever listener
    // - this component is mounted unconditionally (see App.tsx), so it
    // would otherwise silently "steal" that first-subscriber slot from
    // whichever chrome surface subscribes to "app-chat-library" next (e.g.
    // ChatLibraryDialog.tsx's own lazy-mounted subscription - see that
    // file's own identical resubscribe() call and comment for the
    // observed failure this closes: "No saved chats yet" with a real,
    // populated library). resubscribe() forces a fresh snapshot
    // unconditionally, making this independent of subscribe ORDER between
    // the two dialogs.
    transport.resubscribe("app-chat-library");
    return unsubscribe;
  }, [transport]);

  // Reset local state during render on the false->true transition - same
  // "not in an effect" posture as CommandPalette.tsx's own wasOpen tracking.
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setQuery("");
      setSelectedIndex(0);
    }
  }

  useEffect(() => {
    if (isOpen) inputRef.current?.focus();
  }, [isOpen]);

  const workspaceNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const workspace of state.workspaces) map.set(workspace.id, workspace.name);
    return map;
  }, [state.workspaces]);

  const candidates = useMemo(() => state.rows.filter((row) => !row.archived), [state.rows]);
  const filtered = useMemo(
    () => fuzzyFilterAndSort(query, candidates, (row) => row.title),
    [candidates, query],
  );

  const clampedIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));

  useEffect(() => {
    resultsRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [clampedIndex]);

  if (!isOpen) return null;

  function jumpTo(id: number) {
    overlays.close();
    transport.fireIntent("app-chat-library", "loadChat", [id]);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const row = filtered[clampedIndex];
      if (row) jumpTo(row.id);
    }
    // Escape is handled globally by the overlay system.
  }

  return (
    <div className="overlay-scrim" onPointerDown={(e) => e.target === e.currentTarget && overlays.close()}>
      <div role="dialog" aria-modal="true" aria-label="Quick switcher" className="palette-shell">
        <input
          ref={inputRef}
          className="palette-search-input"
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Jump to a graph…"
          aria-label="Search graphs by title"
          autoComplete="off"
          spellCheck={false}
        />
        <ul className="palette-results" role="listbox" aria-label="Graphs" ref={resultsRef}>
          {filtered.length === 0 && <li className="palette-empty">No matching graphs</li>}
          {filtered.map((row, index) => (
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events
            <li
              key={row.id}
              role="option"
              aria-selected={index === clampedIndex}
              className={"palette-result" + (index === clampedIndex ? " selected" : "")}
              onMouseEnter={() => setSelectedIndex(index)}
              onClick={() => jumpTo(row.id)}
            >
              <span className="quick-switcher-result-title">{row.title}</span>
              <span className="quick-switcher-result-meta">
                {workspaceNameById.get(row.workspaceId) ?? "Default"} · {row.updatedLabel}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
