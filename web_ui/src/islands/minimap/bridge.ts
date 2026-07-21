import { MinimapState, initialMinimapState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateMinimapState } from "../../lib/bridge-core/generated/minimap-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: MinimapState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtMinimapObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  selectNode: (id: string) => void;
}

export interface MinimapBridge {
  ready(): void;
  selectNode(id: string): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateMinimapState);
}

class MockMinimapBridge implements MinimapBridge {
  private readonly state: MinimapState = structuredClone(initialMinimapState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  selectNode(): void {}
  dispose(): void {}
}

export function createMinimapBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): MinimapBridge {
  const fallback = new MockMinimapBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtMinimapObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[minimap bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.minimapBridge as QtMinimapObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtMinimapObject>(
    method: K,
    ...args: QtMinimapObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    selectNode: (id) => call("selectNode", id),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
