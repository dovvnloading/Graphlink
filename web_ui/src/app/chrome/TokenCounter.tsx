import { useSyncExternalStore } from "react";
import type { ComposerStore } from "./composerStore";

export function TokenCounter({ store }: { store: ComposerStore }) {
  const counter = useSyncExternalStore(store.subscribe, store.getTokenCounter);
  return (
    <div className="token-counter" aria-label="Token usage">
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
  );
}
