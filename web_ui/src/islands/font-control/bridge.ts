import { FontControlState, initialFontControlState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateFontControlState } from "../../lib/bridge-core/generated/font-control-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: FontControlState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtFontControlObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  setFontFamily: (family: string) => void;
  setFontSize: (size: number) => void;
  setFontColor: (hex: string) => void;
  resize: (height: number) => void;
}

export interface FontControlBridge {
  ready(): void;
  setFontFamily(family: string): void;
  setFontSize(size: number): void;
  setFontColor(hex: string): void;
  resize(height: number): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateFontControlState);
}

class MockFontControlBridge implements FontControlBridge {
  private readonly state: FontControlState = structuredClone(initialFontControlState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  setFontFamily(): void {}
  setFontSize(): void {}
  setFontColor(): void {}
  resize(): void {}
  dispose(): void {}
}

export function createFontControlBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): FontControlBridge {
  const fallback = new MockFontControlBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtFontControlObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[font-control bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.fontControlBridge as QtFontControlObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtFontControlObject>(
    method: K,
    ...args: QtFontControlObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    setFontFamily: (family) => call("setFontFamily", family),
    setFontSize: (size) => call("setFontSize", size),
    setFontColor: (hex) => call("setFontColor", hex),
    resize: (height) => call("resize", height),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
