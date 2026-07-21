import { SearchOverlayState, initialSearchOverlayState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { installTextFocusReporting } from "../../lib/bridge-core/textFocus";
import { validateSearchOverlayState } from "../../lib/bridge-core/generated/search-overlay-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: SearchOverlayState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtSearchOverlayObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  search: (text: string) => void;
  next: () => void;
  previous: () => void;
  close: () => void;
}

export interface SearchOverlayBridge {
  ready(): void;
  search(text: string): void;
  next(): void;
  previous(): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateSearchOverlayState);
}

class MockSearchOverlayBridge implements SearchOverlayBridge {
  private readonly state: SearchOverlayState = structuredClone(initialSearchOverlayState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  search(): void {}
  next(): void {}
  previous(): void {}
  close(): void {}
  dispose(): void {}
}

export function createSearchOverlayBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): SearchOverlayBridge {
  const fallback = new MockSearchOverlayBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtSearchOverlayObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[search-overlay bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.searchOverlayBridge as QtSearchOverlayObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    // A real text input (the search box) means this island must
    // participate in the keyboard-arbitration protocol, like command-palette.
    installTextFocusReporting(objects);
  });

  const call = <K extends keyof QtSearchOverlayObject>(
    method: K,
    ...args: QtSearchOverlayObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    search: (text) => call("search", text),
    next: () => call("next"),
    previous: () => call("previous"),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
