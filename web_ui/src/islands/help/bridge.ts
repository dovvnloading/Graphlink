import { HelpState, initialHelpState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateHelpState } from "../../lib/bridge-core/generated/help-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: HelpState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtHelpObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  close: () => void;
}

export interface HelpBridge {
  ready(): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateHelpState);
}

class MockHelpBridge implements HelpBridge {
  private readonly state: HelpState = structuredClone(initialHelpState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  close(): void {
    // No real top-level window to close in the mock/test environment.
  }

  dispose(): void {}
}

export function createHelpBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): HelpBridge {
  const fallback = new MockHelpBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtHelpObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[help bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.helpBridge as QtHelpObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtHelpObject>(
    method: K,
    ...args: QtHelpObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
