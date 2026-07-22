import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { useOverlays } from "../overlays/overlays";
import { buildCommands } from "./commands";

/**
 * The command palette (Qt-removal plan R2.4) - command-palette island's
 * successor. Opens via the overlay system ("palette", dialog tier, so it
 * gets a scrim + focus trap for free) - Ctrl/Cmd+K is the conventional
 * trigger, wired in App.tsx as a document-level shortcut.
 */
export function CommandPalette({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const rf = useReactFlow();
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const isOpen = overlays.isOpen("palette");

  // Reset local state during render on the false->true transition (not in
  // an effect - the react-hooks/set-state-in-effect rule, and the same
  // pattern the legacy command-palette island's own wasVisible tracking
  // used for exactly this "reset on fresh open" need).
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

  const commands = useMemo(() => buildCommands(store, rf, overlays), [store, rf, overlays]);

  const filtered = useMemo(() => {
    const term = query.toLowerCase().trim();
    const candidates = commands.filter((c) => c.enabled());
    if (!term) return candidates;
    return candidates.filter(
      (c) => c.name.toLowerCase().includes(term) || c.aliases.some((a) => a.includes(term)),
    );
  }, [commands, query]);

  const clampedIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));

  if (!isOpen) return null;

  function execute(id: string) {
    const command = commands.find((c) => c.id === id);
    command?.run();
    overlays.close();
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
      const command = filtered[clampedIndex];
      if (command) execute(command.id);
    }
    // Escape is handled globally by the overlay system.
  }

  return (
    <div className="overlay-scrim" onPointerDown={(e) => e.target === e.currentTarget && overlays.close()}>
      <div role="dialog" aria-modal="true" aria-label="Command palette" className="palette-shell">
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
          placeholder="Type a command…"
          aria-label="Search commands"
          autoComplete="off"
          spellCheck={false}
        />
        <ul className="palette-results" role="listbox" aria-label="Commands">
          {filtered.length === 0 && <li className="palette-empty">No matching commands</li>}
          {filtered.map((command, index) => (
            <li
              key={command.id}
              role="option"
              aria-selected={index === clampedIndex}
              className={"palette-result" + (index === clampedIndex ? " selected" : "")}
              onMouseEnter={() => setSelectedIndex(index)}
              onClick={() => execute(command.id)}
            >
              {command.name}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
