import { useEffect, useRef, useState } from "react";
import { ComposerContextState, initialComposerContextState } from "./bridgeTypes";
import { BridgeRejection, ComposerContextBridge, createComposerContextBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<ComposerContextState>(initialComposerContextState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<ComposerContextBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bridge = createComposerContextBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

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

  if (rejection) {
    return (
      <BridgeErrorState
        title="Context review is unavailable"
        rejection={rejection}
        className="composer-context-shell bridge-error"
      />
    );
  }

  const hasRows = state.anchor !== null || state.items.length > 0;

  return (
    <div className="composer-context-shell" ref={shellRef}>
      <div className="composer-context-header">
        <div className="composer-context-heading">
          <p className="composer-context-eyebrow">Context</p>
          <p className="composer-context-title">Included context</p>
        </div>
        <button
          type="button"
          className="composer-context-close-btn"
          onClick={() => bridgeRef.current?.close()}
          aria-label="Close context review"
        >
          &times;
        </button>
      </div>

      {hasRows && (
        <ul className="composer-context-list" aria-label="Included context items">
          {state.anchor && (
            <li className="composer-context-row">
              <span className="composer-context-kind">{state.anchor.type || "Graph"}</span>
              <span className="composer-context-name" title={state.anchor.label}>
                {state.anchor.label}
              </span>
            </li>
          )}
          {state.items.map((item) => (
            <li key={item.id} className="composer-context-row">
              <span className="composer-context-kind">{item.kind}</span>
              <span className="composer-context-name" title={item.name}>
                {item.name}
              </span>
              <button
                type="button"
                className="composer-context-remove"
                onClick={() => bridgeRef.current?.removeContextItem(item.id)}
                aria-label={`Remove ${item.name}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="composer-context-total">Estimated context · {state.totalTokens.toLocaleString()} tokens</p>
    </div>
  );
}

export default App;
