import { AboutState, initialAboutState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateAboutState } from "../../lib/bridge-core/generated/about-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: AboutState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtAboutObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  close: () => void;
  openExternal: (url: string) => void;
}

export interface AboutBridge {
  ready(): void;
  close(): void;
  openExternal(url: string): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateAboutState);
}

class MockAboutBridge implements AboutBridge {
  private readonly state: AboutState = structuredClone(initialAboutState);
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

  openExternal(): void {
    // No real browser to open in the mock/test environment - a no-op,
    // same treatment every other island gives a native-only action with
    // no dev-mode stand-in (e.g. settings' pickLlamaCppChatModelFile).
    // Fewer params than the interface declares is valid TS here (a class
    // method implementation may ignore trailing arguments).
  }

  dispose(): void {}
}

export function createAboutBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): AboutBridge {
  const fallback = new MockAboutBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtAboutObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[about bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.aboutBridge as QtAboutObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtAboutObject>(
    method: K,
    ...args: QtAboutObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    close: () => call("close"),
    openExternal: (url) => call("openExternal", url),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
