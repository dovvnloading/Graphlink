import { useEffect, useRef, useState } from "react";
import { BridgeRejection, SearchOverlayBridge, createSearchOverlayBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [currentIndex, setCurrentIndex] = useState(-1);
  const [totalMatches, setTotalMatches] = useState(0);
  const bridgeRef = useRef<SearchOverlayBridge | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const bridge = createSearchOverlayBridge(
      (state) => {
        setCurrentIndex(state.currentIndex);
        setTotalMatches(state.totalMatches);
      },
      setRejection,
    );
    bridgeRef.current = bridge;
    bridge.ready();
    inputRef.current?.focus();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Search is unavailable"
        rejection={rejection}
        className="search-overlay-shell bridge-error"
      />
    );
  }

  function onChange(event: React.ChangeEvent<HTMLInputElement>) {
    bridgeRef.current?.search(event.target.value);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && event.shiftKey) {
      event.preventDefault();
      bridgeRef.current?.previous();
    } else if (event.key === "Enter") {
      event.preventDefault();
      bridgeRef.current?.next();
    } else if (event.key === "Escape") {
      event.preventDefault();
      bridgeRef.current?.close();
    }
  }

  // Matches the legacy SearchOverlay.update_results_label's exact 3-way
  // branch: "current" is 1-based once a match is focused, 0 right after a
  // fresh search - (currentIndex + 1) reproduces that with currentIndex's
  // own -1 "no current match" sentinel folding to 0 automatically.
  const current = currentIndex + 1;
  const tone = totalMatches === 0 ? "error" : current > 0 ? "active" : "idle";

  return (
    <div className="search-overlay-shell">
      <input
        ref={inputRef}
        className="search-overlay-input"
        type="text"
        onChange={onChange}
        onKeyDown={onKeyDown}
        placeholder="Find..."
        aria-label="Search the canvas"
        autoComplete="off"
        spellCheck={false}
      />
      <span className="search-overlay-count" data-tone={tone}>
        {current} / {totalMatches}
      </span>
      <button
        type="button"
        className="search-overlay-icon-btn"
        onClick={() => bridgeRef.current?.previous()}
        aria-label="Previous match (Shift+Enter)"
      >
        ▲
      </button>
      <button
        type="button"
        className="search-overlay-icon-btn"
        onClick={() => bridgeRef.current?.next()}
        aria-label="Next match (Enter)"
      >
        ▼
      </button>
      <button
        type="button"
        className="search-overlay-icon-btn"
        onClick={() => bridgeRef.current?.close()}
        aria-label="Close (Esc)"
      >
        ×
      </button>
    </div>
  );
}

export default App;
