import { DragSpeedState, initialDragSpeedState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateDragSpeedState } from "../../lib/bridge-core/generated/drag-speed-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: DragSpeedState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtDragSpeedObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  setDragFactor: (factor: number) => void;
  resize: (height: number) => void;
}

export interface DragSpeedBridge {
  ready(): void;
  setDragFactor(factor: number): void;
  resize(height: number): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateDragSpeedState);
}

class MockDragSpeedBridge implements DragSpeedBridge {
  private readonly state: DragSpeedState = structuredClone(initialDragSpeedState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  setDragFactor(): void {}
  resize(): void {}
  dispose(): void {}
}

export function createDragSpeedBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): DragSpeedBridge {
  const fallback = new MockDragSpeedBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtDragSpeedObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[drag-speed bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.dragSpeedBridge as QtDragSpeedObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtDragSpeedObject>(
    method: K,
    ...args: QtDragSpeedObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    setDragFactor: (factor) => call("setDragFactor", factor),
    resize: (height) => call("resize", height),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
