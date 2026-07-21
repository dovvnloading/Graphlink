import { ComposerContextState, initialComposerContextState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateComposerContextState } from "../../lib/bridge-core/generated/composer-context-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: ComposerContextState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtComposerContextObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  removeContextItem: (id: string) => void;
  resize: (height: number) => void;
  close: () => void;
}

export interface ComposerContextBridge {
  ready(): void;
  removeContextItem(id: string): void;
  resize(height: number): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateComposerContextState);
}

class MockComposerContextBridge implements ComposerContextBridge {
  private readonly state: ComposerContextState = structuredClone(initialComposerContextState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  removeContextItem(): void {}
  resize(): void {}
  close(): void {}
  dispose(): void {}
}

export function createComposerContextBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): ComposerContextBridge {
  const fallback = new MockComposerContextBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtComposerContextObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[composer-context bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.composerContextBridge as QtComposerContextObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtComposerContextObject>(
    method: K,
    ...args: QtComposerContextObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    removeContextItem: (id) => call("removeContextItem", id),
    resize: (height) => call("resize", height),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
