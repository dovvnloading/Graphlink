import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { AppChatLibraryState } from "../../lib/bridge-core/generated/app-chat-library-state";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { WsTransport } from "../../lib/ws/transport";
import { Dialog, useOverlays } from "../overlays/overlays";
import { useConfirm } from "../ui/ConfirmDialog";
import { EmptyState } from "../ui/EmptyState";
import { TextField } from "../ui/TextField";
import { Kbd } from "../ui/Kbd";
import { Icon } from "../ui/icons/Icon";
import { registerNewChatConfirm } from "./requestNewChat";
import { fuzzyFilterAndSort } from "./quickSwitcherFuzzy";
import "./chat-library.css";

const initialState: AppChatLibraryState = { schemaVersion: 0, revision: 0, rows: [], workspaces: [] };

/** The eagerly-mounted switcher owns the same live topic as the lazy library. */
export function QuickSwitcherDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const askConfirm = useConfirm();
  const [state, setState] = useState<AppChatLibraryState>(initialState);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const resultsRef = useRef<HTMLUListElement>(null);
  const isOpen = overlays.isOpen("quick-switcher");

  // Global palette/shortcut callers cannot use hooks. They obtain the same
  // nested kit confirmation from this always-mounted component.
  useEffect(() => registerNewChatConfirm(askConfirm), [askConfirm]);

  useEffect(() => {
    const unsubscribe = transport.subscribe("app-chat-library", (payload) => {
      const validated = TOPIC_VALIDATORS["app-chat-library"](payload);
      if (validated.ok) setState(validated.value as AppChatLibraryState);
      else console.error("[app-chat-library] rejected snapshot:", validated.errors);
    });
    transport.resubscribe("app-chat-library");
    return unsubscribe;
  }, [transport]);

  const [wasOpen, setWasOpen] = useState(isOpen);
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setQuery("");
      setSelectedIndex(0);
    }
  }

  const workspaceNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const workspace of state.workspaces) map.set(workspace.id, workspace.name);
    return map;
  }, [state.workspaces]);

  const candidates = useMemo(() => state.rows.filter((row) => !row.archived), [state.rows]);
  const filtered = useMemo(() => fuzzyFilterAndSort(query, candidates, (row) => row.title), [candidates, query]);
  const clampedIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));

  useEffect(() => {
    if (isOpen) resultsRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [isOpen, clampedIndex]);

  async function jumpTo(id: number) {
    try {
      const outcome = await transport.request("app-chat-library", "loadChat", [id]);
      if (outcome === "discard-required") {
        const accepted = await askConfirm({
          title: "Discard incomplete chat?",
          body: "This graph contains missing or unreadable plugin nodes. Changes to the incomplete on-screen copy will not be saved. The original saved graph remains unchanged.",
          confirmLabel: "Discard and switch",
          destructive: true,
        });
        if (!accepted) return;
        const retry = await transport.request("app-chat-library", "loadChat", [id, true]);
        if (retry === "discard-required") return;
      }
      overlays.close();
    } catch (error) {
      console.error("[quick-switcher] Could not switch graphs:", error);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((index) => Math.min(index + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const row = filtered[clampedIndex];
      if (row) void jumpTo(row.id);
    }
  }

  return (
    <Dialog {...{ title: "Quick switcher" }} name="quick-switcher" size="md" className="quick-switcher-dialog">
      <TextField
        label="Search graphs by title"
        type="search"
        prefixIcon="search"
        suffix={{ type: "clear" }}
        value={query}
        onValueChange={(value) => { setQuery(value); setSelectedIndex(0); }}
        placeholder="Jump to a graph…"
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded="true"
        aria-controls="quick-switcher-results"
        aria-activedescendant={filtered.length ? "quick-switcher-option-" + filtered[clampedIndex].id : undefined}
        autoComplete="off"
        spellCheck={false}
      />
      {filtered.length === 0 ? (
        <EmptyState icon="search" {...{ title: "No matching graphs" }} body="Try a different title or open the chat library." />
      ) : (
        <ul id="quick-switcher-results" className="quick-switcher-results" role="listbox" aria-label="Graphs" ref={resultsRef}>
          {filtered.map((row, index) => {
            const stamp = row.updatedLabel;
            return (
              // Focus stays in the combobox; aria-activedescendant follows the highlighted option.
              // eslint-disable-next-line jsx-a11y/click-events-have-key-events
              <li
                key={row.id}
                id={"quick-switcher-option-" + row.id}
                role="option"
                aria-selected={index === clampedIndex}
                className={"quick-switcher-result" + (index === clampedIndex ? " is-selected" : "")}
                onMouseEnter={() => setSelectedIndex(index)}
                onClick={() => { void jumpTo(row.id); }}
              >
                <Icon name="chat" size="md" />
                <span className="quick-switcher-result-copy">
                  <span className="quick-switcher-result-title">{row.title}</span>
                  <span className="quick-switcher-result-meta">
                    {workspaceNameById.get(row.workspaceId) ?? "Default"} · {stamp}
                  </span>
                </span>
                {row.favorite && <Icon name="star-filled" size="sm" />}
              </li>
            );
          })}
        </ul>
      )}
      <div className="quick-switcher-shortcuts">
        <span>Navigate <Kbd shortcut="Up" /> <Kbd shortcut="Down" /></span>
        <span>Open <Kbd shortcut="Enter" /></span>
        <span>Close <Kbd shortcut="Esc" /></span>
      </div>
    </Dialog>
  );
}
