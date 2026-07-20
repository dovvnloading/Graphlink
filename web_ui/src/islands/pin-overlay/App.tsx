import { useEffect, useMemo, useRef, useState } from "react";
import { PinOverlayState, initialPinOverlayState } from "./bridgeTypes";
import { BridgeRejection, PinOverlayBridge, createPinOverlayBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<PinOverlayState>(initialPinOverlayState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [query, setQuery] = useState("");
  const bridgeRef = useRef<PinOverlayBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

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
