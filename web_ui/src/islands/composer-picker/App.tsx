import { useEffect, useMemo, useRef, useState } from "react";
import { ComposerPickerState, initialComposerPickerState } from "./bridgeTypes";
import { BridgeRejection, ComposerPickerBridge, createComposerPickerBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<ComposerPickerState>(initialComposerPickerState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [query, setQuery] = useState("");
  const [lastOpenToken, setLastOpenToken] = useState(0);
  const bridgeRef = useRef<ComposerPickerBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const bridge = createComposerPickerBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  // A fresh open() call bumps openToken server-side - reset the local
  // search query whenever that happens, during render rather than an
  // effect (the same "reset local state when a fresh X begins" pattern
  // pin-overlay's own draft editor already uses) - a stale query from the
  // LAST time this surface was open must not silently filter whatever kind
  // just opened.
  if (state.openToken !== lastOpenToken) {
    setLastOpenToken(state.openToken);
    setQuery("");
  }

  useEffect(() => {
    searchRef.current?.focus();
  }, [state.openToken]);

  // Content-driven height negotiation, matching PinOverlayHost's own
  // ResizeObserver-based reporting.
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

  // Escape closes from anywhere in the surface, matching the legacy native
  // popup's own keyPressEvent override - outside-click-close itself is
  // handled natively (WebIslandHost's dismiss_on_outside_focus).
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") bridgeRef.current?.close();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const term = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!term) return state.options;
    return state.options.filter(
      (option) => option.label.toLowerCase().includes(term) || option.id.toLowerCase().includes(term),
    );
  }, [term, state.options]);

  if (rejection) {
    return (
      <BridgeErrorState
        title="The picker is unavailable"
        rejection={rejection}
        className="composer-picker-shell bridge-error"
      />
    );
  }

  const isModelKind = state.kind === "model";
  const showSettingsHint = isModelKind && term.length === 0 && state.options.length === 0;
  const emptyMessage =
    term.length > 0
      ? "No model matches this search."
      : isModelKind
        ? "No model catalog available yet."
        : "No reasoning levels are available for this provider.";

  return (
    <div className="composer-picker-shell" ref={shellRef}>
      <div className="composer-picker-header">
        <div className="composer-picker-heading">
          <p className="composer-picker-eyebrow">{isModelKind ? "Model" : "Reasoning"}</p>
          <p className="composer-picker-title">{state.title}</p>
        </div>
        <button
          type="button"
          className="composer-picker-close-btn"
          onClick={() => bridgeRef.current?.close()}
          aria-label="Close selector"
        >
          &times;
        </button>
      </div>

      {isModelKind && (
        <input
          ref={searchRef}
          className="composer-picker-search-input"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search available models"
          aria-label="Search available models"
          autoComplete="off"
          spellCheck={false}
        />
      )}

      {filtered.length > 0 ? (
        <ul
          className="composer-picker-list"
          role="listbox"
          aria-label={isModelKind ? "Model options" : "Reasoning options"}
        >
          {filtered.map((option) => (
            <li key={option.id} role="option" aria-selected={option.current}>
              <button
                type="button"
                className={"composer-picker-row" + (option.unavailable ? " unavailable" : "")}
                onClick={() => !option.unavailable && bridgeRef.current?.selectOption(option.id)}
                disabled={option.unavailable}
              >
                <span className="composer-picker-row-copy">
                  <span className="composer-picker-row-label">{option.label}</span>
                  <span className="composer-picker-row-meta">{option.meta}</span>
                </span>
                {option.current && <span className="composer-picker-current-badge">Current</span>}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="composer-picker-empty">{emptyMessage}</p>
      )}

      {showSettingsHint && (
        <button
          type="button"
          className="composer-picker-settings-btn"
          onClick={() => bridgeRef.current?.requestSettings()}
        >
          Open Settings to discover models
        </button>
      )}
    </div>
  );
}

export default App;
