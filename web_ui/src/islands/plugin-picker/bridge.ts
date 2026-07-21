import { PluginPickerState, initialPluginPickerState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validatePluginPickerState } from "../../lib/bridge-core/generated/plugin-picker-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: PluginPickerState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtPluginPickerObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  executePlugin: (pluginName: string) => void;
  resize: (height: number) => void;
  close: () => void;
}

export interface PluginPickerBridge {
  ready(): void;
  executePlugin(pluginName: string): void;
  resize(height: number): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validatePluginPickerState);
}

class MockPluginPickerBridge implements PluginPickerBridge {
  private readonly state: PluginPickerState = structuredClone(initialPluginPickerState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  executePlugin(): void {}
  resize(): void {}
  close(): void {}
  dispose(): void {}
}

export function createPluginPickerBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): PluginPickerBridge {
  const fallback = new MockPluginPickerBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtPluginPickerObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[plugin-picker bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.pluginPickerBridge as QtPluginPickerObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtPluginPickerObject>(
    method: K,
    ...args: QtPluginPickerObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    executePlugin: (pluginName) => call("executePlugin", pluginName),
    resize: (height) => call("resize", height),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
