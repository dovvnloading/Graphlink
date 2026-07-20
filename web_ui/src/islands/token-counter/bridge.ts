import { TokenCounterState, initialTokenCounterState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateTokenCounterState } from "../../lib/bridge-core/generated/token-counter-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: TokenCounterState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtTokenCounterObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
}

export interface TokenCounterBridge {
  ready(): void;
  dispose(): void;
}

/**
 * Read-only display, the simplest of the three interaction shapes Phase 2's
 * pilot islands cover: no intents flow back to Python, so this bridge is
 * just parse-state-in + ready()/dispose() - no `call()` dispatcher, unlike
 * composer's bridge.ts, since there is nothing here to dispatch.
 */
function parseState(payload: string) {
  return parseIslandState(payload, validateTokenCounterState);
}

class MockTokenCounterBridge implements TokenCounterBridge {
  private state: TokenCounterState = structuredClone(initialTokenCounterState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  dispose(): void {}
}

export function createTokenCounterBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): TokenCounterBridge {
  const fallback = new MockTokenCounterBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtTokenCounterObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[token-counter bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.tokenCounterBridge as QtTokenCounterObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  return {
    ready: () => {
      if (connected && remote) remote.ready();
    },
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
