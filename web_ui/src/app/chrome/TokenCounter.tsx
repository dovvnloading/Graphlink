import { useState } from "react";
import { useSyncExternalStore } from "react";
import type { ComposerStore } from "./composerStore";

/**
 * Token usage (R8a follow-up) - a hover-triggered fold-out attached to the
 * composer's own control row, replacing what used to be a standalone
 * floating widget pinned to the canvas's bottom-left corner. That widget
 * had no relationship to anything else on screen - an island, per the
 * user's own description - and (before the App.tsx layout fix alongside
 * it) didn't even know to get out of the way when Document View's docked
 * panel opened. Folding it into the composer removes both problems at
 * once: it's now part of the SAME control row as Reasoning/Model, so it
 * reflows with everything else the composer already reflows with, and it
 * only ever shows a compact total until hovered.
 */
export function TokenCounter({ store }: { store: ComposerStore }) {
  const counter = useSyncExternalStore(store.subscribe, store.getTokenCounter);
  const [hovered, setHovered] = useState(false);

  return (
    <div
      className="token-counter-trigger"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        type="button"
        className="composer-control token-counter-summary"
        aria-haspopup="true"
        aria-expanded={hovered}
        aria-label={`Token usage: ${counter.totalTokens} total`}
      >
        <span className="control-kicker">Tokens</span>
        <span className="control-value">{counter.totalTokens}</span>
      </button>
      {hovered && (
        <div className="token-counter-popout" role="status" aria-label="Token usage breakdown">
          <span className="token-counter-row">
            <span className="token-counter-label">Input</span>
            <span className="token-counter-value">{counter.inputTokens}</span>
          </span>
          <span className="token-counter-row">
            <span className="token-counter-label">Output</span>
            <span className="token-counter-value">{counter.outputTokens}</span>
          </span>
          <span className="token-counter-row">
            <span className="token-counter-label">Context</span>
            <span className="token-counter-value">{counter.contextTokens}</span>
          </span>
          <span className="token-counter-row token-counter-total">
            <span className="token-counter-label">Total</span>
            <span className="token-counter-value">{counter.totalTokens}</span>
          </span>
        </div>
      )}
    </div>
  );
}
