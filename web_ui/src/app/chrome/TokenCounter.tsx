import { useSyncExternalStore } from "react";
import { OverlayProviderIfMissing, Popover, useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import type { ComposerStore } from "./composerStore";

/** A single, keyboard-accessible overlay shares the app popover layer. */
export function TokenCounter({ store }: { store: ComposerStore }) {
  return (
    <OverlayProviderIfMissing>
      <TokenCounterSurface store={store} />
    </OverlayProviderIfMissing>
  );
}
function TokenCounterSurface({ store }: { store: ComposerStore }) {
  const counter = useSyncExternalStore(store.subscribe, store.getTokenCounter);
  const overlays = useOverlays();
  const name = "composer-token-usage";
  const expanded = overlays.isOpen(name);
  const usageLabel = counter.usageIsReal ? "exact" : "estimated";
  return (
    <span className="token-counter-trigger">
      <Button variant="ghost" size="sm" className="token-counter-summary"
        data-overlay-trigger={name} aria-haspopup="dialog" aria-expanded={expanded}
        aria-label={`Token usage: ${counter.totalTokens} total`}
        onMouseEnter={() => { if (!expanded) overlays.open(name, "popover"); }}
        onMouseLeave={() => { if (expanded) overlays.closeLayer(name); }}
        onClick={() => overlays.toggle(name, "popover")}>
        <span className="composer-control-value">{counter.totalTokens}</span>
        <span className="composer-control-label">tokens</span>
      </Button>
      <Popover anchored name={name} label="Token usage breakdown">
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
          {counter.promptTokens != null && (
            <span className="token-counter-row">
              <span className="token-counter-label">Prompt (exact)</span>
              <span className="token-counter-value">{counter.promptTokens}</span>
            </span>
          )}
          {counter.completionTokens != null && (
            <span className="token-counter-row">
              <span className="token-counter-label">Completion (exact)</span>
              <span className="token-counter-value">{counter.completionTokens}</span>
            </span>
          )}
          <span className="token-counter-row token-counter-total">
            <span className="token-counter-label">Total ({usageLabel})</span>
            <span className="token-counter-value">{counter.totalTokens}</span>
          </span>
          {counter.estimatedCostUsd != null && (
            <span className="token-counter-row">
              <span className="token-counter-label">Est. cost</span>
              <span className="token-counter-value">
                {`$${counter.estimatedCostUsd.toFixed(4)}`}
              </span>
            </span>
          )}
        </div>
      </Popover>
    </span>
  );
}
