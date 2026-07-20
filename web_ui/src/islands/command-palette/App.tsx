import { useEffect, useMemo, useRef, useState } from "react";
import { CommandPaletteState, initialCommandPaletteState } from "./bridgeTypes";
import { BridgeRejection, CommandPaletteBridge, createCommandPaletteBridge } from "./bridge";
import { CommandPaletteErrorState } from "./CommandPaletteErrorState";

function App() {
  const [state, setState] = useState<CommandPaletteState>(initialCommandPaletteState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const bridgeRef = useRef<CommandPaletteBridge | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const bridge = createCommandPaletteBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  // Every genuine (re)open (Python's visible flips false -> true) resets the
  // search box and selection - done during render (React's documented
  // "adjusting state when a prop changes" pattern), not in an effect, per
  // this island family's established set-state-in-effect lint fix. Keyed
  // specifically on the false->true transition, not on every publish: a
  // stale-command republish (see bridge module docs) keeps visible=true
  // throughout and must NOT wipe whatever the user is still typing.
  const [wasVisible, setWasVisible] = useState(state.visible);
  if (state.visible !== wasVisible) {
    setWasVisible(state.visible);
    if (state.visible) {
      setQuery("");
      setSelectedIndex(0);
    }
  }

  // The "no longer available" notice is shown once per Python revision, then
  // cleared locally the moment the user types again - Python has no way to
  // know the user started a new search, so JS owns dismissing its own copy.
  const [visibleNotice, setVisibleNotice] = useState<string | null>(null);
  const [noticeRevision, setNoticeRevision] = useState(state.revision);
  if (noticeRevision !== state.revision) {
    setNoticeRevision(state.revision);
    setVisibleNotice(state.notice ?? null);
  }

  useEffect(() => {
    if (state.visible) inputRef.current?.focus();
  }, [state.visible]);

  const filtered = useMemo(() => {
    const term = query.toLowerCase().trim();
    if (!term) return state.commands;
    return state.commands.filter((command) =>
      command.aliases.some((alias) => alias.includes(term)),
    );
  }, [query, state.commands]);

  const clampedIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));

  if (rejection) {
    return <CommandPaletteErrorState rejection={rejection} />;
  }

  function onQueryChange(event: React.ChangeEvent<HTMLInputElement>) {
    setQuery(event.target.value);
    setSelectedIndex(0);
    setVisibleNotice(null);
  }

  function execute(id: string) {
    bridgeRef.current?.executeCommand(id);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((index) => Math.min(index + 1, Math.max(filtered.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const command = filtered[clampedIndex];
      if (command) execute(command.id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      bridgeRef.current?.dismiss();
    }
  }

  return (
    <main
      className="palette-shell"
      style={{ display: state.visible ? undefined : "none" }}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <input
        ref={inputRef}
        className="palette-search-input"
        type="text"
        value={query}
        onChange={onQueryChange}
        onKeyDown={onKeyDown}
        placeholder="Type a command..."
        aria-label="Search commands"
        autoComplete="off"
        spellCheck={false}
      />

      {visibleNotice && (
        <p className="palette-notice" role="status">
          {visibleNotice}
        </p>
      )}

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
    </main>
  );
}

export default App;
