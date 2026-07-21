import { GridControlState, initialGridControlState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateGridControlState } from "../../lib/bridge-core/generated/grid-control-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: GridControlState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtGridControlObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  setGridSize: (size: number) => void;
  setGridOpacityPercent: (percent: number) => void;
  setGridStyle: (style: string) => void;
  setGridColor: (hex: string) => void;
  setSnapToGrid: (enabled: boolean) => void;
  setOrthogonalConnections: (enabled: boolean) => void;
  setSmartGuides: (enabled: boolean) => void;
  setFadeConnections: (enabled: boolean) => void;
  resize: (height: number) => void;
}

export interface GridControlBridge {
  ready(): void;
  setGridSize(size: number): void;
  setGridOpacityPercent(percent: number): void;
  setGridStyle(style: string): void;
  setGridColor(hex: string): void;
  setSnapToGrid(enabled: boolean): void;
  setOrthogonalConnections(enabled: boolean): void;
  setSmartGuides(enabled: boolean): void;
  setFadeConnections(enabled: boolean): void;
  resize(height: number): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateGridControlState);
}

class MockGridControlBridge implements GridControlBridge {
  private readonly state: GridControlState = structuredClone(initialGridControlState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  setGridSize(): void {}
  setGridOpacityPercent(): void {}
  setGridStyle(): void {}
  setGridColor(): void {}
  setSnapToGrid(): void {}
  setOrthogonalConnections(): void {}
  setSmartGuides(): void {}
  setFadeConnections(): void {}
  resize(): void {}
  dispose(): void {}
}

export function createGridControlBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): GridControlBridge {
  const fallback = new MockGridControlBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtGridControlObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[grid-control bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.gridControlBridge as QtGridControlObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtGridControlObject>(
    method: K,
    ...args: QtGridControlObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    setGridSize: (size) => call("setGridSize", size),
    setGridOpacityPercent: (percent) => call("setGridOpacityPercent", percent),
    setGridStyle: (style) => call("setGridStyle", style),
    setGridColor: (hex) => call("setGridColor", hex),
    setSnapToGrid: (enabled) => call("setSnapToGrid", enabled),
    setOrthogonalConnections: (enabled) => call("setOrthogonalConnections", enabled),
    setSmartGuides: (enabled) => call("setSmartGuides", enabled),
    setFadeConnections: (enabled) => call("setFadeConnections", enabled),
    resize: (height) => call("resize", height),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
